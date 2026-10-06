#!/usr/bin/env bash
# Run a Headscale server on this computer behind Caddy (automatic HTTPS).
# Runs as root through pkexec (action br.com.biglinux.remoteplay.headscale-server).
#
# Commands (one per run, arguments validated here; nothing else is accepted):
#   configure HOST        point Headscale at https://HOST and serve it through Caddy
#   firewall              allow HTTP and HTTPS in firewalld or ufw, when one is active
#   create-user NAME      create the Headscale user NAME unless it exists
#   create-apikey         create an API key that expires in 90 days
#   status                report the services and the configured address
#   unconfigure           undo "configure" (restores the saved configuration)
#   hosts-pin HOST        make HOST reach this computer directly (127.0.0.1 in
#                         /etc/hosts) when the router does not send this
#                         computer's own traffic back to it ("hairpin NAT")
#   hosts-unpin HOST      remove that line
#
# Safety:
#   * an existing Headscale configuration is changed only while it still has
#     the package's default address, or the address this helper wrote;
#   * an existing Caddy site file is replaced only when this helper wrote it;
#   * the original configuration is kept once as config.yaml.brp-backup;
#   * Headscale keeps listening on 127.0.0.1 only; Caddy is the only listener
#     on the network (ports 80 and 443).
#
# Emits machine-readable markers (see script_protocol.py):
#   BRP_PHASE <fraction>     progress 0..1
#   BRP_DATA KEY=value       results; RESULT=ok on success
# The API key is printed once as BRP_DATA API_KEY=… for the calling program,
# which stores it in the keyring; it is never written to a file here.
# eval_gettext expands variables in deliberately single-quoted translation templates.
# shellcheck disable=SC2016
set -u
umask 077

export TEXTDOMAIN=big-remote-play
export TEXTDOMAINDIR="${TEXTDOMAINDIR:-/usr/share/locale}"
if [ -f /usr/bin/gettext.sh ]; then
	# shellcheck source=/dev/null
	. /usr/bin/gettext.sh
else
	gettext() { printf '%s' "$1"; }
	eval_gettext() { printf '%s' "$1"; }
fi

CONFIG=/etc/headscale/config.yaml
BACKUP=/etc/headscale/config.yaml.brp-backup
SITE=/etc/caddy/conf.d/big-remote-play-headscale.caddy
MARKER="# Written by Big Remote Play"
DEFAULT_URL="http://127.0.0.1:8080"
BASE_DOMAIN="brp.internal"
# Loopback ports Headscale may use; the first free one is chosen (8080 is
# often taken by another web application or a container).
LISTEN_PORTS="8080 18080 18081 18082 18083 18084 18085 18086 18087 18088 18089"
METRICS_PORTS="9090 19090 19091 19092 19093 19094 19095"
GRPC_PORTS="50443 50444 50445 50446 50447 50448 50449"
CADDY_MARK=/etc/headscale/.brp-enabled-caddy
# Present when headscale.service was already enabled before setup: undoing the
# setup then leaves it enabled.
HEADSCALE_KEEP=/etc/headscale/.brp-headscale-was-enabled
HOSTS=/etc/hosts
HOSTS_MARK="# big-remote-play headscale"
LOCAL_LISTEN=""

say() { printf '%s\n' "$1"; }
finish() {
	echo "BRP_DATA RESULT=$1"
	exit "${2:-0}"
}

valid_host() {
	local host="$1" octet
	if [[ "$host" =~ ^([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})$ ]]; then
		for octet in "${BASH_REMATCH[@]:1}"; do
			((10#$octet <= 255)) || return 1
		done
		return 0
	fi
	[ "${#host}" -le 253 ] || return 1
	[[ "$host" =~ ^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]([a-z0-9-]{0,61}[a-z0-9])?$ ]]
}

config_value() {
	# The value of a top-level key, without quotes.
	awk -v key="$1" '$1 == key":" { v = $2; gsub(/"|'\''/, "", v); print v; exit }' "$CONFIG"
}

