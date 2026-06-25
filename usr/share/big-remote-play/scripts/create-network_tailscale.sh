#!/bin/bash
# shellcheck disable=SC1091,SC2005
# gettext fallback intentionally emits no newline, so translated menu strings are
# wrapped in echo throughout this legacy interactive script.

# Tailscale Network Manager Script
# $(gettext 'Version:') 1.0
# Developed for BigLinux/Manjaro

umask 077

# Colors for better visualization
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
WHITE='\033[1;37m'
NC='\033[0m' # No Color

# Machine-readable markers for the GUI (locale-independent ASCII); other output
# is human prose translated via gettext.
brp_phase() { printf 'BRP_PHASE %s\n' "$1"; }
export TEXTDOMAIN=big-remote-play
if [ -f /usr/bin/gettext.sh ]; then
	. /usr/bin/gettext.sh
else
	gettext() { printf '%s' "$1"; }
	eval_gettext() { printf '%s' "$1"; }
fi

# Global variables
TAILSCALE_CONFIG="${XDG_CONFIG_HOME:-$HOME/.config}/big-remote-play/tailscale"
ACCOUNTS_FILE="$TAILSCALE_CONFIG/accounts.txt"
AUTH_KEY_TMP=""

cleanup_auth_key_tmp() {
	if [ -n "$AUTH_KEY_TMP" ] && [ -f "$AUTH_KEY_TMP" ]; then
		rm -f "$AUTH_KEY_TMP"
	fi
}

trap cleanup_auth_key_tmp EXIT INT TERM

write_auth_key_file() {
	cleanup_auth_key_tmp
	AUTH_KEY_TMP=$(mktemp "${TMPDIR:-/tmp}/brp-tailscale-auth.XXXXXX")
	chmod 600 "$AUTH_KEY_TMP"
	printf '%s' "$1" >"$AUTH_KEY_TMP"
	printf 'file:%s' "$AUTH_KEY_TMP"
}

# Function to check dependencies
check_dependencies() {
	brp_phase 0.1
	echo -e "${YELLOW}$(gettext 'Checking dependencies...')${NC}"

	# Check whether Tailscale is installed
	if ! command -v tailscale &>/dev/null; then
		echo -e "${RED}$(gettext 'Tailscale not found. Installing...')${NC}"

		# Install tailscale from the official repos (pacman, not AUR).
		sudo pacman -S --needed --noconfirm tailscale

		# Enable and start service
		sudo systemctl enable tailscaled
		sudo systemctl start tailscaled
	fi

	# Check whether jq is installed
	if ! command -v jq &>/dev/null; then
		echo -e "${RED}$(gettext 'jq not found. Installing...')${NC}"
		sudo pacman -S jq --noconfirm
	fi

	# Check whether curl is installed
	if ! command -v curl &>/dev/null; then
		echo -e "${RED}$(gettext 'curl not found. Installing...')${NC}"
		sudo pacman -S curl --noconfirm
	fi

	# Create config directory
	mkdir -p "$TAILSCALE_CONFIG"
	chmod 700 "$TAILSCALE_CONFIG" 2>/dev/null || true
}

