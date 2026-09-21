#!/bin/bash
# shellcheck disable=SC1091,SC2005
# gettext fallback intentionally emits no newline, so translated menu strings are
# wrapped in echo throughout this legacy interactive script.
# ==============================================================================
# ZEROTIER NETWORK MANAGER - Big Remote Play
# Developed for BigLinux/Manjaro
# ==============================================================================

set -e
umask 077

green='\033[0;32m'
blue='\033[0;34m'
yellow='\033[1;33m'
red='\033[0;31m'
cyan='\033[0;36m'
nc='\033[0m'

# Machine-readable markers for the GUI (locale-independent ASCII). The GUI parses
# these; all other output is human prose, translated via gettext.
brp_data() { printf 'BRP_DATA %s=%s\n' "$1" "$2"; }
brp_phase() { printf 'BRP_PHASE %s\n' "$1"; }

# gettext for human-facing prose (msgids are English; catalogs translate them).
export TEXTDOMAIN=big-remote-play
export TEXTDOMAINDIR="${TEXTDOMAINDIR:-/usr/share/locale}"
if [ -f /usr/bin/gettext.sh ]; then
	. /usr/bin/gettext.sh
else
	gettext() { printf '%s' "$1"; }
	eval_gettext() { printf '%s' "$1"; }
fi

zerotier_config="${XDG_CONFIG_HOME:-$HOME/.config}/big-remote-play/zerotier"
networks_file="$zerotier_config/networks.txt"

header() {
	clear
	echo -e "${blue}====================================================${nc}"
	echo -e "${green}         $(gettext 'ZEROTIER - VIRTUAL PRIVATE NETWORK')            ${nc}"
	echo -e "${blue}====================================================${nc}"
}

check_deps() {
	brp_phase 0.1
	echo -e "${yellow}$(gettext 'Checking dependencies...')${nc}"

	if ! command -v zerotier-cli &>/dev/null; then
		echo -e "${yellow}$(gettext 'Installing ZeroTier...')${nc}"
		sudo pacman -S zerotier-one --noconfirm
	else
		echo -e "${green}✓ $(gettext 'ZeroTier already installed')${nc}"
	fi

	if ! command -v jq &>/dev/null; then
		sudo pacman -S jq --noconfirm
	fi
	if ! command -v curl &>/dev/null; then
		sudo pacman -S curl --noconfirm
	fi

	if ! systemctl is-active --quiet zerotier-one 2>/dev/null; then
		echo -e "${yellow}$(gettext 'Starting ZeroTier service...')${nc}"
		sudo systemctl enable zerotier-one
		sudo systemctl start zerotier-one
		sleep 3
	else
		echo -e "${green}✓ $(gettext 'ZeroTier daemon active')${nc}"
	fi

	mkdir -p "$zerotier_config"
	chmod 700 "$zerotier_config" 2>/dev/null || true
}

load_token() {
	echo -e "${cyan}$(gettext 'Paste your API Token here: ')${nc}"
	read -r API_TOKEN
	if [ -z "$API_TOKEN" ]; then
		echo -e "${red}$(gettext 'Token cannot be empty!')${nc}"
		exit 1
	fi
}

