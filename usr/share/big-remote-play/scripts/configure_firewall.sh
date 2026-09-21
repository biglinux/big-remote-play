#!/bin/bash
# Explicit, user-authorized firewall setup. Never expose Sunshine's Web/API port.
# Sunshine uses TCP base-5,base,base+21 and UDP base+9..base+11.
# 5353/udp is mDNS; 48011/udp is Big Remote Play's optional search-code lookup.
set -euo pipefail

export TEXTDOMAIN=big-remote-play
export TEXTDOMAINDIR="${TEXTDOMAINDIR:-/usr/share/locale}"
if [ -f /usr/bin/gettext.sh ]; then
    # shellcheck source=/dev/null
    . /usr/bin/gettext.sh
else
    gettext() { printf '%s' "$1"; }
    eval_gettext() { printf '%s' "$1"; }
fi

dry_run=false
if [[ ${1:-} == --dry-run ]]; then dry_run=true; shift; fi
base=${1:-47989}
if [[ $# -gt 1 || ! $base =~ ^[0-9]{1,5}$ ]]; then
    printf '%s\n' "$(gettext 'Usage: configure_firewall.sh [--dry-run] [Sunshine base port]')" >&2
    exit 2
fi
base=$((10#$base))
if ((base < 6 || base > 65514)); then
    printf '%s\n' "$(gettext 'The base port must be between 6 and 65514.')" >&2
    exit 2
fi
tcp_ports=("$((base - 5))" "$base" "$((base + 21))")
udp_ports=("$((base + 9))" "$((base + 10))" "$((base + 11))" 5353 48011)
printf '%s: %s\n' "$(gettext 'TCP ports')" "${tcp_ports[*]}"
printf '%s: %s\n' "$(gettext 'UDP ports')" "${udp_ports[*]}"
if $dry_run; then exit 0; fi

if command -v firewall-cmd &>/dev/null && firewall-cmd --state &>/dev/null; then
    for port in "${tcp_ports[@]}"; do firewall-cmd --permanent --add-port="${port}/tcp"; done
    for port in "${udp_ports[@]}"; do firewall-cmd --permanent --add-port="${port}/udp"; done
    firewall-cmd --reload
elif command -v ufw &>/dev/null; then
    for port in "${tcp_ports[@]}"; do ufw allow "${port}/tcp"; done
    for port in "${udp_ports[@]}"; do ufw allow "${port}/udp"; done
    # Do not enable a firewall that the user deliberately left disabled.
    ufw reload
else
    # Runtime-only fallback. -C makes repeat calls idempotent. Do not change
    # forwarding, router advertisement policy or whether IPv6 is enabled.
    found=false
    for tool in iptables ip6tables; do
        command -v "$tool" &>/dev/null || continue
        found=true
        for port in "${tcp_ports[@]}"; do
            "$tool" -C INPUT -p tcp --dport "$port" -j ACCEPT 2>/dev/null ||
                "$tool" -I INPUT -p tcp --dport "$port" -j ACCEPT
        done
        for port in "${udp_ports[@]}"; do
            "$tool" -C INPUT -p udp --dport "$port" -j ACCEPT 2>/dev/null ||
                "$tool" -I INPUT -p udp --dport "$port" -j ACCEPT
        done
    done
    if ! $found; then printf '%s\n' "$(gettext 'No supported firewall tool was found.')" >&2; exit 1; fi
fi
printf '%s\n' "$(gettext 'Firewall configuration finished.')"
