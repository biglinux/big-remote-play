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
green='\033[0;32m'
blue='\033[0;34m'
yellow='\033[1;33m'
red='\033[0;31m'
cyan='\033[0;36m'
nc='\033[0m'

# Machine-readable markers for the GUI (locale-independent ASCII); other output
# is human prose translated via gettext.
brp_data() { printf 'BRP_DATA %s=%s\n' "$1" "$2"; }
brp_phase() { printf 'BRP_PHASE %s\n' "$1"; }
trap 'exit 130' INT
trap 'exit 143' TERM

export TEXTDOMAIN=big-remote-play
export TEXTDOMAINDIR="${TEXTDOMAINDIR:-/usr/share/locale}"
if [ -f /usr/bin/gettext.sh ]; then
	. /usr/bin/gettext.sh
else
	gettext() { printf '%s' "$1"; }
	eval_gettext() { printf '%s' "$1"; }
fi

header() {
	clear
	echo -e "${blue}====================================================${nc}"
	echo -e "${green}      $(gettext 'HEADSCALE & CLOUDFLARE - PRIVATE NETWORK')         ${nc}"
	echo -e "${blue}====================================================${nc}"
}

check_deps() {
	echo -e "${yellow}$(gettext 'Checking dependencies...')${nc}"
	for pkg in docker docker-compose jq curl miniupnpc; do
		if ! command -v $pkg &>/dev/null; then
			echo -e "${yellow}$(gettext 'Installing') $pkg...${nc}"
			sudo pacman -S $pkg --noconfirm 2>/dev/null || echo -e "${red}$(gettext 'Failed to install') $pkg. $(gettext 'Install it manually.')${nc}"
		fi
	done
}

# --- PORT CHECK FUNCTION ---
check_ports() {
	echo -e "${yellow}$(gettext 'Checking open ports...')${nc}"

	# Check whether Docker is running
	if ! systemctl is-active --quiet docker; then
		echo -e "${red}$(gettext 'Docker is not running. Starting...')${nc}"
		sudo systemctl start docker
		sudo systemctl enable docker
	fi

	# Check local ports
	echo -e "${cyan}$(gettext 'Local ports in use:')${nc}"
	sudo netstat -tulpn | grep -E ':(80|443|41641)' || true

	# Try to open firewall ports
	echo -e "${yellow}$(gettext 'Configuring firewall...')${nc}"

	# For UFW
	if command -v ufw &>/dev/null; then
		sudo ufw allow 80/tcp 2>/dev/null || true
		sudo ufw allow 443/tcp 2>/dev/null || true
		sudo ufw allow 41641/udp 2>/dev/null || true
		sudo ufw reload 2>/dev/null || true
		echo -e "${green}$(gettext 'UFW firewall configured')${nc}"
	fi

	# For firewalld
	if command -v firewall-cmd &>/dev/null; then
		sudo firewall-cmd --permanent --add-port=80/tcp 2>/dev/null || true
		sudo firewall-cmd --permanent --add-port=443/tcp 2>/dev/null || true
		sudo firewall-cmd --permanent --add-port=41641/udp 2>/dev/null || true
		sudo firewall-cmd --reload 2>/dev/null || true
		echo -e "${green}$(gettext 'Firewalld configured')${nc}"
	fi
}

