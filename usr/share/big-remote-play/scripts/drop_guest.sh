#!/usr/bin/env bash
# Disconnect a specific guest by closing Sunshine TCP sockets.
# Usage: drop_guest.sh <ip-address>
# eval_gettext expands the variables in these deliberately single-quoted templates.
# shellcheck disable=SC2034,SC2016
set -u

export TEXTDOMAIN=big-remote-play
export TEXTDOMAINDIR="${TEXTDOMAINDIR:-/usr/share/locale}"
if [ -f /usr/bin/gettext.sh ]; then
	# shellcheck source=/dev/null
	. /usr/bin/gettext.sh
else
	# Invoked indirectly through command substitutions below.
	# shellcheck disable=SC2317
	gettext() { printf '%s' "$1"; }
	eval_gettext() { printf '%s' "$1"; }
fi

if [[ $# -ne 1 || -z ${1:-} ]]; then
	script_name=$0; printf '%s\n' "$(eval_gettext 'Usage: ${script_name} <IP address>')"
	exit 1
fi

ip=$1

# This script runs through pkexec. Parse the input as an IP address before it is
# passed to the privileged `ss` command; a permissive regex would accept values
# such as 999.999.999.999 or malformed IPv6 strings.
if ! /usr/bin/python3 - "$ip" <<'PY'; then
import ipaddress
import sys

try:
    ipaddress.ip_address(sys.argv[1])
except ValueError:
    raise SystemExit(1)
PY
	printf '%s\n' "$(eval_gettext 'Invalid IP address: ${ip}')"
	exit 1
fi

# Sunshine listens on these host-side TCP ports. Restrict each deletion to the
# selected destination to avoid terminating unrelated connections.
ports=(47984 47989 48010)
for port in "${ports[@]}"; do
	/usr/bin/ss -K dst "$ip" sport = :"$port"
done

exit 0