require_programs() {
	if [ ! -x /usr/bin/headscale ] || [ ! -x /usr/bin/caddy ]; then
		say "$(gettext 'Headscale and Caddy must be installed first.')"
		finish not_installed 3
	fi
	if [ ! -f "$CONFIG" ]; then
		say "$(gettext 'The Headscale configuration file is missing.')"
		finish no_config 3
	fi
}

# PIDs listening on a TCP port (any address).
port_pids() {
	ss -ltnpH "( sport = :$1 )" 2>/dev/null | grep -o 'pid=[0-9]*' | cut -d= -f2 | sort -u
}

# Names of the programs listening on a TCP port, for the message.
port_names() {
	ss -ltnpH "( sport = :$1 )" 2>/dev/null | grep -o 'users:(("[^"]*"' | cut -d'"' -f2 | sort -u | paste -sd, -
}

# True when only the given systemd unit (or nothing) listens on the port.
port_free_for() {
	local port="$1" unit="$2" main pid pids
	[ -n "$(ss -ltnH "( sport = :$port )" 2>/dev/null)" ] || return 0
	pids="$(port_pids "$port")"
	# Something listens and its owner cannot be told: not free.
	[ -n "$pids" ] || return 1
	main="$(systemctl show -p MainPID --value "$unit" 2>/dev/null)"
	for pid in $pids; do
		[ "$pid" = "$main" ] && [ "$main" != 0 ] && continue
		# A child of the unit (Caddy may fork) counts as the unit.
		[ "$main" != 0 ] && [ "$(ps -o ppid= -p "$pid" 2>/dev/null | tr -d ' ')" = "$main" ] && continue
		return 1
	done
	return 0
}

choose_port() {
	local port
	for port in $1; do
		if port_free_for "$port" headscale.service; then
			printf '%s' "$port"
			return 0
		fi
	done
	return 1
}

# The current loopback port of a listen key, when it is one of ours.
our_port() {
	local value="$1" list="$2" port
	port="${value##*:}"
	[ "${value%:*}" = "127.0.0.1" ] || return 1
	case " $list " in *" $port "*) printf '%s' "$port" ;; *) return 1 ;; esac
}

wait_health() {
	local _
	for _ in $(seq 1 30); do
		if curl -fsS --max-time 2 "http://${LOCAL_LISTEN}/health" >/dev/null 2>&1; then
			return 0
		fi
		sleep 1
	done
	return 1
}