# --- SERVER (HOST) FUNCTION ---
setup_host() {
	header
	echo -e "${yellow}$(gettext 'HOST (SERVER) SETUP')${nc}"

	# Gather information
	read -r -p "$(gettext 'Domain (e.g. vpn.ruscher.org): ')" DOMAIN
	read -r -p "$(gettext 'Cloudflare Zone ID: ')" ZONE_ID
	read -r -p "$(gettext 'Cloudflare API Token: ')" API_TOKEN
	# Env-overridable pins (uppercase env in, lowercase locals).
	headscale_version="${HEADSCALE_VERSION:-0.29.1}"
	headscale_image="${HEADSCALE_IMAGE:-docker.io/headscale/headscale:v$headscale_version}"
	caddy_image="${CADDY_IMAGE:-caddy:2}"

	# Check dependencies
	check_deps
	check_ports

	# Create directories
	mkdir -p ~/headscale-server/{config,data,caddy_data,caddy_config}
	cd ~/headscale-server

	# 1. Creating optimized Caddyfile
	echo -e "${yellow}$(gettext 'Generating reverse proxy config (Caddy)...')${nc}"
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
    image: $headscale_image
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
    image: $caddy_image
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
	echo -e "${yellow}$(gettext 'Configuring Headscale...')${nc}"
	if [ ! -f ./config/config.yaml ]; then
		curl -fsSL "https://raw.githubusercontent.com/juanfont/headscale/v$headscale_version/config-example.yaml" -o ./config/config.yaml

		# Essential settings
		sed -i "s|server_url: .*|server_url: https://$DOMAIN|" ./config/config.yaml
		sed -i 's|listen_addr: 127.0.0.1:8080|listen_addr: 0.0.0.0:8080|' ./config/config.yaml
		sed -i 's|disable_check_updates: false|disable_check_updates: true|' ./config/config.yaml
	fi

	# Permissions
	chmod 700 config data caddy_data caddy_config 2>/dev/null || true

	# 4. Validate generated config with the selected Headscale image.
	echo -e "${yellow}$(gettext 'Validating Headscale configuration...')${nc}"
	if ! docker run --rm -v "$PWD/config:/etc/headscale:ro" "$headscale_image" configtest; then
		echo -e "${red}$(gettext 'Invalid Headscale configuration; aborting before changing DNS or starting services.')${nc}"
		return 1
	fi

	# 5. DNS Cloudflare
	echo -e "${yellow}$(gettext 'Updating DNS in Cloudflare...')${nc}"
	current_ip=$(curl -s https://api.ipify.org)

	# Check whether a record already exists
	response=$(curl -s -X GET "https://api.cloudflare.com/client/v4/zones/$ZONE_ID/dns_records?name=$DOMAIN" \
		-H "Authorization: Bearer $API_TOKEN" \
		-H "Content-Type: application/json")

	record_id=$(echo "$response" | jq -r '.result[0].id // empty')

	if [ -n "$record_id" ] && [ "$record_id" != "null" ]; then
		# Atualizar registro existente
		curl -s -X PUT "https://api.cloudflare.com/client/v4/zones/$ZONE_ID/dns_records/$record_id" \
			-H "Authorization: Bearer $API_TOKEN" \
			-H "Content-Type: application/json" \
			--data "{\"type\":\"A\",\"name\":\"$DOMAIN\",\"content\":\"$current_ip\",\"ttl\":120,\"proxied\":false}" |
			jq -r '.success'
		echo -e "${green}$(gettext 'DNS record updated')${nc}"
	else
		# Create new record
		curl -s -X POST "https://api.cloudflare.com/client/v4/zones/$ZONE_ID/dns_records" \
			-H "Authorization: Bearer $API_TOKEN" \
			-H "Content-Type: application/json" \
			--data "{\"type\":\"A\",\"name\":\"$DOMAIN\",\"content\":\"$current_ip\",\"ttl\":120,\"proxied\":false}" |
			jq -r '.success'
		echo -e "${green}$(gettext 'New DNS record created')${nc}"
	fi

	# 5. Subindo containers
	echo -e "${yellow}$(gettext 'Starting containers...')${nc}"
	docker-compose down 2>/dev/null || true
	docker-compose up -d

	# Wait for startup
	echo -e "${yellow}$(gettext 'Waiting for services to start...')${nc}"
	sleep 15

	# 6. Configurar UPnP (Roteador)
	ip_local=$(ip route get 1 | awk '{print $7;exit}')
	echo -e "${yellow}$(gettext 'Configuring router ports via UPnP...')${nc}"

	# Try to open ports
	for port in 80 443; do
		upnpc -d $port TCP 2>/dev/null || true
		upnpc -e "Headscale HTTPS" -a "$ip_local" "$port" "$port" TCP 2>/dev/null || true
	done

	upnpc -d 41641 UDP 2>/dev/null || true
	upnpc -e "Headscale Data" -a "$ip_local" 41641 41641 UDP 2>/dev/null || true

	# 7. Create user and keys
	echo -e "${yellow}$(gettext 'Creating user and keys...')${nc}"

	# Try to create user
	docker exec headscale headscale users create amigos 2>/dev/null || true

	# Get user_id
	user_id=$(docker exec headscale headscale users list -o json 2>/dev/null | jq -r '.[] | select(.name=="amigos") | .id')

	if [ -z "$user_id" ] || [ "$user_id" = "null" ]; then
		echo -e "${yellow}$(gettext 'Creating new user...')${nc}"
		docker exec headscale headscale users create amigos
		user_id=$(docker exec headscale headscale users list -o json | jq -r '.[] | select(.name=="amigos") | .id')
	fi

	# Create Auth Key (valid for 7 days)
	auth_key=$(docker exec headscale headscale preauthkeys create --user "$user_id" --reusable --expiration 168h 2>/dev/null)

	# 8. Test services
	echo -e "${yellow}$(gettext 'Testing services...')${nc}"

	# Test Headscale
	if curl -s http://localhost:8080/health >/dev/null; then
		echo -e "${green}✓ $(gettext 'Headscale is working')${nc}"
	else
		echo -e "${red}✗ $(gettext 'Headscale is not responding')${nc}"
		docker-compose logs headscale --tail=20
	fi

	# Test Caddy
	if curl -s http://localhost:80 >/dev/null; then
		echo -e "${green}✓ $(gettext 'Caddy is working')${nc}"
	else
		echo -e "${red}✗ $(gettext 'Caddy is not responding')${nc}"
		docker-compose logs caddy --tail=20
	fi

	# 9. Show final information
	header
	# Machine-readable data for the GUI (raw, locale-independent).
	brp_data web_ui "https://$DOMAIN/web"
	brp_data api_url "https://$DOMAIN"
	brp_data public_ip "$current_ip"
	brp_data local_ip "$ip_local"
	brp_data auth_key "$auth_key"
	brp_data domain "$DOMAIN"
	brp_phase 0.95

	echo -e "${green}✅ $(gettext 'SERVER CONFIGURED SUCCESSFULLY!')${nc}"
	echo ""
	echo -e "${cyan}=== $(gettext 'ACCESS INFORMATION') ===${nc}"
	echo -e "$(gettext 'Web Interface:') ${yellow}https://$DOMAIN/web${nc}"
	echo -e "$(gettext 'API URL:') ${cyan}https://$DOMAIN${nc}"
	echo -e "$(gettext 'Your Public IP:') ${green}$current_ip${nc}"
	echo -e "$(gettext 'Server Local IP:') ${green}$ip_local${nc}"
	echo ""
	echo -e "${cyan}=== $(gettext 'CREDENTIALS') ===${nc}"
	echo -e "${green}$(gettext 'Key for Friends:') $auth_key${nc}"
	echo ""
	echo -e "${cyan}=== $(gettext 'USEFUL COMMANDS') ===${nc}"
	echo -e "$(gettext 'View logs: ')${yellow}cd ~/headscale-server && docker-compose logs -f${nc}"
	echo -e "$(gettext 'Restart: ')${yellow}cd ~/headscale-server && docker-compose restart${nc}"
	echo -e "$(gettext 'Stop the server with:') ${yellow}cd ~/headscale-server && docker-compose down${nc}"
	echo ""
	echo -e "${yellow}⚠️  $(gettext 'SHARE ONLY THE KEY FOR FRIENDS')${nc}"
	echo -e "${blue}====================================================${nc}"

	# Start log monitoring
	echo ""
	read -p "$(gettext 'Show real-time logs? (y/N): ')" -n 1 -r
	echo
	if [[ $REPLY =~ ^[YySs]$ ]]; then
		cd ~/headscale-server
		docker-compose logs -f --tail=50
	fi
}

# --- FUNÇÃO DE TROUBLESHOOTING ---
troubleshoot() {
	header
	echo -e "${yellow}=== $(gettext 'ADVANCED TROUBLESHOOTING') ===${nc}"
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
			echo -e "${red}$(gettext 'Server directory not found')${nc}"
		fi
		;;
	2)
		if [ -d ~/headscale-server ]; then
			cd ~/headscale-server
			echo -e "${cyan}=== $(gettext 'HEADSCALE LOGS') ===${nc}"
			docker-compose logs headscale --tail=50
			echo -e "${cyan}=== $(gettext 'CADDY LOGS') ===${nc}"
			docker-compose logs caddy --tail=50
		fi
		;;
	3)
		read -r -p "$(gettext 'Domain to test: ')" TEST_DOMAIN
		echo -e "${yellow}$(gettext 'Testing') $TEST_DOMAIN...${nc}"
		curl -v --connect-timeout 10 "https://$TEST_DOMAIN/health" ||
			curl -v --connect-timeout 10 "https://$TEST_DOMAIN" ||
			echo -e "${red}$(gettext 'Connection failed')${nc}"
		;;
	4)
		if [ -d ~/headscale-server ]; then
			cd ~/headscale-server
			echo -e "${yellow}$(gettext 'Recreating keys...')${nc}"
			docker exec headscale headscale preauthkeys list --user amigos
			read -p "$(gettext 'Create a new key? (y/N): ')" -n 1 -r
			echo
			if [[ $REPLY =~ ^[YySs]$ ]]; then
				new_key=$(docker exec headscale headscale preauthkeys create --user amigos --reusable --expiration 168h)
				echo -e "${green}$(gettext 'New key: ')$new_key${nc}"
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
	echo "$(gettext '2) Troubleshooting')"
	echo "$(gettext '3) Exit')"
	read -r -p "$(gettext 'Option: ')" OPT

	case $OPT in
	1) setup_host ;;
	2) troubleshoot ;;
	*) exit 0 ;;
	esac
}

# Executar menu principal
main_menu
