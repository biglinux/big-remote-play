#!/bin/bash
# shellcheck disable=SC1091,SC2005,SC2129
# gettext fallback intentionally emits no newline, so translated menu strings are
# wrapped in echo throughout this legacy interactive script.
# ==============================================================================
# HEADSCALE ULTIMATE MANAGER - Rafael Ruscher Edition
# Com Caddy Proxy para resolver erro de CORS (Failed to Fetch)
# Suporte para HOST (Servidor) e GUEST (Cliente)
# VERSÃO COMPLETA COM TODAS CORREÇÕES
# ==============================================================================

set -e
umask 077

# Interface colors
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m'

# Machine-readable markers for the GUI (locale-independent ASCII); other output
# is human prose translated via gettext.
brp_data() { printf 'BRP_DATA %s=%s\n' "$1" "$2"; }
brp_phase() { printf 'BRP_PHASE %s\n' "$1"; }
AUTH_KEY_TMP=""

cleanup_auth_key_tmp() {
	if [ -n "$AUTH_KEY_TMP" ] && [ -f "$AUTH_KEY_TMP" ]; then
		rm -f "$AUTH_KEY_TMP"
	fi
}

trap cleanup_auth_key_tmp EXIT INT TERM

write_auth_key_file() {
	cleanup_auth_key_tmp
	AUTH_KEY_TMP=$(mktemp "${TMPDIR:-/tmp}/brp-headscale-auth.XXXXXX")
	chmod 600 "$AUTH_KEY_TMP"
	printf '%s' "$1" >"$AUTH_KEY_TMP"
	printf 'file:%s' "$AUTH_KEY_TMP"
}

export TEXTDOMAIN=big-remote-play
if [ -f /usr/bin/gettext.sh ]; then
	. /usr/bin/gettext.sh
else
	gettext() { printf '%s' "$1"; }
	eval_gettext() { printf '%s' "$1"; }
fi

header() {
	clear
	echo -e "${BLUE}====================================================${NC}"
	echo -e "${GREEN}      $(gettext 'HEADSCALE & CLOUDFLARE - PRIVATE NETWORK')         ${NC}"
	echo -e "${BLUE}====================================================${NC}"
}

check_deps() {
	echo -e "${YELLOW}$(gettext 'Checking dependencies...')${NC}"
	for pkg in docker docker-compose jq curl miniupnpc; do
		if ! command -v $pkg &>/dev/null; then
			echo -e "${YELLOW}$(gettext 'Installing') $pkg...${NC}"
			sudo pacman -S $pkg --noconfirm 2>/dev/null || echo -e "${RED}$(gettext 'Failed to install') $pkg. $(gettext 'Install it manually.')${NC}"
		fi
	done
}

# --- PORT CHECK FUNCTION ---
check_ports() {
	echo -e "${YELLOW}$(gettext 'Checking open ports...')${NC}"

	# Check whether Docker is running
	if ! systemctl is-active --quiet docker; then
		echo -e "${RED}$(gettext 'Docker is not running. Starting...')${NC}"
		sudo systemctl start docker
		sudo systemctl enable docker
	fi

	# Check local ports
	echo -e "${CYAN}$(gettext 'Local ports in use:')${NC}"
	sudo netstat -tulpn | grep -E ':(80|443|41641)' || true

	# Try to open firewall ports
	echo -e "${YELLOW}$(gettext 'Configuring firewall...')${NC}"

	# For UFW
	if command -v ufw &>/dev/null; then
		sudo ufw allow 80/tcp 2>/dev/null || true
		sudo ufw allow 443/tcp 2>/dev/null || true
		sudo ufw allow 41641/udp 2>/dev/null || true
		sudo ufw reload 2>/dev/null || true
		echo -e "${GREEN}$(gettext 'UFW firewall configured')${NC}"
	fi

	# For firewalld
	if command -v firewall-cmd &>/dev/null; then
		sudo firewall-cmd --permanent --add-port=80/tcp 2>/dev/null || true
		sudo firewall-cmd --permanent --add-port=443/tcp 2>/dev/null || true
		sudo firewall-cmd --permanent --add-port=41641/udp 2>/dev/null || true
		sudo firewall-cmd --reload 2>/dev/null || true
		echo -e "${GREEN}$(gettext 'Firewalld configured')${NC}"
	fi
}