# Function to log in to Tailscale
login_tailscale() {
	echo -e "${BLUE}=== $(gettext 'TAILSCALE LOGIN') ===${NC}"

	# Check and start the service first
	echo -e "${YELLOW}$(gettext 'Checking tailscaled service...')${NC}"

	if ! systemctl is-active --quiet tailscaled; then
		echo -e "${YELLOW}$(gettext 'tailscaled service is not running. Starting...')${NC}"
		sudo systemctl start tailscaled
		sleep 3

		if ! systemctl is-active --quiet tailscaled; then
			echo -e "${RED}✗ $(gettext 'Failed to start tailscaled. Check manually:')${NC}"
			echo "sudo systemctl status tailscaled"
			echo "sudo journalctl -u tailscaled -f"
			return 1
		fi

		sudo systemctl enable tailscaled
	fi

	echo -e "${GREEN}✓ $(gettext 'tailscaled service is running')${NC}"
	sleep 2

	echo -e "${YELLOW}$(gettext 'Choose the login method:')${NC}"
	echo "$(gettext '1) Browser login (recommended)')"
	echo "$(gettext '2) Login with auth key')"
	echo "$(gettext '3) Back')"

	read -r LOGIN_OPTION

	case $LOGIN_OPTION in
	1)
		brp_phase 0.5
		echo -e "${GREEN}$(gettext 'Starting browser login...')${NC}"
		echo -e "${YELLOW}$(gettext 'A URL will open in your browser. Log in with your account.')${NC}"

		if sudo tailscale up --reset 2>&1 | grep -q "https://"; then
			echo -e "${GREEN}$(gettext 'Login URL generated. Follow the instructions in the browser.')${NC}"
		else
			sudo tailscale login
		fi

		echo -e "${YELLOW}$(gettext 'Waiting for authentication...')${NC}"
		for _ in {1..15}; do
			sleep 2
			if sudo tailscale status &>/dev/null; then
				echo -e "${GREEN}✓ $(gettext 'Login confirmed!')${NC}"
				break
			fi
			echo -n "."
		done
		echo ""
		;;
	2)
		echo -e "${CYAN}$(gettext 'Enter your auth key:')${NC}"
		echo -e "${YELLOW}($(gettext 'Expected format:') tskey-auth-xxxxxx-yyyyyy)${NC}"
		read -r AUTH_KEY

		if [ -n "$AUTH_KEY" ]; then
			echo -e "${YELLOW}$(gettext 'Authenticating with key...')${NC}"
			AUTH_KEY_ARG=$(write_auth_key_file "$AUTH_KEY")

			if ! sudo tailscale status &>/dev/null; then
				echo -e "${YELLOW}$(gettext 'Service not responding. Reconnecting...')${NC}"
				sudo systemctl restart tailscaled
				sleep 3
			fi

			OUTPUT=$(sudo tailscale up --reset --force-reauth --auth-key="$AUTH_KEY_ARG" 2>&1)
			EXIT_CODE=$?

			if [ $EXIT_CODE -ne 0 ]; then
				echo -e "${YELLOW}$(gettext 'First attempt failed. Trying alternative method...')${NC}"

				if echo "$OUTPUT" | grep -q "interactive"; then
					sudo tailscale up --force-reauth --auth-key="$AUTH_KEY_ARG"
				elif echo "$OUTPUT" | grep -q "unauthenticated"; then
					sudo tailscale login --auth-key="$AUTH_KEY_ARG"
				else
					echo -e "${YELLOW}$(gettext 'Restarting service and trying again...')${NC}"
					sudo systemctl stop tailscaled
					sleep 2
					sudo systemctl start tailscaled
					sleep 3
					sudo tailscale up --force-reauth --auth-key="$AUTH_KEY_ARG"
				fi
			fi

			if sudo tailscale status &>/dev/null; then
				echo -e "${GREEN}✓ $(gettext 'Login successful!')${NC}"
			else
				echo -e "${RED}✗ $(gettext 'Login error. Diagnostics:')${NC}"
				echo "$(gettext '1. Check that the key is valid (not expired)')"
				echo "$(gettext '2. Check connectivity:') ping 8.8.8.8"
				echo "$(gettext '3. Check the service:') sudo journalctl -u tailscaled -n 20"
				echo ""
				echo -e "${YELLOW}$(gettext 'Alternative manual command:')${NC}"
				echo "sudo tailscale up --auth-key=file:/path/to/auth-key --force-reauth --reset"
			fi
		else
			echo -e "${RED}$(gettext 'Key cannot be empty!')${NC}"
			return 1
		fi
		;;
	3)
		return
		;;
	*)
		echo -e "${RED}$(gettext 'Invalid option!')${NC}"
		;;
	esac

	# Success check
	echo -e "${YELLOW}$(gettext 'Checking connection...')${NC}"

	MAX_RETRIES=8
	RETRY_COUNT=0
	LOGIN_SUCCESS=false

	while [ $RETRY_COUNT -lt $MAX_RETRIES ]; do
		if sudo tailscale status &>/dev/null; then
			LOGIN_SUCCESS=true
			break
		fi
		echo -e "${YELLOW}$(gettext 'Waiting for connection, attempt') $((RETRY_COUNT + 1))/$MAX_RETRIES${NC}"
		sleep 3
		RETRY_COUNT=$((RETRY_COUNT + 1))
	done

	if [ "$LOGIN_SUCCESS" = true ]; then
		echo -e "${GREEN}✓ $(gettext 'Logged in and connection established successfully!')${NC}"
		echo ""
		echo -e "${CYAN}$(gettext 'Device information:')${NC}"

		if command -v jq &>/dev/null; then
			DEVICE_NAME=$(tailscale status --json 2>/dev/null | jq -r '.Self.DNSName' 2>/dev/null | sed 's/\.$//')
		fi

		if [ -z "$DEVICE_NAME" ]; then
			DEVICE_NAME=$(tailscale status 2>/dev/null | head -1 | awk '{print $2}')
		fi

		[ -n "$DEVICE_NAME" ] && echo -e "$(gettext 'Name: ')${GREEN}$DEVICE_NAME${NC}"

		IPV4=$(tailscale ip -4 2>/dev/null)
		IPV6=$(tailscale ip -6 2>/dev/null)

		[ -n "$IPV4" ] && echo -e "IPv4: ${GREEN}$IPV4${NC}"
		[ -n "$IPV6" ] && echo -e "IPv6: ${GREEN}$IPV6${NC}"

		echo ""
		echo -e "${CYAN}$(gettext 'Devices on the network:')${NC}"
		tailscale status 2>/dev/null | head -5 || echo "Nenhum dispositivo encontrado"

		if [ -n "$DEVICE_NAME" ] && [ -n "$IPV4" ]; then
			printf '%s:%s:%s\n' "$DEVICE_NAME" "$IPV4" "$(date)" >>"$ACCOUNTS_FILE"
			chmod 600 "$ACCOUNTS_FILE" 2>/dev/null || true
		fi
	else
		echo -e "${RED}✗ $(gettext 'Connection failed. Full diagnostics:')${NC}"
		echo ""
		echo -e "${YELLOW}$(gettext '1. Service status:')${NC}"
		systemctl status tailscaled --no-pager | head -3

		echo -e "${YELLOW}2. Logs recentes:${NC}"
		sudo journalctl -u tailscaled -n 5 --no-pager

		echo -e "${YELLOW}$(gettext '3. Connectivity:')${NC}"
		if ping -c 1 8.8.8.8 &>/dev/null; then
			echo -e "${GREEN}   ✓ Internet OK${NC}"
		else
			echo -e "${RED}   ✗ Sem internet${NC}"
		fi

		echo ""
		echo -e "${YELLOW}Comandos para resolver manualmente:${NC}"
		echo "sudo systemctl restart tailscaled"
		echo "sudo tailscale down"
		echo "sudo tailscale up --reset"
		echo ""
		echo -e "${CYAN}$(gettext 'After running the commands above, try logging in again.')${NC}"
	fi
}

