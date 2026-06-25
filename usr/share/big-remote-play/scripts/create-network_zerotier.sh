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

GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m'

# Machine-readable markers for the GUI (locale-independent ASCII). The GUI parses
# these; all other output is human prose, translated via gettext.
brp_data() { printf 'BRP_DATA %s=%s\n' "$1" "$2"; }
brp_phase() { printf 'BRP_PHASE %s\n' "$1"; }

# gettext for human-facing prose (msgids are English; catalogs translate them).
export TEXTDOMAIN=big-remote-play
if [ -f /usr/bin/gettext.sh ]; then
	. /usr/bin/gettext.sh
else
	gettext() { printf '%s' "$1"; }
	eval_gettext() { printf '%s' "$1"; }
fi

ZEROTIER_CONFIG="${XDG_CONFIG_HOME:-$HOME/.config}/big-remote-play/zerotier"
NETWORKS_FILE="$ZEROTIER_CONFIG/networks.txt"

header() {
	clear
	echo -e "${BLUE}====================================================${NC}"
	echo -e "${GREEN}         $(gettext 'ZEROTIER - VIRTUAL PRIVATE NETWORK')            ${NC}"
	echo -e "${BLUE}====================================================${NC}"
}

check_deps() {
	brp_phase 0.1
	echo -e "${YELLOW}$(gettext 'Checking dependencies...')${NC}"

	if ! command -v zerotier-cli &>/dev/null; then
		echo -e "${YELLOW}$(gettext 'Installing ZeroTier...')${NC}"
		sudo pacman -S zerotier-one --noconfirm
	else
		echo -e "${GREEN}✓ $(gettext 'ZeroTier already installed')${NC}"
	fi

	if ! command -v jq &>/dev/null; then
		sudo pacman -S jq --noconfirm
	fi
	if ! command -v curl &>/dev/null; then
		sudo pacman -S curl --noconfirm
	fi

	if ! systemctl is-active --quiet zerotier-one 2>/dev/null; then
		echo -e "${YELLOW}$(gettext 'Starting ZeroTier service...')${NC}"
		sudo systemctl enable zerotier-one
		sudo systemctl start zerotier-one
		sleep 3
	else
		echo -e "${GREEN}✓ $(gettext 'ZeroTier daemon active')${NC}"
	fi

	mkdir -p "$ZEROTIER_CONFIG"
	chmod 700 "$ZEROTIER_CONFIG" 2>/dev/null || true
}

load_token() {
	echo -e "${CYAN}$(gettext 'Paste your API Token here: ')${NC}"
	read -r API_TOKEN
	if [ -z "$API_TOKEN" ]; then
		echo -e "${RED}$(gettext 'Token cannot be empty!')${NC}"
		exit 1
	fi
}

