#!/bin/bash
# Description: Configures the firewall to allow Sunshine/BigRemotePlay traffic
# Supports: firewalld, ufw, iptables

set -euo pipefail

echo "Starting firewall configuration for BigRemotePlay..."

# Sunshine Ports:
# TCP streaming/control ports. Do not open 47990/tcp: Sunshine Web UI stays local.
# UDP Range: 47998-48020 (Include potential dynamic ports)
TCP_PORTS=(47984 47989 48010)
UDP_START="47998"
UDP_END="48020"

if command -v firewall-cmd &>/dev/null; then
	echo "Detected: firewalld"
	for port in "${TCP_PORTS[@]}"; do
		firewall-cmd --permanent --add-port="${port}/tcp"
	done
	firewall-cmd --permanent --add-port="${UDP_START}-${UDP_END}/udp"
	firewall-cmd --permanent --add-port=1900/udp
	firewall-cmd --permanent --add-port=5353/udp
	firewall-cmd --reload
	echo "Firewalld configured."
elif command -v ufw &>/dev/null; then
	echo "Detected: ufw"
	for port in "${TCP_PORTS[@]}"; do
		ufw allow "${port}/tcp"
	done
	ufw allow "${UDP_START}:${UDP_END}/udp"
	ufw allow 1900/udp
	ufw allow 5353/udp
	ufw reload
	echo "UFW configured."
else
	echo "Fallback: Using iptables/ip6tables directly..."
	# TCP
	for port in "${TCP_PORTS[@]}"; do
		iptables -I INPUT -p tcp --dport "$port" -j ACCEPT
		ip6tables -I INPUT -p tcp --dport "$port" -j ACCEPT
	done
	# UDP
	iptables -I INPUT -p udp --dport "${UDP_START}:${UDP_END}" -j ACCEPT
	iptables -I INPUT -p udp --dport 1900 -j ACCEPT
	ip6tables -I INPUT -p udp --dport "${UDP_START}:${UDP_END}" -j ACCEPT
	ip6tables -I INPUT -p udp --dport 1900 -j ACCEPT
	# mDNS
	iptables -I INPUT -p udp --dport 5353 -j ACCEPT
	ip6tables -I INPUT -p udp --dport 5353 -j ACCEPT
	echo "Iptables rules applied."
fi

# Enable IPv6 Forwarding and disable ICMPv6 blocks (essential for discovery)
sysctl -w net.ipv6.conf.all.disable_ipv6=0
sysctl -w net.ipv6.conf.all.accept_ra=2
echo "Configuration finished successfully."