# Function to create a new network/account
create_network() {
	echo -e "${BLUE}=== $(gettext 'CREATE NEW TAILSCALE NETWORK') ===${NC}"
	echo -e "${YELLOW}$(gettext 'Note: in Tailscale, networks are different accounts/orgs.')${NC}"
	echo -e "${YELLOW}$(gettext 'You need a different account for each network.')${NC}"

	echo -e "${CYAN}$(gettext 'Network/company name:')${NC}"
	read -r NETWORK_NAME

	if [ -z "$NETWORK_NAME" ]; then
		echo -e "${RED}$(gettext 'Name cannot be empty!')${NC}"
		return 1
	fi

	echo -e "${YELLOW}$(gettext 'To create a new network:')${NC}"
	echo "1. Acesse https://login.tailscale.com"
	echo "$(gettext '2. Create a new account with a different email')"
	echo "$(gettext '3. Or use a different domain to create a separate org')"
	echo ""
	echo -e "${CYAN}$(gettext 'Log out and log in with a new account? (y/N):')${NC}"
	read -r CONFIRM

	if [[ "$CONFIRM" =~ ^[Ss]$ ]]; then
		logout_tailscale
		login_tailscale
	fi
}

# Function to list networks/devices
list_networks() {
	echo -e "${BLUE}=== $(gettext 'TAILSCALE NETWORK STATUS') ===${NC}"

	if ! sudo tailscale status &>/dev/null; then
		echo -e "${RED}$(gettext 'Not connected to Tailscale. Log in first.')${NC}"
		return 1
	fi

	echo -e "${GREEN}$(gettext 'Current status:')${NC}"
	sudo tailscale status

	echo ""
	echo -e "${YELLOW}$(gettext 'IP addresses:')${NC}"
	sudo tailscale ip

	echo ""
	if command -v jq &>/dev/null; then
		echo -e "${CYAN}$(gettext 'Detailed information:')${NC}"
		sudo tailscale status --json | jq '.Self' 2>/dev/null || echo "$(gettext 'Could not get details')"
	fi
}