cmd_configure() {
	local host="$1" current listen base tmp
	valid_host "$host" || {
		say "$(gettext 'This server address is not valid.')"
		finish invalid 2
	}
	require_programs
	echo "BRP_PHASE 0.1"
	current="$(config_value server_url)"
	if [ "$current" != "$DEFAULT_URL" ] && [ "$current" != "https://${host}" ]; then
		say "$(eval_gettext 'Headscale on this computer is already set up for ${current}. Nothing was changed.')"
		echo "BRP_DATA CURRENT_URL=${current}"
		finish other_server 4
	fi
	listen="$(config_value listen_addr)"
	if ! our_port "$listen" "$LISTEN_PORTS" >/dev/null; then
		say "$(gettext 'Headscale on this computer uses a custom listen address. Nothing was changed.')"
		finish custom_listen 4
	fi
	if [ -e "$SITE" ] && ! grep -qF "$MARKER" "$SITE"; then
		say "$(gettext 'A Caddy site file with the same name already exists. Nothing was changed.')"
		finish caddy_conflict 4
	fi
	# HTTPS needs ports 80 and 443. Another web server or a container there
	# cannot share them with Caddy: refuse before changing anything.
	local busy="" names="" port
	for port in 80 443; do
		if ! port_free_for "$port" caddy.service; then
			busy="${busy:+$busy,}$port"
			names="${names:+$names,}$(port_names "$port")"
		fi
	done
	if [ -n "$busy" ]; then
		say "$(gettext 'Ports 80 and 443 are used by another program. Nothing was changed.')"
		echo "BRP_DATA BUSY_PORTS=${busy}"
		echo "BRP_DATA BUSY_BY=${names}"
		finish ports_busy 4
	fi
	local listen_port metrics_port grpc_port metrics grpc
	listen_port="$(choose_port "$LISTEN_PORTS")" || {
		say "$(gettext 'No free local port was found for Headscale. Nothing was changed.')"
		finish ports_busy 4
	}
	LOCAL_LISTEN="127.0.0.1:${listen_port}"
	metrics="$(config_value metrics_listen_addr)"
	grpc="$(config_value grpc_listen_addr)"
	# The metrics and gRPC listeners move too, but only from their default
	# or a port this helper chose: a setting someone else made stays.
	metrics_port=""
	grpc_port=""
	if our_port "$metrics" "$METRICS_PORTS" >/dev/null; then
		metrics_port="$(choose_port "$METRICS_PORTS")" || metrics_port=""
	fi
	if our_port "$grpc" "$GRPC_PORTS" >/dev/null; then
		grpc_port="$(choose_port "$GRPC_PORTS")" || grpc_port=""
	fi

	say "$(gettext 'Configuring…')"
	# The first setup keeps the original; only then is the state before Big
	# Remote Play known.
	first_setup=0
	if [ ! -e "$BACKUP" ]; then
		cp -p "$CONFIG" "$BACKUP" || finish error 1
		first_setup=1
	fi
	# This run's starting point, put back when the HTTPS part is refused.
	before="$(mktemp /etc/headscale/.config-before.XXXXXX)" || finish error 1
	cp -p "$CONFIG" "$before" || {
		rm -f "$before"
		finish error 1
	}
	base="$(awk '$1 == "base_domain:" { v = $2; gsub(/"|'\''/, "", v); print v; exit }' "$CONFIG")"
	tmp="$(mktemp /etc/headscale/.config.XXXXXX)" || finish error 1
	# The MagicDNS domain must not contain the server's own name.
	awk -v url="https://${host}" -v host="$host" -v base="$base" -v newbase="$BASE_DOMAIN" \
		-v listen="$LOCAL_LISTEN" -v metrics="${metrics_port:+127.0.0.1:$metrics_port}" -v grpc="${grpc_port:+127.0.0.1:$grpc_port}" '
		/^server_url:/ { print "server_url: " url; next }
		/^listen_addr:/ { print "listen_addr: " listen; next }
		/^metrics_listen_addr:/ && metrics != "" { print "metrics_listen_addr: " metrics; next }
		/^grpc_listen_addr:/ && grpc != "" { print "grpc_listen_addr: " grpc; next }
		/^[[:space:]]+base_domain:/ && (base == "example.com" || base == host || (length(host) > length(base) && substr(host, length(host) - length(base)) == "." base)) {
			sub(/base_domain:.*/, "base_domain: " newbase); print; next
		}
		{ print }
	' "$CONFIG" >"$tmp" || {
		rm -f "$tmp"
		finish error 1
	}
	if ! { chmod --reference="$CONFIG" "$tmp" && chown --reference="$CONFIG" "$tmp" && mv -f "$tmp" "$CONFIG"; }; then
		rm -f "$tmp"
		finish error 1
	fi

	mkdir -p /etc/caddy/conf.d
	tmp="$(mktemp /etc/caddy/conf.d/.brp.XXXXXX)" || finish error 1
	{
		printf '%s: Headscale behind HTTPS. Remove this file to undo.\n' "$MARKER"
		printf '%s {\n\treverse_proxy %s\n}\n' "$host" "$LOCAL_LISTEN"
	} >"$tmp"
	chmod 0644 "$tmp"
	mv -f "$tmp" "$SITE"
	if ! caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null 2>&1; then
		rm -f "$SITE"
		mv -f "$before" "$CONFIG"
		say "$(gettext 'The HTTPS configuration was not accepted. Nothing was enabled.')"
		finish caddy_invalid 1
	fi
	rm -f "$before"

	echo "BRP_PHASE 0.5"
	say "$(gettext 'Starting…')"
	# Remember whether Caddy was off before, so "unconfigure" can turn it off again.
	if ! systemctl is-enabled --quiet caddy.service 2>/dev/null; then
		touch "$CADDY_MARK"
	fi
	if [ "$first_setup" = 1 ] && systemctl is-enabled --quiet headscale.service 2>/dev/null; then
		touch "$HEADSCALE_KEEP"
	fi
	systemctl enable headscale.service caddy.service >/dev/null 2>&1
	systemctl restart headscale.service
	systemctl reload-or-restart caddy.service
	echo "BRP_PHASE 0.7"
	say "$(gettext 'Checking…')"
	if ! wait_health; then
		say "$(gettext 'Headscale did not start. See its log under Advanced.')"
		finish not_running 1
	fi
	echo "BRP_DATA LISTEN=${LOCAL_LISTEN}"
	echo "BRP_PHASE 1.0"
	finish ok
}

