#!/bin/bash
# Disconnects a specific IP by killing TCP sockets
# Usage: drop_guest.sh <IP>

IP="$1"

if [ -z "$IP" ]; then
    echo "Usage: $0 <IP>"
    exit 1
fi

# Kill TCP connections to this IP
# This requires root privileges (cap_net_admin) which is why this script runs via pkexec
ss -K dst "$IP"

# If we want to be extra thorough and kill UDP states (conntrack), we could use conntrack tool
# conntrack -D -d "$IP" 2>/dev/null

exit 0
