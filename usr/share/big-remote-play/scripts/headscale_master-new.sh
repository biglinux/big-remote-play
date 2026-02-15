#!/bin/bash
# ==============================================================================
# HEADSCALE ULTIMATE MANAGER - Rafael Ruscher Edition
# Com Caddy Proxy para resolver erro de CORS (Failed to Fetch)
# Suporte para HOST (Servidor) e GUEST (Cliente)
# VERSÃO COMPLETA COM TODAS CORREÇÕES
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
        if ! command -v $pkg &> /dev/null; then
            echo -e "${YELLOW}Instalando $pkg...${NC}"
            sudo pacman -S $pkg --noconfirm 2>/dev/null || echo -e "${RED}Falha ao instalar $pkg. Instale manualmente.${NC}"
        fi
    done
}

# --- FUNÇÃO PARA VERIFICAR PORTAS ---
check_ports() {
    echo -e "${YELLOW}Verificando portas abertas...${NC}"
    
    # Verificar se Docker está rodando
    if ! systemctl is-active --quiet docker; then
        echo -e "${RED}Docker não está rodando. Iniciando...${NC}"
        sudo systemctl start docker
        sudo systemctl enable docker
    fi
    
    # Verificar portas locais
    echo -e "${CYAN}Portas locais em uso:${NC}"
    sudo netstat -tulpn | grep -E ':(80|8080|41641)' || true
    
    # Tentar abrir portas no firewall
    echo -e "${YELLOW}Configurando firewall...${NC}"
    
    # Para UFW
    if command -v ufw &> /dev/null; then
        sudo ufw allow 80/tcp 2>/dev/null || true
        sudo ufw allow 8080/tcp 2>/dev/null || true
        sudo ufw allow 41641/udp 2>/dev/null || true
        sudo ufw reload 2>/dev/null || true
        echo -e "${GREEN}Firewall UFW configurado${NC}"
    fi
    
    # Para firewalld
    if command -v firewall-cmd &> /dev/null; then
        sudo firewall-cmd --permanent --add-port=80/tcp 2>/dev/null || true
        sudo firewall-cmd --permanent --add-port=8080/tcp 2>/dev/null || true
        sudo firewall-cmd --permanent --add-port=41641/udp 2>/dev/null || true
        sudo firewall-cmd --reload 2>/dev/null || true
        echo -e "${GREEN}Firewalld configurado${NC}"
    fi
}