# --- SERVER (HOST) FUNCTION ---
setup_host() {
	header
	echo -e "${YELLOW}$(gettext 'HOST (SERVER) SETUP')${NC}"

	# Gather information
	read -r -p "$(gettext 'Domain (e.g. vpn.ruscher.org): ')" DOMAIN
	read -r -p "Cloudflare Zone ID: " ZONE_ID
	read -r -p "Cloudflare API Token: " API_TOKEN
	HEADSCALE_VERSION="${HEADSCALE_VERSION:-0.29.1}"
	HEADSCALE_IMAGE="${HEADSCALE_IMAGE:-docker.io/headscale/headscale:v$HEADSCALE_VERSION}"
	CADDY_IMAGE="${CADDY_IMAGE:-caddy:2}"

	# Check dependencies
	check_deps
	check_ports

	# Create directories
	mkdir -p ~/headscale-server/{config,data,caddy_data,caddy_config}
	cd ~/headscale-server

	# 1. Creating optimized Caddyfile
	echo -e "${YELLOW}$(gettext 'Generating reverse proxy config (Caddy)...')${NC}"
	cat <<EOF >Caddyfile
{
    # Enable logs for debugging
    debug
    admin off
}

$DOMAIN {
    # Tailscale captive portal detection
    handle /generate_204 {
        respond 204
    }

    # Log every request
    log {
        output stdout
        level INFO
    }
    
    # CORS headers for all responses
    header {
        Access-Control-Allow-Origin "*"
        Access-Control-Allow-Methods "GET, POST, PUT, DELETE, OPTIONS"
        Access-Control-Allow-Headers "*"
    }
    
    # Headscale UI
    handle /web/* {
        reverse_proxy headscale-ui:80 {
            header_up Host {host}
            header_up True-Client-IP {remote_host}
            header_up X-Real-IP {remote_host}
            header_up X-Forwarded-For {remote_host}
            header_up X-Forwarded-Proto {scheme}
        }
    }
    
    # Headscale API
    handle /api/* {
        reverse_proxy headscale:8080 {
            header_up Host {host}
            header_up True-Client-IP {remote_host}
            header_up X-Real-IP {remote_host}
            header_up X-Forwarded-For {remote_host}
            header_up X-Forwarded-Proto {scheme}
        }
    }
    
    # Tailscale login endpoints
    handle /ts2021/* {
        reverse_proxy headscale:8080 {
            header_up Host {host}
            header_up True-Client-IP {remote_host}
            header_up X-Real-IP {remote_host}
            header_up X-Forwarded-For {remote_host}
            header_up X-Forwarded-Proto {scheme}
        }
    }
    
    # Register endpoint
    handle /register/* {
        reverse_proxy headscale:8080 {
            header_up Host {host}
            header_up True-Client-IP {remote_host}
            header_up X-Real-IP {remote_host}
            header_up X-Forwarded-For {remote_host}
            header_up X-Forwarded-Proto {scheme}
        }
    }
    
    # For all other endpoints
    handle {
        reverse_proxy headscale:8080 {
            header_up Host {host}
            header_up True-Client-IP {remote_host}
            header_up X-Real-IP {remote_host}
            header_up X-Forwarded-For {remote_host}
            header_up X-Forwarded-Proto {scheme}
        }
    }
}
EOF

	# 2. Updated Docker Compose
	cat <<EOF >docker-compose.yml
services:
  headscale:
    image: $HEADSCALE_IMAGE
    container_name: headscale
    read_only: true
    tmpfs:
      - /var/run/headscale
    volumes:
      - ./config:/etc/headscale:ro
      - ./data:/var/lib/headscale
    command: serve
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "headscale", "health"]
    networks:
      - headscale-network
    ports:
      - "41641:41641/udp"
      - "127.0.0.1:8080:8080"
      - "127.0.0.1:9090:9090"

  headscale-ui:
    image: ghcr.io/gurucomputing/headscale-ui:latest
    container_name: headscale-ui
    restart: unless-stopped
    networks:
      - headscale-network
    depends_on:
      - headscale

  caddy:
    image: $CADDY_IMAGE
    container_name: caddy
    ports:
      - "80:80"
      - "443:443"
      - "443:443/udp"
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile
      - ./caddy_data:/data
      - ./caddy_config:/config
    restart: unless-stopped
    networks:
      - headscale-network
    depends_on:
      - headscale
      - headscale-ui

networks:
  headscale-network:
    driver: bridge
EOF

	# 3. Headscale configuration
	echo -e "${YELLOW}$(gettext 'Configuring Headscale...')${NC}"
	if [ ! -f ./config/config.yaml ]; then
		curl -fsSL "https://raw.githubusercontent.com/juanfont/headscale/v$HEADSCALE_VERSION/config-example.yaml" -o ./config/config.yaml

		# Essential settings
		sed -i "s|server_url: .*|server_url: https://$DOMAIN|" ./config/config.yaml
		sed -i 's|listen_addr: 127.0.0.1:8080|listen_addr: 0.0.0.0:8080|' ./config/config.yaml
		sed -i 's|disable_check_updates: false|disable_check_updates: true|' ./config/config.yaml
	fi

	# Permissions
	chmod 700 config data caddy_data caddy_config 2>/dev/null || true

	# 4. Validate generated config with the selected Headscale image.
	echo -e "${YELLOW}$(gettext 'Validating Headscale configuration...')${NC}"
	if ! docker run --rm -v "$PWD/config:/etc/headscale:ro" "$HEADSCALE_IMAGE" configtest; then
		echo -e "${RED}$(gettext 'Invalid Headscale configuration; aborting before changing DNS or starting services.')${NC}"
		return 1
	fi

	# 5. DNS Cloudflare
	echo -e "${YELLOW}Atualizando DNS na Cloudflare...${NC}"
	CURRENT_IP=$(curl -s https://api.ipify.org)

	# Check whether a record already exists
	RESPONSE=$(curl -s -X GET "https://api.cloudflare.com/client/v4/zones/$ZONE_ID/dns_records?name=$DOMAIN" \
		-H "Authorization: Bearer $API_TOKEN" \
		-H "Content-Type: application/json")

	RECORD_ID=$(echo "$RESPONSE" | jq -r '.result[0].id // empty')

	if [ -n "$RECORD_ID" ] && [ "$RECORD_ID" != "null" ]; then
		# Atualizar registro existente
		curl -s -X PUT "https://api.cloudflare.com/client/v4/zones/$ZONE_ID/dns_records/$RECORD_ID" \
			-H "Authorization: Bearer $API_TOKEN" \
			-H "Content-Type: application/json" \
			--data "{\"type\":\"A\",\"name\":\"$DOMAIN\",\"content\":\"$CURRENT_IP\",\"ttl\":120,\"proxied\":false}" |
			jq -r '.success'
		echo -e "${GREEN}Registro DNS atualizado${NC}"
	else
		# Create new record
		curl -s -X POST "https://api.cloudflare.com/client/v4/zones/$ZONE_ID/dns_records" \
			-H "Authorization: Bearer $API_TOKEN" \
			-H "Content-Type: application/json" \
			--data "{\"type\":\"A\",\"name\":\"$DOMAIN\",\"content\":\"$CURRENT_IP\",\"ttl\":120,\"proxied\":false}" |
			jq -r '.success'
		echo -e "${GREEN}$(gettext 'New DNS record created')${NC}"
	fi

	# 5. Subindo containers
	echo -e "${YELLOW}$(gettext 'Starting containers...')${NC}"
	docker-compose down 2>/dev/null || true
	docker-compose up -d

	# Wait for startup
	echo -e "${YELLOW}$(gettext 'Waiting for services to start...')${NC}"
	sleep 15

	# 6. Configurar UPnP (Roteador)
	IP_LOCAL=$(ip route get 1 | awk '{print $7;exit}')
	echo -e "${YELLOW}$(gettext 'Configuring router ports via UPnP...')${NC}"

	# Try to open ports
	for port in 80 443; do
		upnpc -d $port TCP 2>/dev/null || true
		upnpc -e "Headscale HTTPS" -a "$IP_LOCAL" "$port" "$port" TCP 2>/dev/null || true
	done

	upnpc -d 41641 UDP 2>/dev/null || true
	upnpc -e "Headscale Data" -a "$IP_LOCAL" 41641 41641 UDP 2>/dev/null || true

	# 7. Create user and keys
	echo -e "${YELLOW}$(gettext 'Creating user and keys...')${NC}"

	# Try to create user
	docker exec headscale headscale users create amigos 2>/dev/null || true

	# Get USER_ID
	USER_ID=$(docker exec headscale headscale users list -o json 2>/dev/null | jq -r '.[] | select(.name=="amigos") | .id')

	if [ -z "$USER_ID" ] || [ "$USER_ID" = "null" ]; then
		echo -e "${YELLOW}$(gettext 'Creating new user...')${NC}"
		docker exec headscale headscale users create amigos
		USER_ID=$(docker exec headscale headscale users list -o json | jq -r '.[] | select(.name=="amigos") | .id')
	fi

	# Create Auth Key (valid for 7 days)
	AUTH_KEY=$(docker exec headscale headscale preauthkeys create --user "$USER_ID" --reusable --expiration 168h 2>/dev/null)

	# 8. Test services
	echo -e "${YELLOW}$(gettext 'Testing services...')${NC}"

	# Test Headscale
	if curl -s http://localhost:8080/health >/dev/null; then
		echo -e "${GREEN}✓ $(gettext 'Headscale is working')${NC}"
	else
		echo -e "${RED}✗ $(gettext 'Headscale is not responding')${NC}"
		docker-compose logs headscale --tail=20
	fi

	# Test Caddy
	if curl -s http://localhost:80 >/dev/null; then
		echo -e "${GREEN}✓ $(gettext 'Caddy is working')${NC}"
	else
		echo -e "${RED}✗ $(gettext 'Caddy is not responding')${NC}"
		docker-compose logs caddy --tail=20
	fi

	# 9. Show final information
	header
	# Machine-readable data for the GUI (raw, locale-independent).
	brp_data web_ui "https://$DOMAIN/web"
	brp_data api_url "https://$DOMAIN"
	brp_data public_ip "$CURRENT_IP"
	brp_data local_ip "$IP_LOCAL"
	brp_data auth_key "$AUTH_KEY"
	brp_data domain "$DOMAIN"
	brp_phase 0.95

	echo -e "${GREEN}✅ $(gettext 'SERVER CONFIGURED SUCCESSFULLY!')${NC}"
	echo ""
	echo -e "${CYAN}=== $(gettext 'ACCESS INFORMATION') ===${NC}"
	echo -e "$(gettext 'Web Interface:') ${YELLOW}https://$DOMAIN/web${NC}"
	echo -e "$(gettext 'API URL:') ${CYAN}https://$DOMAIN${NC}"
	echo -e "$(gettext 'Your Public IP:') ${GREEN}$CURRENT_IP${NC}"
	echo -e "$(gettext 'Server Local IP:') ${GREEN}$IP_LOCAL${NC}"
	echo ""
	echo -e "${CYAN}=== $(gettext 'CREDENTIALS') ===${NC}"
	echo -e "${GREEN}$(gettext 'Key for Friends:') $AUTH_KEY${NC}"
	echo ""
	echo -e "${CYAN}=== $(gettext 'USEFUL COMMANDS') ===${NC}"
	echo -e "$(gettext 'View logs: ')${YELLOW}cd ~/headscale-server && docker-compose logs -f${NC}"
	echo -e "$(gettext 'Restart: ')${YELLOW}cd ~/headscale-server && docker-compose restart${NC}"
	echo -e "Parar: ${YELLOW}cd ~/headscale-server && docker-compose down${NC}"
	echo ""
	echo -e "${YELLOW}⚠️  $(gettext 'SHARE ONLY THE KEY FOR FRIENDS')${NC}"
	echo -e "${BLUE}====================================================${NC}"

	# Start log monitoring
	echo ""
	read -p "$(gettext 'Show real-time logs? (y/N): ')" -n 1 -r
	echo
	if [[ $REPLY =~ ^[Ss]$ ]]; then
		cd ~/headscale-server
		docker-compose logs -f --tail=50
	fi
}

# --- FUNÇÃO PARA O CLIENTE (GUEST) ---
setup_guest() {
	header
	echo -e "${YELLOW}$(gettext 'CLIENT (GUEST) SETUP')${NC}"

	# Gather information
	read -r -p "$(gettext 'Server domain (e.g. vpn.ruscher.org): ')" HOST_DOMAIN
	read -r -p "$(gettext 'Access Key (Auth Key): ')" AUTH_KEY

	echo -e "${YELLOW}$(gettext 'Checking dependencies...')${NC}"

	# 1. Test connection to the server BEFORE installing
	echo -e "${CYAN}$(gettext 'Testing connection to the server...')${NC}"
	if curl -fsS --connect-timeout 10 "https://$HOST_DOMAIN/health" >/dev/null 2>&1; then
		echo -e "${GREEN}✓ $(gettext 'Server reachable')${NC}"
	elif curl -fsS --connect-timeout 10 "https://$HOST_DOMAIN" >/dev/null 2>&1; then
		echo -e "${GREEN}✓ $(gettext 'Server reachable')${NC}"
	else
		echo -e "${RED}✗ $(gettext 'Could not connect to the server')${NC}"
		echo -e "${YELLOW}$(gettext 'Check:')${NC}"
		echo "$(gettext '1. Is the domain correct?')"
		echo "$(gettext '2. Is the server online?')"
		echo "$(gettext '3. Is the Auth Key valid?')"
		read -p "Continuar mesmo assim? (s/N): " -n 1 -r
		echo
		if [[ ! $REPLY =~ ^[Ss]$ ]]; then
			exit 1
		fi
	fi

	# 2. Install Tailscale
	echo -e "${YELLOW}$(gettext 'Installing Tailscale...')${NC}"
	if ! command -v tailscale &>/dev/null; then
		sudo pacman -S tailscale --noconfirm
	else
		echo -e "${GREEN}✓ $(gettext 'Tailscale already installed')${NC}"
	fi

	# 3. Start service
	sudo systemctl start tailscaled
	sudo systemctl enable tailscaled

	# 4. Connect to the network
	echo -e "${YELLOW}$(gettext 'Connecting to the private network...')${NC}"
	AUTH_KEY_ARG=$(write_auth_key_file "$AUTH_KEY")

	# Try connection with timeout
	if timeout 60 sudo tailscale up \
		--login-server="https://$HOST_DOMAIN" \
		--auth-key="$AUTH_KEY_ARG" \
		--reset \
		--force-reauth \
		--accept-routes=true \
		--accept-dns=true \
		--hostname="guest-$(hostname)-$(date +%s)" \
		--advertise-exit-node=false; then

		echo -e "${GREEN}✅ $(gettext 'Connection established!')${NC}"
	else
		echo -e "${RED}✗ $(gettext 'Connection failed')${NC}"
		echo -e "${YELLOW}$(gettext 'Trying alternative method...')${NC}"

		# Alternative method
		sudo tailscale up \
			--login-server="https://$HOST_DOMAIN" \
			--auth-key="$AUTH_KEY_ARG" \
			--force-reauth \
			--reset
	fi

	# 5. Wait and verify
	echo -e "${YELLOW}$(gettext 'Waiting for connection...')${NC}"
	sleep 5

	# 6. Check status
	echo -e "${CYAN}=== $(gettext 'CONNECTION STATUS') ===${NC}"
	STATUS_JSON=$(tailscale status --json 2>/dev/null)
	BACKEND_STATE=$(echo "$STATUS_JSON" | jq -r .BackendState)

	if [ "$BACKEND_STATE" = "Running" ]; then
		echo -e "${GREEN}✅ $(gettext 'Connected successfully!')${NC}"

		# Get IP
		YOUR_IP=$(tailscale ip -4 2>/dev/null || tailscale ip 2>/dev/null)
		echo -e "$(gettext 'Your network IP: ')${GREEN}$YOUR_IP${NC}"

		# Testar ping para servidor (Headscale Server IP usually starts with 100.64.0.1 if using magic dns, but not always pingable)
		# Instead, just show success since BackendState is Running

		# Show peers
		echo ""
		echo -e "${CYAN}=== $(gettext 'CONNECTED DEVICES') ===${NC}"
		PEERS_COUNT=$(echo "$STATUS_JSON" | jq '.Peer | length')
		if [ "$PEERS_COUNT" -gt 0 ]; then
			tailscale status
		else
			echo -e "${YELLOW}$(gettext 'No other device connected yet. You are the first!')${NC}"
			echo -e "${YELLOW}$(gettext 'Invite friends using the same Access Key.')${NC}"
		fi

	else
		echo -e "${RED}✗ $(gettext 'Connection failed')${NC}"
		echo ""
		echo -e "${YELLOW}=== $(gettext 'TROUBLESHOOTING') ===${NC}"
		echo "$(gettext '1. Check that the server is online')"
		echo "$(gettext '2. Check the Auth Key')"
		echo "$(gettext '3. Try restarting:') sudo systemctl restart tailscaled"
		echo "$(gettext '4. Check logs:') sudo journalctl -u tailscaled -f"

		# Show logs
		echo ""
		read -p "$(gettext 'View Tailscale logs? (y/N): ')" -n 1 -r
		echo
		if [[ $REPLY =~ ^[Ss]$ ]]; then
			sudo journalctl -u tailscaled -n 50 --no-pager
		fi
	fi

	# 7. Configure firewall if needed
	if command -v ufw &>/dev/null; then
		echo -e "${YELLOW}$(gettext 'Configuring firewall...')${NC}"
		sudo ufw allow in on tailscale0 2>/dev/null || true
		sudo ufw reload 2>/dev/null || true
	fi

	echo -e "${BLUE}====================================================${NC}"
	echo -e "${GREEN}$(gettext 'Client setup complete!')${NC}"
	echo -e "${YELLOW}Para desconectar: sudo tailscale down${NC}"
	echo -e "${YELLOW}Para reconectar: sudo tailscale up${NC}"
}

# --- FUNÇÃO DE TROUBLESHOOTING ---
troubleshoot() {
	header
	echo -e "${YELLOW}=== $(gettext 'ADVANCED TROUBLESHOOTING') ===${NC}"
	echo "$(gettext '1) Check server status')"
	echo "$(gettext '2) Check server logs')"
	echo "$(gettext '3) Test external connection')"
	echo "$(gettext '4) Recreate access keys')"
	echo "$(gettext '5) Back to main menu')"
	read -r -p "$(gettext 'Choose an option: ')" TROUBLE_OPT

	case $TROUBLE_OPT in
	1)
		if [ -d ~/headscale-server ]; then
			cd ~/headscale-server
			docker-compose ps
			docker-compose logs --tail=20
		else
			echo -e "${RED}$(gettext 'Server directory not found')${NC}"
		fi
		;;
	2)
		if [ -d ~/headscale-server ]; then
			cd ~/headscale-server
			echo -e "${CYAN}=== $(gettext 'HEADSCALE LOGS') ===${NC}"
			docker-compose logs headscale --tail=50
			echo -e "${CYAN}=== $(gettext 'CADDY LOGS') ===${NC}"
			docker-compose logs caddy --tail=50
		fi
		;;
	3)
		read -r -p "$(gettext 'Domain to test: ')" TEST_DOMAIN
		echo -e "${YELLOW}$(gettext 'Testing') $TEST_DOMAIN...${NC}"
		curl -v --connect-timeout 10 "https://$TEST_DOMAIN/health" ||
			curl -v --connect-timeout 10 "https://$TEST_DOMAIN" ||
			echo -e "${RED}$(gettext 'Connection failed')${NC}"
		;;
	4)
		if [ -d ~/headscale-server ]; then
			cd ~/headscale-server
			echo -e "${YELLOW}$(gettext 'Recreating keys...')${NC}"
			docker exec headscale headscale preauthkeys list --user amigos
			read -p "$(gettext 'Create a new key? (y/N): ')" -n 1 -r
			echo
			if [[ $REPLY =~ ^[Ss]$ ]]; then
				NEW_KEY=$(docker exec headscale headscale preauthkeys create --user amigos --reusable --expiration 168h)
				echo -e "${GREEN}$(gettext 'New key: ')$NEW_KEY${NC}"
			fi
		fi
		;;
	esac

	read -r -p "$(gettext 'Press Enter to continue...')"
	main_menu
}

# --- MENU PRINCIPAL ---
main_menu() {
	header
	check_deps
	echo "$(gettext 'Select an option:')"
	echo "$(gettext '1) Be the HOST (create and manage the network)')"
	echo "$(gettext '2) Be the GUEST (join a friend network)')"
	echo "$(gettext '3) Troubleshooting')"
	echo "$(gettext '4) Exit')"
	read -r -p "$(gettext 'Option: ')" OPT

	case $OPT in
	1) setup_host ;;
	2) setup_guest ;;
	3) troubleshoot ;;
	*) exit 0 ;;
	esac
}

# Executar menu principal
main_menu