# Function to list all devices
list_devices() {
	echo -e "${BLUE}=== $(gettext 'DEVICES ON THE NETWORK') ===${NC}"

	if ! sudo tailscale status &>/dev/null; then
		echo -e "${RED}$(gettext 'Not connected to Tailscale. Log in first.')${NC}"
		return 1
	fi

	echo -e "${GREEN}Dispositivos conectados:${NC}"

	LINE_COUNT=$(sudo tailscale status | wc -l)

	if [ "$LINE_COUNT" -gt 1 ]; then
		sudo tailscale status | tail -n +2 | while read -r line; do
			DEVICE_IP=$(echo "$line" | awk '{print $1}')
			DEVICE_NAME=$(echo "$line" | awk '{print $2}')
			DEVICE_STATUS=$(echo "$line" | awk '{print $3}')

			if [ "$DEVICE_STATUS" == "online" ]; then
				echo -e "${GREEN}✓ $DEVICE_NAME - $DEVICE_IP (Online)${NC}"
			else
				echo -e "${RED}✗ $DEVICE_NAME - $DEVICE_IP (Offline)${NC}"
			fi
		done
	else
		echo -e "${YELLOW}$(gettext 'Only this device connected to the network')${NC}"
		THIS_DEVICE=$(sudo tailscale status | head -1)
		echo -e "${CYAN}→ $THIS_DEVICE${NC}"
	fi
}

# Function to add a new device
add_device() {
	echo -e "${BLUE}=== $(gettext 'ADD NEW DEVICE') ===${NC}"
	echo ""
	echo -e "${YELLOW}$(gettext 'Method 1: invite URL')${NC}"
	echo "1. Acesse https://login.tailscale.com/admin/invite"
	echo "2. Gere um link de convite"
	echo "3. Execute no novo dispositivo: curl -fsSL <LINK> | sh"
	echo ""
	echo -e "${YELLOW}$(gettext 'Method 2: auth key')${NC}"
	echo "1. Acesse https://login.tailscale.com/admin/settings/keys"
	echo "$(gettext '2. Generate an auth key')"
	echo "3. No novo dispositivo: sudo tailscale up --auth-key=<CHAVE>"
	echo ""
	echo -e "${YELLOW}$(gettext 'Method 3: login with the same account')${NC}"
	echo "$(gettext 'Just log in with the same account on the new device')"
	echo ""
	echo -e "${CYAN}$(gettext 'Press ENTER to return to the main menu')${NC}"
	read -r
}

# Function to remove a device
remove_device() {
	echo -e "${BLUE}=== $(gettext 'REMOVE DEVICE') ===${NC}"
	echo -e "${RED}$(gettext 'WARNING: This will remove the device from the network!')${NC}"

	list_devices

	echo -e "${CYAN}$(gettext 'Enter the device name to remove:')${NC}"
	read -r DEVICE_NAME

	if [ -z "$DEVICE_NAME" ]; then
		echo -e "${RED}$(gettext 'Name cannot be empty!')${NC}"
		return 1
	fi

	echo -e "${YELLOW}Tem certeza que deseja remover '$DEVICE_NAME'? (s/N):${NC}"
	read -r CONFIRM

	if [[ "$CONFIRM" =~ ^[Ss]$ ]]; then
		echo -e "${YELLOW}$(gettext 'To remove via API, you need:')${NC}"
		echo "1. Acesse https://login.tailscale.com/admin/machines"
		echo "2. $(gettext 'Find the device') $DEVICE_NAME"
		echo "3. Clique nos 3 pontos e selecione 'Delete'"
		echo ""
		echo -e "${CYAN}Deseja apenas desconectar localmente? (s/N):${NC}"
		read -r LOCAL_ONLY

		if [[ "$LOCAL_ONLY" =~ ^[Ss]$ ]]; then
			sudo tailscale logout
			echo -e "${GREEN}Desconectado localmente.${NC}"
		fi
	fi
}