create_network() {
	header
	echo -e "${YELLOW}$(gettext 'CREATE NEW ZEROTIER NETWORK')${NC}"
	echo ""

	load_token

	read -r -p "$(gettext 'Network name: ')" NETWORK_NAME
	if [ -z "$NETWORK_NAME" ]; then
		echo -e "${RED}$(gettext 'Name cannot be empty!')${NC}"
		exit 1
	fi

	read -r -p "$(gettext 'Description (optional): ')" NETWORK_DESC

	brp_phase 0.3
	echo -e "${YELLOW}$(gettext 'Creating network via API...')${NC}"
	RESPONSE=$(curl -s -X POST \
		-H "Authorization: token $API_TOKEN" \
		-H "Content-Type: application/json" \
		-d "{\"name\": \"$NETWORK_NAME\", \"description\": \"$NETWORK_DESC\", \"private\": true}" \
		"https://api.zerotier.com/api/v1/network")

	NETWORK_ID=$(echo "$RESPONSE" | jq -r '.id // empty')

	if [ -z "$NETWORK_ID" ] || [ "$NETWORK_ID" = "null" ]; then
		echo -e "${RED}$(gettext 'Error creating network. Check your token.')${NC}"
		echo "$RESPONSE" | jq '.' 2>/dev/null || echo "$RESPONSE"
		exit 1
	fi

	brp_data network_id "$NETWORK_ID"
	brp_phase 0.6
	echo -e "${GREEN}✅ $(gettext 'Network created! ID:') $NETWORK_ID${NC}"

	# Join the network automatically
	echo -e "${YELLOW}$(gettext 'Joining the network...')${NC}"
	sudo zerotier-cli join "$NETWORK_ID" 2>/dev/null || true
	sleep 3

	# Get Node ID and authorize automatically
	NODE_ID=$(sudo zerotier-cli info 2>/dev/null | cut -d' ' -f3 || echo "")

	if [ -n "$NODE_ID" ]; then
		echo -e "${YELLOW}$(gettext 'Authorizing local device...')${NC}"
		curl -s -X POST \
			-H "Authorization: token $API_TOKEN" \
			-H "Content-Type: application/json" \
			-d '{"config": {"authorized": true}}' \
			"https://api.zerotier.com/api/v1/network/$NETWORK_ID/member/$NODE_ID" >/dev/null
		echo -e "${GREEN}✓ $(gettext 'Device authorized!')${NC}"
	fi

	# Save locally
	printf '%s:%s:%s\n' "$NETWORK_ID" "$NETWORK_NAME" "$(date +%Y-%m-%d)" >>"$NETWORKS_FILE"
	chmod 600 "$NETWORKS_FILE" 2>/dev/null || true

	IP_LOCAL=$(ip route get 1 2>/dev/null | awk '{print $7;exit}' || echo "N/A")
	PUBLIC_IP=$(curl -s https://api.ipify.org 2>/dev/null || echo "N/A")

	# Machine-readable data for the GUI (raw, locale-independent).
	brp_data web_ui "https://my.zerotier.com/network/$NETWORK_ID"
	brp_data api_url "https://api.zerotier.com/api/v1"
	brp_data public_ip "$PUBLIC_IP"
	brp_data local_ip "$IP_LOCAL"
	brp_data auth_key "$NETWORK_ID"
	brp_data network_id "$NETWORK_ID"

	echo ""
	echo -e "${CYAN}=== $(gettext 'NETWORK INFORMATION') ===${NC}"
	echo -e "$(gettext 'Web Interface:') ${YELLOW}https://my.zerotier.com/network/$NETWORK_ID${NC}"
	echo -e "$(gettext 'API URL:') ${CYAN}https://api.zerotier.com/api/v1${NC}"
	echo -e "$(gettext 'Your Public IP:') ${GREEN}$PUBLIC_IP${NC}"
	echo -e "$(gettext 'Server Local IP:') ${GREEN}$IP_LOCAL${NC}"
	echo ""
	echo -e "${CYAN}=== $(gettext 'CREDENTIALS') ===${NC}"
	echo -e "$(gettext 'Key for Friends:') ${GREEN}$NETWORK_ID${NC}"
	brp_phase 0.95
	echo ""
	echo -e "${YELLOW}⚠️  $(gettext 'Share the network ID with your friends:') $NETWORK_ID${NC}"
	echo -e "${YELLOW}$(gettext 'They use the Connect option and enter the network ID')${NC}"
	echo -e "${BLUE}====================================================${NC}"
}

join_network() {
	header
	echo -e "${YELLOW}$(gettext 'JOIN ZEROTIER NETWORK (Client/Guest)')${NC}"
	echo ""

	check_deps

	read -r -p "$(gettext 'ZeroTier Network ID (16 characters): ')" NETWORK_ID
	if [ -z "$NETWORK_ID" ]; then
		echo -e "${RED}$(gettext 'Network ID cannot be empty!')${NC}"
		exit 1
	fi

	echo -e "${YELLOW}$(gettext 'Joining network:') $NETWORK_ID${NC}"
	sudo zerotier-cli join "$NETWORK_ID"
	sleep 5

	# Check status
	echo ""
	echo -e "${CYAN}=== $(gettext 'CONNECTION STATUS') ===${NC}"
	sudo zerotier-cli listnetworks 2>/dev/null || true

	NODE_ID=$(sudo zerotier-cli info 2>/dev/null | cut -d' ' -f3 || echo "N/A")
	echo ""
	echo -e "$(gettext 'Your Node ID: ')${GREEN}$NODE_ID${NC}"
	echo -e "${YELLOW}⚠️  $(gettext 'You must be authorized by the network administrator!')${NC}"
	echo -e "${CYAN}$(gettext 'Give your Node ID to the administrator: ')${GREEN}$NODE_ID${NC}"

	echo -e "${BLUE}====================================================${NC}"
	echo -e "${GREEN}$(gettext 'Join request sent!')${NC}"
	echo -e "${YELLOW}$(gettext 'Wait for the administrator to authorize you')${NC}"
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
		echo -e "${CYAN}=== $(gettext 'ZEROTIER STATUS') ===${NC}"
		sudo zerotier-cli info 2>/dev/null || echo "$(gettext 'ZeroTier not connected')"
		sudo zerotier-cli listnetworks 2>/dev/null || true
		;;
	*) exit 0 ;;
	esac
}

main_menu
