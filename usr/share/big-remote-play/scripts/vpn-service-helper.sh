#!/bin/sh
# Starts or stops one private-network daemon for Big Remote Play, through
# PolicyKit (pkexec). It accepts exactly two words: start or stop, and
# zerotier-one or tailscaled; anything else is refused. It changes nothing
# but that service's running state (start also enables it at boot, as the
# app always did). See docs/zerotier-permissions.md.
set -eu
[ "$#" -eq 2 ] || exit 2
case "$2" in
	zerotier-one | tailscaled) ;;
	*) exit 2 ;;
esac
case "$1" in
	start) exec /usr/bin/systemctl enable --now "$2.service" ;;
	stop) exec /usr/bin/systemctl stop "$2.service" ;;
	*) exit 2 ;;
esac