# Function to authorize a device
authorize_device() {
	echo -e "${BLUE}=== $(gettext 'AUTHORIZE DEVICE') ===${NC}"

	if ! sudo tailscale status &>/dev/null; then
		echo -e "${RED}$(gettext 'Not connected to Tailscale. Log in first.')${NC}"
		return 1
	fi

	echo -e "${YELLOW}$(gettext 'Checking pending devices...')${NC}"

	if command -v jq &>/dev/null; then
		PENDING_DEVICES=$(sudo tailscale status --json 2>/dev/null | jq -r '.Peer[] | select(.Authorized == false) | "\(.DNSName) (pendente)"' 2>/dev/null)

		if [ -n "$PENDING_DEVICES" ]; then
			echo -e "${GREEN}Dispositivos pendentes encontrados:${NC}"
			echo "$PENDING_DEVICES"
		else
			echo -e "${YELLOW}$(gettext 'No pending device found')${NC}"
		fi
	else
		echo -e "${YELLOW}$(gettext 'Could not check pending devices (jq not installed)')${NC}"
	fi

	echo ""
	echo -e "${YELLOW}Para autorizar dispositivos manualmente:${NC}"
	echo "1. Acesse https://login.tailscale.com/admin/machines"
	echo "2. $(gettext 'Look for devices with status Pending')"
	echo "3. Clique em 'Approve' ou 'Authorize'"
	echo ""
	echo -e "${CYAN}$(gettext 'Press ENTER to continue')${NC}"
	read -r
}

# Function to share the network
share_network() {
	echo -e "${BLUE}=== $(gettext 'SHARE NETWORK') ===${NC}"
	echo -e "${YELLOW}$(gettext 'Sharing options:')${NC}"
	echo "$(gettext '1) Share with a specific user')"
	echo "2) Criar link de convite"
	echo "$(gettext '3) Back')"

	read -r SHARE_OPTION

	case $SHARE_OPTION in
	1)
		echo -e "${CYAN}$(gettext 'Enter the user email:')${NC}"
		read -r USER_EMAIL

		if [ -n "$USER_EMAIL" ]; then
			echo -e "${GREEN}Para compartilhar com $USER_EMAIL:${NC}"
			echo "1. Acesse https://login.tailscale.com/admin/users"
			echo "2. Clique em 'Invite user'"
			echo "$(gettext '3. Enter the email and select permissions')"
		fi
		;;
	2)
		echo -e "${GREEN}Criando link de convite:${NC}"
		echo "1. Acesse https://login.tailscale.com/admin/invite"
		echo "$(gettext '2. Configure the desired options')"
		echo "3. Compartilhe o link gerado"
		;;
	3)
		return
		;;
	*)
		echo -e "${RED}$(gettext 'Invalid option!')${NC}"
		;;
	esac

	echo ""
	echo -e "${CYAN}$(gettext 'Press ENTER to continue')${NC}"
	read -r
}

# Function to configure ACLs
configure_acl() {
	echo -e "${BLUE}=== CONFIGURAR ACLs ===${NC}"
	echo -e "${YELLOW}ACLs controlam o acesso entre dispositivos.${NC}"
	echo ""
	echo "$(gettext 'To configure ACLs:')"
	echo "1. Acesse https://login.tailscale.com/admin/acls"
	echo "2. Edite o arquivo JSON de ACLs"
	echo ""
	echo "$(gettext 'Basic example:')"
	cat <<'EOF'
{
  "acls": [
    {"action": "accept", "src": ["*"], "dst": ["*:*"]}
  ]
}
EOF
	echo ""
	echo -e "${CYAN}$(gettext 'Open the ACL panel in the browser? (y/N):')${NC}"
	read -r OPEN_BROWSER

	if [[ "$OPEN_BROWSER" =~ ^[Ss]$ ]]; then
		xdg-open "https://login.tailscale.com/admin/acls" 2>/dev/null ||
			echo -e "${RED}$(gettext 'Could not open the browser. Open manually:') https://login.tailscale.com/admin/acls${NC}"
	fi
}

# Function to log out
logout_tailscale() {
	echo -e "${BLUE}=== $(gettext 'LOG OUT OF TAILSCALE') ===${NC}"
	echo -e "${YELLOW}$(gettext 'Do you really want to log out of Tailscale? (y/N):')${NC}"
	read -r CONFIRM

	if [[ "$CONFIRM" =~ ^[Ss]$ ]]; then
		sudo tailscale logout
		echo -e "${GREEN}$(gettext 'Logout successful!')${NC}"
	else
		echo -e "${YELLOW}$(gettext 'Operation cancelled.')${NC}"
	fi
}