cmd_firewall() {
	if systemctl is-active --quiet firewalld; then
		if ! { firewall-cmd --quiet --permanent --add-service=http --add-service=https && firewall-cmd --quiet --add-service=http --add-service=https; }; then
			finish error 1
		fi
		echo "BRP_DATA FIREWALL=firewalld"
		finish ok
	fi
	if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q '^Status: active'; then
		if ! { ufw allow 80/tcp >/dev/null && ufw allow 443/tcp >/dev/null; }; then
			finish error 1
		fi
		echo "BRP_DATA FIREWALL=ufw"
		finish ok
	fi
	echo "BRP_DATA FIREWALL=none"
	finish ok
}

cmd_create_user() {
	local name="$1" id
	[[ "$name" =~ ^[a-z0-9][a-z0-9._-]{0,62}$ ]] || {
		say "$(gettext 'This name is not valid.')"
		finish invalid 2
	}
	command -v headscale >/dev/null 2>&1 || finish not_installed 3
	id="$(headscale users list -o json 2>/dev/null | jq -r --arg n "$name" '.[]? | select(.name == $n) | .id' | head -n 1)"
	if [ -z "$id" ]; then
		headscale users create "$name" >/dev/null 2>&1 || finish error 1
		id="$(headscale users list -o json 2>/dev/null | jq -r --arg n "$name" '.[]? | select(.name == $n) | .id' | head -n 1)"
	fi
	[ -n "$id" ] || finish error 1
	echo "BRP_DATA USER_ID=${id}"
	echo "BRP_DATA USER_NAME=${name}"
	finish ok
}

cmd_create_apikey() {
	local key
	command -v headscale >/dev/null 2>&1 || finish not_installed 3
	key="$(headscale apikeys create --expiration 90d 2>/dev/null | tail -n 1 | tr -d '[:space:]')"
	[ -n "$key" ] || finish error 1
	echo "BRP_DATA API_KEY=${key}"
	finish ok
}

cmd_status() {
	local value
	for unit in headscale caddy; do
		if systemctl is-active --quiet "$unit"; then value=active; else value=inactive; fi
		echo "BRP_DATA ${unit^^}=${value}"
	done
	[ -f "$CONFIG" ] && echo "BRP_DATA SERVER_URL=$(config_value server_url)"
	[ -f "$CONFIG" ] && echo "BRP_DATA LISTEN=$(config_value listen_addr)"
	[ -e "$SITE" ] && grep -qF "$MARKER" "$SITE" && echo "BRP_DATA OURS=yes"
	finish ok
}

valid_domain() {
	[ "${#1}" -le 253 ] && [[ "$1" =~ ^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]([a-z0-9-]{0,61}[a-z0-9])?$ ]]
}