create_network() {
	header
	echo -e "${yellow}$(gettext 'CREATE NEW ZEROTIER NETWORK')${nc}"
	echo ""

	load_token

	read -r -p "$(gettext 'Network name: ')" NETWORK_NAME
	if [ -z "$NETWORK_NAME" ]; then
		echo -e "${red}$(gettext 'Name cannot be empty!')${nc}"
		exit 1
	fi

	read -r -p "$(gettext 'Description (optional): ')" NETWORK_DESC

	brp_phase 0.3
	echo -e "${yellow}$(gettext 'Creating network via API...')${nc}"
	response=$(curl -s -X POST \
		-H "Authorization: token $API_TOKEN" \
		-H "Content-Type: application/json" \
		-d "{\"name\": \"$NETWORK_NAME\", \"description\": \"$NETWORK_DESC\", \"private\": true}" \
		"https://api.zerotier.com/api/v1/network")

	network_id=$(echo "$response" | jq -r '.id // empty')

	if [ -z "$network_id" ] || [ "$network_id" = "null" ]; then
		echo -e "${red}$(gettext 'Error creating network. Check your token.')${nc}"
		echo "$response" | jq '.' 2>/dev/null || echo "$response"
		exit 1
	fi

	brp_data network_id "$network_id"
	brp_phase 0.6
	echo -e "${green}✅ $(gettext 'Network created! ID:') $network_id${nc}"

	# Join the network automatically
	echo -e "${yellow}$(gettext 'Joining the network...')${nc}"
	sudo zerotier-cli join "$network_id" 2>/dev/null || true
	sleep 3

	# Get Node ID and authorize automatically
	node_id=$(sudo zerotier-cli info 2>/dev/null | cut -d' ' -f3 || echo "")

	if [ -n "$node_id" ]; then
		echo -e "${yellow}$(gettext 'Authorizing local device...')${nc}"
		curl -s -X POST \
			-H "Authorization: token $API_TOKEN" \
			-H "Content-Type: application/json" \
			-d '{"config": {"authorized": true}}' \
			"https://api.zerotier.com/api/v1/network/$network_id/member/$node_id" >/dev/null
		echo -e "${green}✓ $(gettext 'Device authorized!')${nc}"
	fi

	# Save locally
	printf '%s:%s:%s\n' "$network_id" "$NETWORK_NAME" "$(date +%Y-%m-%d)" >>"$networks_file"
	chmod 600 "$networks_file" 2>/dev/null || true

	ip_local=$(ip route get 1 2>/dev/null | awk '{print $7;exit}' || echo "N/A")
	public_ip=$(curl -s https://api.ipify.org 2>/dev/null || echo "N/A")

	# Machine-readable data for the GUI (raw, locale-independent).
	brp_data web_ui "https://my.zerotier.com/network/$network_id"
	brp_data api_url "https://api.zerotier.com/api/v1"
	brp_data public_ip "$public_ip"
	brp_data local_ip "$ip_local"
	brp_data auth_key "$network_id"
	brp_data network_id "$network_id"

	echo ""
	echo -e "${cyan}=== $(gettext 'NETWORK INFORMATION') ===${nc}"
	echo -e "$(gettext 'Web Interface:') ${yellow}https://my.zerotier.com/network/$network_id${nc}"
	echo -e "$(gettext 'API URL:') ${cyan}https://api.zerotier.com/api/v1${nc}"
	echo -e "$(gettext 'Your Public IP:') ${green}$public_ip${nc}"
	echo -e "$(gettext 'Server Local IP:') ${green}$ip_local${nc}"
	echo ""
	echo -e "${cyan}=== $(gettext 'CREDENTIALS') ===${nc}"
	echo -e "$(gettext 'Key for Friends:') ${green}$network_id${nc}"
	brp_phase 0.95
	echo ""
	echo -e "${yellow}⚠️  $(gettext 'Share the network ID with your friends:') $network_id${nc}"
	echo -e "${yellow}$(gettext 'They use the Connect option and enter the network ID')${nc}"
	echo -e "${blue}====================================================${nc}"
}

join_network() {
	header
	echo -e "${yellow}$(gettext 'JOIN ZEROTIER NETWORK (Client/Guest)')${nc}"
	echo ""

	check_deps

	read -r -p "$(gettext 'ZeroTier Network ID (16 characters): ')" network_id
	if [ -z "$network_id" ]; then
		echo -e "${red}$(gettext 'Network ID cannot be empty!')${nc}"
		exit 1
	fi

	echo -e "${yellow}$(gettext 'Joining network:') $network_id${nc}"
	sudo zerotier-cli join "$network_id"
	sleep 5

	# Check status
	echo ""
	echo -e "${cyan}=== $(gettext 'CONNECTION STATUS') ===${nc}"
	sudo zerotier-cli listnetworks 2>/dev/null || true

	node_id=$(sudo zerotier-cli info 2>/dev/null | cut -d' ' -f3 || echo "N/A")
	echo ""
	echo -e "$(gettext 'Your Node ID: ')${green}$node_id${nc}"
	echo -e "${yellow}⚠️  $(gettext 'You must be authorized by the network administrator!')${nc}"
	echo -e "${cyan}$(gettext 'Give your Node ID to the administrator: ')${green}$node_id${nc}"

	echo -e "${blue}====================================================${nc}"
	echo -e "${green}$(gettext 'Join request sent!')${nc}"
	echo -e "${yellow}$(gettext 'Wait for the administrator to authorize you')${nc}"
}

# --- MENU PRINCIPAL ---
main_menu() {
	header
	check_deps
	echo "$(gettext 'Select an option:')"
	echo "$(gettext '1) Be the HOST (create and manage the network)')"
	echo "$(gettext '2) Be the GUEST (join a friend network)')"
	echo "$(gettext '3) View network status')"
	echo "$(gettext '4) Exit')"
	read -r -p "$(gettext 'Option: ')" OPT

	case $OPT in
	1) create_network ;;
	2) join_network ;;
	3)
		echo -e "${cyan}=== $(gettext 'ZEROTIER STATUS') ===${nc}"
		sudo zerotier-cli info 2>/dev/null || echo "$(gettext 'ZeroTier not connected')"
		sudo zerotier-cli listnetworks 2>/dev/null || true
		;;
	*) exit 0 ;;
	esac
}

main_menu
