#!/usr/bin/env bash
# Install the components Big Remote Play needs, with pacman, as root (pkexec).
# Used only where Pamac is not available; with Pamac the application asks
# Pamac directly and this helper does not run.
#
# Arguments: component ids (sunshine, moonlight, tailscale, zerotier), or one
# id per stdin line. No package name comes from the caller: each id maps to a
# package below, resolved to the repository package that provides it
# (BigLinux ships Sunshine as sunshine-bin).
#
# Emits machine-readable markers (see script_protocol.py):
#   BRP_PHASE <fraction>          progress 0..1
#   BRP_DATA INSTALL_RESULT=ok    success sentinel
# Other lines are human prose for the progress log.
# eval_gettext expands variables in deliberately single-quoted translation templates.
# shellcheck disable=SC2016
set -u

export TEXTDOMAIN=big-remote-play
export TEXTDOMAINDIR="${TEXTDOMAINDIR:-/usr/share/locale}"
if [ -f /usr/bin/gettext.sh ]; then
	# shellcheck source=/dev/null
	. /usr/bin/gettext.sh
else
	gettext() { printf '%s' "$1"; }
	eval_gettext() { printf '%s' "$1"; }
fi

ids=("$@")
if [ "${#ids[@]}" -eq 0 ]; then
	while read -r line; do
		line="$(printf '%s' "$line" | tr -d '[:space:]')"
		[ -n "$line" ] && ids+=("$line")
	done
fi
if [ "${#ids[@]}" -eq 0 ]; then
	printf '%s\n' "$(gettext 'Nothing to install.')"
	exit 2
fi

packages=()
units=()
for component in "${ids[@]}"; do
	case "$component" in
	sunshine) packages+=("sunshine") ;;
	moonlight) packages+=("moonlight-qt") ;;
	tailscale | headscale)
		packages+=("tailscale")
		units+=("tailscaled")
		;;
	zerotier)
		packages+=("zerotier-one")
		units+=("zerotier-one")
		;;
	*)
		printf '%s\n' "$(eval_gettext 'Unknown component: ${component}')"
		exit 2
		;;
	esac
done

if ! command -v pacman &>/dev/null; then
	printf '%s\n' "$(gettext 'The pacman package manager is not available on this system.')"
	exit 3
fi

# The repository package that provides each name, never an AUR build.
resolved=()
for package in "${packages[@]}"; do
	provider="$(LC_ALL=C pacman -Sp --print-format '%n' "$package" 2>/dev/null | tail -n 1)"
	resolved+=("${provider:-$package}")
done

echo "BRP_PHASE 0.1"
printf '%s\n' "$(gettext 'Installing…')"
if ! pacman -S --needed --noconfirm "${resolved[@]}"; then
	printf '%s\n' "$(gettext 'The installation did not finish.')"
	exit 1
fi

echo "BRP_PHASE 0.8"
for unit in "${units[@]}"; do
	systemctl enable --now "$unit" || true
done

echo "BRP_PHASE 1.0"
echo "BRP_DATA INSTALL_RESULT=ok"
