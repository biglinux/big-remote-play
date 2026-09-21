#!/usr/bin/env bash
# Install a VPN provider's package via pacman and enable its service.
# Invoked with elevated privilege (pkexec). Argument: provider id.
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

# Provider id comes as argv[1], or (when driven via the app's stdin runner) as
# the first stdin line.
provider="${1:-}"
if [ -z "$provider" ]; then
	read -r provider || true
	provider="$(printf '%s' "$provider" | tr -d '[:space:]')"
fi

case "$provider" in
tailscale)
	pkg="tailscale"
	unit="tailscaled"
	;;
zerotier)
	pkg="zerotier-one"
	unit="zerotier-one"
	;;
headscale)
	# Headscale runs in containers; the local dependency is Docker.
	pkg="docker"
	unit="docker"
	;;
*)
	printf '%s\n' "$(eval_gettext 'Unknown VPN service: ${provider}')"
	exit 2
	;;
esac

if ! command -v pacman &>/dev/null; then
	printf '%s\n' "$(gettext 'The pacman package manager is not available on this system.')"
	exit 3
fi

echo "BRP_PHASE 0.1"
printf '%s\n' "$(eval_gettext 'Installing ${pkg}…')"
if ! pacman -S --needed --noconfirm "$pkg"; then
	printf '%s\n' "$(eval_gettext 'Could not install ${pkg}.')"
	exit 1
fi

echo "BRP_PHASE 0.7"
printf '%s\n' "$(eval_gettext 'Starting ${unit}…')"
systemctl enable --now "$unit" || true

echo "BRP_PHASE 1.0"
echo "BRP_DATA INSTALL_RESULT=ok"
printf '%s\n' "$(gettext 'Installation finished.')"
