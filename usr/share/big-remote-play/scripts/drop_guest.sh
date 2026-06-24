#!/bin/bash
# Disconnects a specific IP by killing TCP sockets
# Usage: drop_guest.sh <IP>

IP="$1"

if [ -z "$IP" ]; then
    echo "Usage: $0 <IP>"
    exit 1
fi

# Validate IP format (IPv4 or IPv6) before passing to root-level ss.
# Defense-in-depth: this script runs via pkexec.
if ! [[ "$IP" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ || "$IP" =~ ^[0-9a-fA-F:]+$ ]]; then
    echo "Error: invalid IP address: $IP"
    exit 1
fi

# Kill TCP connections to this IP
# This requires root privileges (cap_net_admin) which is why this script runs via pkexec
# Kill TCP connections to this IP on specific Sunshine ports
# Using a loop for explicit port targeting to avoid collateral damage (especially on localhost)
PORTS=("47984" "47989" "48010")

for PORT in "${PORTS[@]}"; do
    # We use 'sport' because on the Host, these are the Local ports (Source Port)
    # The 'dst' confirms we are only killing connections TO the specific guest IP.
    ss -K dst "$IP" sport = :"$PORT"
done

# If we want to be extra thorough and kill UDP states (conntrack), we could use conntrack tool
# conntrack -D -d "$IP" 2>/dev/null

exit 0