# Function to show detailed status
show_status() {
	echo -e "${BLUE}=== $(gettext 'DETAILED TAILSCALE STATUS') ===${NC}"

	echo -e "${YELLOW}$(gettext 'Service status:')${NC}"
	systemctl status tailscaled --no-pager | grep "Active:"

	echo ""
	echo -e "${YELLOW}$(gettext 'Version:')${NC}"
	tailscale version

	echo ""
	if sudo tailscale status &>/dev/null; then
		echo -e "${GREEN}✓ $(gettext 'Connected to Tailscale')${NC}"
		echo ""
		echo -e "${YELLOW}$(gettext 'Node information:')${NC}"
		if command -v jq &>/dev/null; then
			sudo tailscale status --json | jq '.Self | {Name: .DNSName, IPs: .Addresses, Online: .Online}' 2>/dev/null
		else
			sudo tailscale status | head -1
		fi

		echo ""
		echo -e "${YELLOW}$(gettext 'Statistics:')${NC}"
		TOTAL_PEERS=$(sudo tailscale status | wc -l)
		TOTAL_PEERS=$((TOTAL_PEERS - 1))
		echo "Total de peers: $TOTAL_PEERS"

		echo ""
		echo -e "${YELLOW}Rotas:${NC}"
		ip route show | grep tailscale || echo "$(gettext 'No specific tailscale route')"
	else
		echo -e "${RED}✗ Desconectado do Tailscale${NC}"
	fi
}

# Function to change settings
change_settings() {
	echo -e "${BLUE}=== $(gettext 'CHANGE SETTINGS') ===${NC}"
	echo -e "${YELLOW}$(gettext 'Configuration options:')${NC}"
	echo "1) Ativar/Desativar roteamento de sub-redes"
	echo "2) Ativar/Desativar modo exit node"
	echo "$(gettext '3) Change device name')"
	echo "$(gettext '4) Configure DNS server')"
	echo "$(gettext '5) Back')"

	read -r SETTINGS_OPTION

	case $SETTINGS_OPTION in
	1)
		echo -e "${CYAN}$(gettext 'Enter subnets to route (e.g. 192.168.1.0/24):')${NC}"
		read -r SUBNETS
		sudo tailscale up --advertise-routes="$SUBNETS"
		echo -e "${GREEN}Sub-redes configuradas!${NC}"
		;;
	2)
		echo -e "${CYAN}Ativar como exit node? (s/N):${NC}"
		read -r EXIT_NODE
		if [[ "$EXIT_NODE" =~ ^[Ss]$ ]]; then
			sudo tailscale up --advertise-exit-node
			echo -e "${GREEN}Exit node ativado!${NC}"
		else
			sudo tailscale up --advertise-exit-node=false
			echo -e "${GREEN}Exit node desativado!${NC}"
		fi
		;;
	3)
		echo -e "${CYAN}$(gettext 'New name for the device:')${NC}"
		read -r NEW_NAME
		if [ -n "$NEW_NAME" ]; then
			sudo tailscale set --hostname="$NEW_NAME"
			echo -e "${GREEN}$(gettext 'Name changed to') $NEW_NAME${NC}"
		fi
		;;
	4)
		echo -e "${CYAN}$(gettext 'Enter DNS servers (comma-separated):')${NC}"
		read -r DNS_SERVERS
		sudo tailscale up --dns="$DNS_SERVERS"
		echo -e "${GREEN}DNS configurado!${NC}"
		;;
	5)
		return
		;;
	*)
		echo -e "${RED}$(gettext 'Invalid option!')${NC}"
		;;
	esac

	echo ""
	echo -e "${CYAN}$(gettext 'Press ENTER to continue')${NC}"
	read -r
}

