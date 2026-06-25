#!/usr/bin/env bash
# Install a VPN provider's package via pacman and enable its service.
# Invoked with elevated privilege (bigsudo). Argument: provider id.
#
# Emits machine-readable markers (see script_protocol.py):
#   BRP_PHASE <fraction>          progress 0..1
#   BRP_DATA INSTALL_RESULT=ok    success sentinel
# Other lines are human prose for the progress log.
set -u

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
	echo "Unknown provider: ${provider}"
	exit 2
	;;
esac

if ! command -v pacman &>/dev/null; then
	echo "pacman not available on this system"
	exit 3
fi

echo "BRP_PHASE 0.1"
echo "Installing ${pkg}..."
if ! pacman -S --needed --noconfirm "$pkg"; then
	echo "Failed to install ${pkg}"
	exit 1
fi

echo "BRP_PHASE 0.7"
echo "Enabling ${unit}..."
systemctl enable --now "$unit" || true

echo "BRP_PHASE 1.0"
echo "BRP_DATA INSTALL_RESULT=ok"
echo "Done."