# Rewrite /etc/hosts atomically, keeping its owner and mode. A symlinked
# /etc/hosts is written through to its target, never replaced by a file.
write_hosts() {
	local tmp target
	target="$(readlink -f -- "$HOSTS")" || finish error 1
	tmp="$(mktemp "${target%/*}/.hosts.XXXXXX")" || finish error 1
	cat >"$tmp" || {
		rm -f "$tmp"
		finish error 1
	}
	if ! { chmod --reference="$target" "$tmp" && chown --reference="$target" "$tmp" && mv -f "$tmp" "$target"; }; then
		rm -f "$tmp"
		finish error 1
	fi
}

cmd_hosts_pin() {
	local host="$1"
	valid_domain "$host" || {
		say "$(gettext 'This server address is not valid.')"
		finish invalid 2
	}
	if grep -qF "127.0.0.1 ${host} ${HOSTS_MARK}" "$HOSTS"; then
		finish ok
	fi
	# A line someone else wrote for this name stays as it is.
	if awk -v h="$host" '!/^[[:space:]]*#/ { for (i = 2; i <= NF; i++) { if ($i ~ /^#/) break; if ($i == h) found = 1 } } END { exit !found }' "$HOSTS"; then
		say "$(gettext 'This name is already set in /etc/hosts. Nothing was changed.')"
		finish hosts_conflict 4
	fi
	{
		cat "$HOSTS"
		printf '127.0.0.1 %s %s\n' "$host" "$HOSTS_MARK"
	} | write_hosts
	finish ok
}

cmd_hosts_unpin() {
	local host="$1"
	valid_domain "$host" || finish invalid 2
	grep -qF "127.0.0.1 ${host} ${HOSTS_MARK}" "$HOSTS" || finish ok
	grep -vF "127.0.0.1 ${host} ${HOSTS_MARK}" "$HOSTS" | write_hosts
	finish ok
}

cmd_unconfigure() {
	# Only what this helper set up is undone.
	if ! { [ -e "$SITE" ] && grep -qF "$MARKER" "$SITE"; } && [ ! -f "$BACKUP" ]; then
		finish nothing
	fi
	if [ -e "$SITE" ] && grep -qF "$MARKER" "$SITE"; then
		rm -f "$SITE"
		systemctl reload-or-restart caddy.service >/dev/null 2>&1 || true
	fi
	if [ -f "$BACKUP" ]; then
		# Changes made after setup are kept beside it, then the original goes back.
		cp -p "$CONFIG" "$CONFIG.brp-before-undo" 2>/dev/null
		tmp="$(mktemp /etc/headscale/.config.XXXXXX)" || finish error 1
		if cp -p "$BACKUP" "$tmp" && mv -f "$tmp" "$CONFIG"; then
			rm -f "$BACKUP"
		else
			rm -f "$tmp"
			finish error 1
		fi
	fi
	if [ -e "$HEADSCALE_KEEP" ]; then
		rm -f "$HEADSCALE_KEEP"
		systemctl restart headscale.service >/dev/null 2>&1 || true
	else
		systemctl disable --now headscale.service >/dev/null 2>&1 || true
	fi
	if [ -f "$CADDY_MARK" ]; then
		systemctl disable --now caddy.service >/dev/null 2>&1 || true
		rm -f "$CADDY_MARK"
	fi
	finish ok
}

command="${1:-}"
case "$command" in
configure) [ $# -eq 2 ] || finish invalid 2; cmd_configure "$2" ;;
firewall) [ $# -eq 1 ] || finish invalid 2; cmd_firewall ;;
create-user) [ $# -eq 2 ] || finish invalid 2; cmd_create_user "$2" ;;
create-apikey) [ $# -eq 1 ] || finish invalid 2; cmd_create_apikey ;;
status) [ $# -eq 1 ] || finish invalid 2; cmd_status ;;
unconfigure) [ $# -eq 1 ] || finish invalid 2; cmd_unconfigure ;;
hosts-pin) [ $# -eq 2 ] || finish invalid 2; cmd_hosts_pin "$2" ;;
hosts-unpin) [ $# -eq 2 ] || finish invalid 2; cmd_hosts_unpin "$2" ;;
*) finish invalid 2 ;;
esac
