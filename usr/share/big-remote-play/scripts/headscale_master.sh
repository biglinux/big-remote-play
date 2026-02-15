#!/bin/bash
# ==============================================================================
# HEADSCALE ULTIMATE MANAGER - Rafael Ruscher Edition
# Com Caddy Proxy para resolver erro de CORS (Failed to Fetch)
# Suporte para HOST (Servidor) e GUEST (Cliente)
# ==============================================================================

set -e

# Cores para interface
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m'

header() {
    clear
    echo -e "${BLUE}====================================================${NC}"
    echo -e "${GREEN}      HEADSCALE & CLOUDFLARE - REDE PRIVADA         ${NC}"
    echo -e "${BLUE}====================================================${NC}"
}

check_deps() {
    echo -e "${YELLOW}Verificando dependências...${NC}"
    for pkg in docker docker-compose jq curl miniupnpc; do
        command -v $pkg &> /dev/null || sudo pacman -S $pkg --noconfirm
    done
}

# --- FUNÇÃO PARA O SERVIDOR (HOST) ---
setup_host() {
    header
    echo -e "${YELLOW}CONFIGURAÇÃO DE HOST (SERVIDOR)${NC}"
    read -p "Domínio (ex: vpn.ruscher.org): " DOMAIN
    read -p "Cloudflare Zone ID: " ZONE_ID
    read -p "Cloudflare API Token: " API_TOKEN

    mkdir -p ~/headscale-server/{config,data}
    cd ~/headscale-server

    # 1. Criando Caddyfile para matar o erro de CORS
    echo -e "${YELLOW}Gerando configuração do Proxy Reverso (Caddy)...${NC}"
    cat <<EOF > Caddyfile
:80 {
    # Rota para a Interface
    handle /web* {
        reverse_proxy headscale-ui:80
    }
    # Rota para a API e Tailscale
    handle /api* {
        reverse_proxy headscale:8080
    }
    handle / {
        reverse_proxy headscale:8080
    }
}
EOF

    # 2. Docker Compose com Caddy incluído
    cat <<EOF > docker-compose.yml
services:
  headscale:
    image: headscale/headscale:latest
    container_name: headscale
    volumes:
      - ./config:/etc/headscale
      - ./data:/var/lib/headscale
    command: serve
    restart: unless-stopped

  headscale-ui:
    image: ghcr.io/gurucomputing/headscale-ui:latest
    container_name: headscale-ui
    restart: unless-stopped

  caddy:
    image: caddy:latest
    container_name: caddy
    ports:
      - "80:80"
      - "41641:41641/udp"
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile
    restart: unless-stopped
EOF

    # 3. Configuração do Headscale
    if [ ! -f ./config/config.yaml ]; then
        curl -s https://raw.githubusercontent.com/juanfont/headscale/main/config-example.yaml -o ./config/config.yaml
        sed -i "s|server_url: .*|server_url: http://$DOMAIN|" ./config/config.yaml
        sed -i 's|listen_addr: 127.0.0.1:8080|listen_addr: 0.0.0.0:8080|' ./config/config.yaml
        sed -i 's|db_path: .*|db_path: /var/lib/headscale/db.sqlite|' ./config/config.yaml
    fi
    sudo chmod -R 777 config data

    # 4. DNS Cloudflare
    echo -e "${YELLOW}Atualizando DNS na Cloudflare...${NC}"
    CURRENT_IP=$(curl -s https://api.ipify.org)
    RECORD_ID=$(curl -s -X GET "https://api.cloudflare.com/client/v4/zones/$ZONE_ID/dns_records?name=$DOMAIN" \
        -H "Authorization: Bearer $API_TOKEN" -H "Content-Type: application/json" | jq -r '.result[0].id')
    
    if [ "$RECORD_ID" != "null" ] && [ -n "$RECORD_ID" ]; then
        curl -s -X PUT "https://api.cloudflare.com/client/v4/zones/$ZONE_ID/dns_records/$RECORD_ID" \
            -H "Authorization: Bearer $API_TOKEN" -H "Content-Type: application/json" \
            --data "{\"type\":\"A\",\"name\":\"$DOMAIN\",\"content\":\"$CURRENT_IP\",\"ttl\":120,\"proxied\":false}"
    fi

    # 5. Subindo containers e Portas via UPnP (Roteador)
    docker-compose down || true
    docker-compose up -d

    IP_LOCAL=$(ip route get 1 | awk '{print $7;exit}')
    echo -e "${YELLOW}Tentando abrir portas no roteador via UPnP...${NC}"
    upnpc -d 80 TCP 2>/dev/null || true
    upnpc -e "Headscale Proxy" -a $IP_LOCAL 80 80 TCP || true
    upnpc -e "Headscale Data" -a $IP_LOCAL 41641 41641 UDP || true

    echo -e "${YELLOW}Finalizando e gerando credenciais...${NC}"
    sleep 10

    # Criar Usuário e API Key
    docker exec headscale headscale users create amigos || true
    USER_ID=$(docker exec headscale headscale users list -o json | jq -r '.[] | select(.name=="amigos") | .id')
    AUTH_KEY=$(docker exec headscale headscale preauthkeys create --user "$USER_ID" --reusable --expiration 24h)
    API_KEY=$(docker exec headscale headscale apikeys create)

    header
    echo -e "${GREEN}TUDO PRONTO COM PROXY REVERSO!${NC}"
    echo -e "Acesse a UI em: ${YELLOW}http://$DOMAIN/web${NC}"
    echo -e "URL para configurar na UI: ${CYAN}http://$DOMAIN${NC}"
    echo -e "Sua API Key: ${CYAN}$API_KEY${NC}"
    echo -e "Chave da VPN para Amigos: ${GREEN}$AUTH_KEY${NC}"
    echo -e "IP Local do Servidor: $IP_LOCAL"
    echo -e "${BLUE}====================================================${NC}"
}

# --- FUNÇÃO PARA O CLIENTE (GUEST) ---
setup_guest() {
    header
    echo -e "${YELLOW}CONFIGURAÇÃO DE CLIENTE (GUEST)${NC}"
    read -p "Domínio do Servidor do seu amigo: " HOST_DOMAIN
    read -p "Chave de Acesso (Auth Key): " AUTH_KEY

    echo -e "${YELLOW}Instalando e configurando Tailscale...${NC}"
    # Verifica se já está instalado para não reinstalar à toa
    command -v tailscale &> /dev/null || sudo pacman -S tailscale --noconfirm
    
    sudo systemctl enable --now tailscaled

    echo -e "${YELLOW}Conectando à rede privada...${NC}"
    # O segredo está na flag --reset que limpa configurações anteriores conflitantes
    sudo tailscale up --login-server http://$HOST_DOMAIN --authkey $AUTH_KEY --reset --accept-dns=false

    # Ajuste de Firewall (BigLinux/UFW)
    if command -v ufw &> /dev/null; then
        echo "Liberando interface tailscale0 no Firewall..."
        sudo ufw allow in on tailscale0
        sudo ufw reload
    fi

    echo -e "${GREEN}SUCESSO! Você agora está na rede privada.${NC}"
    tailscale status
}
# --- MENU PRINCIPAL ---
header
check_deps
echo "Selecione uma opção:"
echo "1) Ser o HOST (Criar e Gerenciar a Rede)"
echo "2) Ser o GUEST (Entrar na rede de um amigo)"
echo "3) Sair"
read -p "Opção: " OPT

case $OPT in
    1) setup_host ;;
    2) setup_guest ;;
    *) exit 0 ;;
esac