# --- FUNÇÃO PARA O SERVIDOR (HOST) ---
setup_host() {
    header
    echo -e "${YELLOW}CONFIGURAÇÃO DE HOST (SERVIDOR)${NC}"
    
    # Obter informações
    read -p "Domínio (ex: vpn.ruscher.org): " DOMAIN
    read -p "Cloudflare Zone ID: " ZONE_ID
    read -p "Cloudflare API Token: " API_TOKEN
    
    # Verificar dependências
    check_deps
    check_ports
    
    # Criar diretórios
    mkdir -p ~/headscale-server/{config,data,caddy_data}
    cd ~/headscale-server
    
    # 1. Criando Caddyfile OTIMIZADO
    echo -e "${YELLOW}Gerando configuração do Proxy Reverso (Caddy)...${NC}"
    cat <<EOF > Caddyfile
{
    # Habilitar logs para debug
    debug
    admin off
}

:80, :8080 {
    # Log de todas as requisições
    log {
        output stdout
        level INFO
    }
    
    # CORS headers para todas as respostas
    header {
        Access-Control-Allow-Origin "*"
        Access-Control-Allow-Methods "GET, POST, PUT, DELETE, OPTIONS"
        Access-Control-Allow-Headers "*"
    }
    
    # Headscale UI
    handle /web/* {
        reverse_proxy headscale-ui:80 {
            header_up Host {host}
            header_up X-Real-IP {remote}
            header_up X-Forwarded-For {remote}
            header_up X-Forwarded-Proto {scheme}
        }
    }
    
    # Headscale API
    handle /api/* {
        reverse_proxy headscale:8080 {
            header_up Host {host}
            header_up X-Real-IP {remote}
            header_up X-Forwarded-For {remote}
            header_up X-Forwarded-Proto {scheme}
        }
    }
    
    # Tailscale login endpoints
    handle /ts2021/* {
        reverse_proxy headscale:8080 {
            header_up Host {host}
            header_up X-Real-IP {remote}
            header_up X-Forwarded-For {remote}
            header_up X-Forwarded-Proto {scheme}
        }
    }
    
    # Register endpoint
    handle /register/* {
        reverse_proxy headscale:8080 {
            header_up Host {host}
            header_up X-Real-IP {remote}
            header_up X-Forwarded-For {remote}
            header_up X-Forwarded-Proto {scheme}
        }
    }
    
    # Para todos os outros endpoints
    handle {
        reverse_proxy headscale:8080 {
            header_up Host {host}
            header_up X-Real-IP {remote}
            header_up X-Forwarded-For {remote}
            header_up X-Forwarded-Proto {scheme}
        }
    }
}
EOF

    # 2. Docker Compose ATUALIZADO
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
    networks:
      - headscale-network
    ports:
      - "41642:41641/udp"

  headscale-ui:
    image: ghcr.io/gurucomputing/headscale-ui:latest
    container_name: headscale-ui
    restart: unless-stopped
    networks:
      - headscale-network
    depends_on:
      - headscale

  caddy:
    image: caddy:latest
    container_name: caddy
    ports:
      - "80:80"
      - "8080:8080"
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile
      - ./caddy_data:/data
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

    # 3. Configuração do Headscale
    echo -e "${YELLOW}Configurando Headscale...${NC}"
    if [ ! -f ./config/config.yaml ]; then
        curl -s https://raw.githubusercontent.com/juanfont/headscale/main/config-example.yaml -o ./config/config.yaml
        
        # Configurações essenciais
        sed -i "s|server_url: .*|server_url: http://$DOMAIN|" ./config/config.yaml
        sed -i 's|listen_addr: 127.0.0.1:8080|listen_addr: 0.0.0.0:8080|' ./config/config.yaml
        sed -i 's|db_path: .*|db_path: /var/lib/headscale/db.sqlite|' ./config/config.yaml
        sed -i 's|disable_check_updates: false|disable_check_updates: true|' ./config/config.yaml
        sed -i 's|# magic_dns: true|magic_dns: true|' ./config/config.yaml
        
        # Permitir todas as rotas
        echo "ip_prefixes:" >> ./config/config.yaml
        echo "  - 0.0.0.0/0" >> ./config/config.yaml
        echo "  - ::/0" >> ./config/config.yaml
    fi
    
    # Permissões
    sudo chmod -R 777 config data 2>/dev/null || true

    # 4. DNS Cloudflare
    echo -e "${YELLOW}Atualizando DNS na Cloudflare...${NC}"
    CURRENT_IP=$(curl -s https://api.ipify.org)
    
    # Verificar se já existe registro
    RESPONSE=$(curl -s -X GET "https://api.cloudflare.com/client/v4/zones/$ZONE_ID/dns_records?name=$DOMAIN" \
        -H "Authorization: Bearer $API_TOKEN" \
        -H "Content-Type: application/json")
    
    RECORD_ID=$(echo $RESPONSE | jq -r '.result[0].id // empty')
    
    if [ -n "$RECORD_ID" ] && [ "$RECORD_ID" != "null" ]; then
        # Atualizar registro existente
        curl -s -X PUT "https://api.cloudflare.com/client/v4/zones/$ZONE_ID/dns_records/$RECORD_ID" \
            -H "Authorization: Bearer $API_TOKEN" \
            -H "Content-Type: application/json" \
            --data "{\"type\":\"A\",\"name\":\"$DOMAIN\",\"content\":\"$CURRENT_IP\",\"ttl\":120,\"proxied\":false}" \
            | jq -r '.success'
        echo -e "${GREEN}Registro DNS atualizado${NC}"
    else
        # Criar novo registro
        curl -s -X POST "https://api.cloudflare.com/client/v4/zones/$ZONE_ID/dns_records" \
            -H "Authorization: Bearer $API_TOKEN" \
            -H "Content-Type: application/json" \
            --data "{\"type\":\"A\",\"name\":\"$DOMAIN\",\"content\":\"$CURRENT_IP\",\"ttl\":120,\"proxied\":false}" \
            | jq -r '.success'
        echo -e "${GREEN}Novo registro DNS criado${NC}"
    fi

    # 5. Subindo containers
    echo -e "${YELLOW}Iniciando containers...${NC}"
    docker-compose down 2>/dev/null || true
    docker-compose up -d
    
    # Aguardar inicialização
    echo -e "${YELLOW}Aguardando inicialização dos serviços...${NC}"
    sleep 15

    # 6. Configurar UPnP (Roteador)
    IP_LOCAL=$(ip route get 1 | awk '{print $7;exit}')
    echo -e "${YELLOW}Configurando portas no roteador via UPnP...${NC}"
    
    # Tentar abrir portas
    for port in 80 8080; do
        upnpc -d $port TCP 2>/dev/null || true
        upnpc -e "Headscale HTTP" -a $IP_LOCAL $port $port TCP 2>/dev/null || true
    done
    
    upnpc -d 41642 UDP 2>/dev/null || true
    upnpc -e "Headscale Data" -a $IP_LOCAL 41642 41642 UDP 2>/dev/null || true

    # 7. Criar Usuário e Chaves
    echo -e "${YELLOW}Criando usuário e chaves...${NC}"
    
    # Tentar criar usuário
    docker exec headscale headscale users create amigos 2>/dev/null || true
    
    # Obter USER_ID
    USER_ID=$(docker exec headscale headscale users list -o json 2>/dev/null | jq -r '.[] | select(.name=="amigos") | .id')
    
    if [ -z "$USER_ID" ] || [ "$USER_ID" = "null" ]; then
        echo -e "${YELLOW}Criando novo usuário...${NC}"
        docker exec headscale headscale users create amigos
        USER_ID=$(docker exec headscale headscale users list -o json | jq -r '.[] | select(.name=="amigos") | .id')
    fi
    
    # Criar Auth Key (válida por 7 dias)
    AUTH_KEY=$(docker exec headscale headscale preauthkeys create --user "$USER_ID" --reusable --expiration 168h 2>/dev/null)
    
    # Criar API Key para UI
    API_KEY=$(docker exec headscale headscale apikeys create 2>/dev/null | tr -d '\n')

    # 8. Testar serviços
    echo -e "${YELLOW}Testando serviços...${NC}"
    
    # Testar Headscale
    if curl -s http://localhost:8080/health > /dev/null; then
        echo -e "${GREEN}✓ Headscale está funcionando${NC}"
    else
        echo -e "${RED}✗ Headscale não responde${NC}"
        docker-compose logs headscale --tail=20
    fi
    
    # Testar Caddy
    if curl -s http://localhost:80 > /dev/null; then
        echo -e "${GREEN}✓ Caddy está funcionando${NC}"
    else
        echo -e "${RED}✗ Caddy não responde${NC}"
        docker-compose logs caddy --tail=20
    fi

    # 9. Mostrar informações finais
    header
    echo -e "${GREEN}✅ SERVIDOR CONFIGURADO COM SUCESSO!${NC}"
    echo ""
    echo -e "${CYAN}=== INFORMAÇÕES DE ACESSO ===${NC}"
    echo -e "Interface Web: ${YELLOW}http://$DOMAIN/web${NC}"
    echo -e "URL da API: ${CYAN}http://$DOMAIN${NC}"
    echo -e "Seu IP Público: ${GREEN}$CURRENT_IP${NC}"
    echo -e "IP Local do Servidor: ${GREEN}$IP_LOCAL${NC}"
    echo ""
    echo -e "${CYAN}=== CREDENCIAIS ===${NC}"
    echo -e "API Key (para UI): ${CYAN}$API_KEY${NC}"
    echo -e "${GREEN}Chave para Amigos: $AUTH_KEY${NC}"
    echo ""
    echo -e "${CYAN}=== COMANDOS ÚTEIS ===${NC}"
    echo -e "Ver logs: ${YELLOW}cd ~/headscale-server && docker-compose logs -f${NC}"
    echo -e "Reiniciar: ${YELLOW}cd ~/headscale-server && docker-compose restart${NC}"
    echo -e "Parar: ${YELLOW}cd ~/headscale-server && docker-compose down${NC}"
    echo ""
    echo -e "${YELLOW}⚠️  COMPARTILHE APENAS A 'CHAVE PARA AMIGOS'${NC}"
    echo -e "${BLUE}====================================================${NC}"
    
    # Iniciar monitoramento de logs
    echo ""
    read -p "Deseja ver os logs em tempo real? (s/N): " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Ss]$ ]]; then
        cd ~/headscale-server
        docker-compose logs -f --tail=50
    fi
}

# --- FUNÇÃO PARA O CLIENTE (GUEST) ---
setup_guest() {
    header
    echo -e "${YELLOW}CONFIGURAÇÃO DE CLIENTE (GUEST)${NC}"
    
    # Obter informações
    read -p "Domínio do Servidor (ex: vpn.ruscher.org): " HOST_DOMAIN
    read -p "Chave de Acesso (Auth Key): " AUTH_KEY
    
    echo -e "${YELLOW}Verificando dependências...${NC}"
    
    # 1. Testar conexão com servidor ANTES de instalar
    echo -e "${CYAN}Testando conexão com o servidor...${NC}"
    if curl -s --connect-timeout 10 "http://$HOST_DOMAIN/health" > /dev/null 2>&1; then
        echo -e "${GREEN}✓ Servidor acessível${NC}"
    elif curl -s --connect-timeout 10 "http://$HOST_DOMAIN" > /dev/null 2>&1; then
        echo -e "${GREEN}✓ Servidor acessível${NC}"
    else
        echo -e "${RED}✗ Não foi possível conectar ao servidor${NC}"
        echo -e "${YELLOW}Verifique:${NC}"
        echo "1. O domínio está correto?"
        echo "2. O servidor está online?"
        echo "3. A Auth Key é válida?"
        read -p "Continuar mesmo assim? (s/N): " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Ss]$ ]]; then
            exit 1
        fi
    fi
    
    # 2. Instalar Tailscale
    echo -e "${YELLOW}Instalando Tailscale...${NC}"
    if ! command -v tailscale &> /dev/null; then
        sudo pacman -S tailscale --noconfirm
    else
        echo -e "${GREEN}✓ Tailscale já instalado${NC}"
    fi
    
    # 3. Parar e limpar estado anterior
    echo -e "${YELLOW}Preparando ambiente...${NC}"
    sudo systemctl stop tailscaled 2>/dev/null || true
    sudo rm -rf /var/lib/tailscale/* 2>/dev/null || true
    
    # 4. Iniciar serviço
    sudo systemctl start tailscaled
    sudo systemctl enable tailscaled
    
    # 5. Conectar à rede
    echo -e "${YELLOW}Conectando à rede privada...${NC}"
    
    # Tentar conexão com timeout
    if timeout 60 sudo tailscale up \
        --login-server="http://$HOST_DOMAIN" \
        --authkey="$AUTH_KEY" \
        --reset \
        --accept-routes=true \
        --accept-dns=true \
        --hostname="guest-$(hostname)-$(date +%s)" \
        --advertise-exit-node=false; then
        
        echo -e "${GREEN}✅ Conexão estabelecida!${NC}"
    else
        echo -e "${RED}✗ Falha na conexão${NC}"
        echo -e "${YELLOW}Tentando método alternativo...${NC}"
        
        # Método alternativo
        sudo tailscale up \
            --login-server="http://$HOST_DOMAIN:8080" \
            --authkey="$AUTH_KEY" \
            --reset
    fi
    
    # 6. Aguardar e verificar
    echo -e "${YELLOW}Aguardando conexão...${NC}"
    sleep 5
    
    # 7. Verificar status
    echo -e "${CYAN}=== STATUS DA CONEXÃO ===${NC}"
    STATUS_JSON=$(tailscale status --json 2>/dev/null)
    BACKEND_STATE=$(echo "$STATUS_JSON" | jq -r .BackendState)
    
    if [ "$BACKEND_STATE" = "Running" ]; then
        echo -e "${GREEN}✅ Conectado com sucesso!${NC}"
        
        # Obter IP
        YOUR_IP=$(tailscale ip -4 2>/dev/null || tailscale ip 2>/dev/null)
        echo -e "Seu IP na rede: ${GREEN}$YOUR_IP${NC}"
        
        # Testar ping para servidor (Headscale Server IP usually starts with 100.64.0.1 if using magic dns, but not always pingable)
        # Instead, just show success since BackendState is Running
        
        # Mostrar peers
        echo ""
        echo -e "${CYAN}=== DISPOSITIVOS CONECTADOS ===${NC}"
        PEERS_COUNT=$(echo "$STATUS_JSON" | jq '.Peer | length')
        if [ "$PEERS_COUNT" -gt 0 ]; then
            tailscale status
        else
            echo -e "${YELLOW}Nenhum outro dispositivo conectado ainda. Você é o primeiro!${NC}"
            echo -e "${YELLOW}Convide amigos usando a mesma Chave de Acesso.${NC}"
        fi
        
    else
        echo -e "${RED}✗ Falha na conexão${NC}"
        echo ""
        echo -e "${YELLOW}=== TROUBLESHOOTING ===${NC}"
        echo "1. Verifique se o servidor está online"
        echo "2. Confirme a Auth Key"
        echo "3. Tente reiniciar: sudo systemctl restart tailscaled"
        echo "4. Verifique logs: sudo journalctl -u tailscaled -f"
        
        # Mostrar logs
        echo ""
        read -p "Ver logs do Tailscale? (s/N): " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Ss]$ ]]; then
            sudo journalctl -u tailscaled -n 50 --no-pager
        fi
    fi
    
    # 8. Configurar firewall se necessário
    if command -v ufw &> /dev/null; then
        echo -e "${YELLOW}Configurando firewall...${NC}"
        sudo ufw allow in on tailscale0 2>/dev/null || true
        sudo ufw reload 2>/dev/null || true
    fi
    
    echo -e "${BLUE}====================================================${NC}"
    echo -e "${GREEN}Configuração do cliente concluída!${NC}"
    echo -e "${YELLOW}Para desconectar: sudo tailscale down${NC}"
    echo -e "${YELLOW}Para reconectar: sudo tailscale up${NC}"
}

# --- FUNÇÃO DE TROUBLESHOOTING ---
troubleshoot() {
    header
    echo -e "${YELLOW}=== TROUBLESHOOTING AVANÇADO ===${NC}"
    echo "1) Verificar status do servidor"
    echo "2) Verificar logs do servidor"
    echo "3) Testar conexão externa"
    echo "4) Recriar chaves de acesso"
    echo "5) Voltar ao menu principal"
    read -p "Escolha uma opção: " TROUBLE_OPT
    
    case $TROUBLE_OPT in
        1)
            if [ -d ~/headscale-server ]; then
                cd ~/headscale-server
                docker-compose ps
                docker-compose logs --tail=20
            else
                echo -e "${RED}Diretório do servidor não encontrado${NC}"
            fi
            ;;
        2)
            if [ -d ~/headscale-server ]; then
                cd ~/headscale-server
                echo -e "${CYAN}=== LOGS DO HEADSCALE ===${NC}"
                docker-compose logs headscale --tail=50
                echo -e "${CYAN}=== LOGS DO CADDY ===${NC}"
                docker-compose logs caddy --tail=50
            fi
            ;;
        3)
            read -p "Domínio para testar: " TEST_DOMAIN
            echo -e "${YELLOW}Testando $TEST_DOMAIN...${NC}"
            curl -v --connect-timeout 10 "http://$TEST_DOMAIN/health" || \
            curl -v --connect-timeout 10 "http://$TEST_DOMAIN" || \
            echo -e "${RED}Falha na conexão${NC}"
            ;;
        4)
            if [ -d ~/headscale-server ]; then
                cd ~/headscale-server
                echo -e "${YELLOW}Recriando chaves...${NC}"
                docker exec headscale headscale preauthkeys list --user amigos
                read -p "Deseja criar nova chave? (s/N): " -n 1 -r
                echo
                if [[ $REPLY =~ ^[Ss]$ ]]; then
                    NEW_KEY=$(docker exec headscale headscale preauthkeys create --user amigos --reusable --expiration 168h)
                    echo -e "${GREEN}Nova chave: $NEW_KEY${NC}"
                fi
            fi
            ;;
    esac
    
    read -p "Pressione Enter para continuar..."
    main_menu
}

# --- MENU PRINCIPAL ---
main_menu() {
    header
    check_deps
    echo "Selecione uma opção:"
    echo "1) Ser o HOST (Criar e Gerenciar a Rede)"
    echo "2) Ser o GUEST (Entrar na rede de um amigo)"
    echo "3) Troubleshooting"
    echo "4) Sair"
    read -p "Opção: " OPT

    case $OPT in
        1) setup_host ;;
        2) setup_guest ;;
        3) troubleshoot ;;
        *) exit 0 ;;
    esac
}

# Executar menu principal
main_menu