# Function to back up settings
backup_config() {
	echo -e "${BLUE}=== $(gettext 'SETTINGS BACKUP') ===${NC}"

	OLD_UMASK=$(umask)
	umask 077
	BACKUP_FILE="$HOME/tailscale-backup-$(date +%Y%m%d-%H%M%S).tar.gz"
	if ! TEMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/tailscale-backup.XXXXXX")"; then
		umask "$OLD_UMASK"
		echo -e "${RED}$(gettext 'Could not create temporary backup directory.')${NC}"
		return 1
	fi

	if [ -d "$TAILSCALE_CONFIG" ]; then
		cp -r "$TAILSCALE_CONFIG" "$TEMP_DIR/script-config" 2>/dev/null
		echo -e "${GREEN}✓ $(gettext 'Script settings saved')${NC}"
	fi

	if [ -d "/var/lib/tailscale" ]; then
		sudo tar -czf "$TEMP_DIR/tailscale-system.tar.gz" -C /var/lib tailscale 2>/dev/null
		sudo chown "$USER:$USER" "$TEMP_DIR/tailscale-system.tar.gz"
		echo -e "${GREEN}✓ $(gettext 'Tailscale settings saved')${NC}"
	fi

	tar -czf "$BACKUP_FILE" -C "$TEMP_DIR" . 2>/dev/null
	rm -rf "$TEMP_DIR"
	umask "$OLD_UMASK"
	chmod 600 "$BACKUP_FILE" 2>/dev/null || true

	echo -e "${GREEN}✓ $(gettext 'Backup created:') $BACKUP_FILE${NC}"
	echo -e "${YELLOW}Tamanho: $(du -h "$BACKUP_FILE" | cut -f1)${NC}"

	echo -e "${CYAN}$(gettext 'Checking backup integrity...')${NC}"
	if tar -tzf "$BACKUP_FILE" &>/dev/null; then
		echo -e "${GREEN}✓ $(gettext 'Backup verified')${NC}"
	else
		echo -e "${RED}✗ $(gettext 'Backup corrupted')${NC}"
	fi

	echo ""
	echo -e "${CYAN}$(gettext 'Press ENTER to continue')${NC}"
	read -r
}

# Function to show the menu
show_menu() {
	clear
	echo -e "${BLUE}====================================${NC}"
	echo -e "${GREEN}    TAILSCALE NETWORK MANAGER v1.0  ${NC}"
	echo -e "${BLUE}====================================${NC}"
	echo -e "${WHITE}Bem-vindo ao gerenciador Tailscale${NC}"
	echo -e "${BLUE}====================================${NC}"
	echo ""
	echo -e "${YELLOW}1)${NC} Login/Fazer login"
	echo -e "${YELLOW}2)${NC} $(gettext 'Create new network/account')"
	echo -e "${YELLOW}3)${NC} $(gettext 'View network status')"
	echo -e "${YELLOW}4)${NC} $(gettext 'List devices')"
	echo -e "${YELLOW}5)${NC} $(gettext 'Add new device (instructions)')"
	echo -e "${YELLOW}6)${NC} $(gettext 'Remove device')"
	echo -e "${YELLOW}7)${NC} $(gettext 'Authorize pending device')"
	echo -e "${YELLOW}8)${NC} $(gettext 'Share network')"
	echo -e "${YELLOW}9)${NC} Configurar ACLs"
	echo -e "${YELLOW}10)${NC} $(gettext 'Change settings')"
	echo -e "${YELLOW}11)${NC} $(gettext 'Detailed status')"
	echo -e "${YELLOW}12)${NC} $(gettext 'Logout/Exit')"
	echo -e "${YELLOW}13)${NC} $(gettext 'Backup settings')"
	echo -e "${YELLOW}0)${NC} $(gettext 'Exit the program')"
	echo ""
	echo -e "${CYAN}$(gettext 'Choose an option:')${NC}"
}

# Main function
main() {
	check_dependencies

	echo -e "${BLUE}╔════════════════════════════════════════╗${NC}"
	echo -e "${BLUE}║${GREEN}     TAILSCALE MANAGER - BigLinux      ${BLUE}║${NC}"
	echo -e "${BLUE}╚════════════════════════════════════════╝${NC}"
	sleep 1

	while true; do
		show_menu
		read -r OPTION

		case $OPTION in
		1) login_tailscale ;;
		2) create_network ;;
		3) list_networks ;;
		4) list_devices ;;
		5) add_device ;;
		6) remove_device ;;
		7) authorize_device ;;
		8) share_network ;;
		9) configure_acl ;;
		10) change_settings ;;
		11) show_status ;;
		12) logout_tailscale ;;
		13) backup_config ;;
		0)
			echo -e "${GREEN}$(gettext 'Exiting...')${NC}"
			exit 0
			;;
		*)
			echo -e "${RED}$(gettext 'Invalid option!')${NC}"
			;;
		esac

		echo ""
		echo -e "${YELLOW}$(gettext 'Press ENTER to continue')...${NC}"
		read -r
	done
}

# Run main function
main
