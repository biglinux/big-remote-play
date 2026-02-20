#!/bin/bash
# ==============================================================================
# ZEROTIER NETWORK MANAGER - Big Remote Play
# Desenvolvido para BigLinux/Manjaro
# ==============================================================================

set -e

GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m'

ZEROTIER_CONFIG="$HOME/.config/big-remoteplay/zerotier"
API_TOKEN_FILE="$ZEROTIER_CONFIG/api_token.txt"
NETWORKS_FILE="$ZEROTIER_CONFIG/networks.txt"

header() {
    clear
    echo -e "${BLUE}====================================================${NC}"
    echo -e "${GREEN}         ZEROTIER - REDE PRIVADA VIRTUAL            ${NC}"
    echo -e "${BLUE}====================================================${NC}"
}

check_deps() {
    echo -e "${YELLOW}Verificando dependências...${NC}"

    if ! command -v zerotier-cli &> /dev/null; then
        echo -e "${YELLOW}Instalando ZeroTier...${NC}"
        sudo pacman -S zerotier-one --noconfirm
    else
        echo -e "${GREEN}✓ ZeroTier já instalado${NC}"
    fi

    if ! command -v jq &> /dev/null; then
        sudo pacman -S jq --noconfirm
    fi
    if ! command -v curl &> /dev/null; then
        sudo pacman -S curl --noconfirm
    fi

    if ! systemctl is-active --quiet zerotier-one 2>/dev/null; then
        echo -e "${YELLOW}Iniciando serviço ZeroTier...${NC}"
        sudo systemctl enable zerotier-one
        sudo systemctl start zerotier-one
        sleep 3
    else
        echo -e "${GREEN}✓ ZeroTier daemon ativo${NC}"
    fi

    mkdir -p "$ZEROTIER_CONFIG"
}

load_token() {
    if [ ! -f "$API_TOKEN_FILE" ]; then
        echo -e "${RED}Token da API não encontrado.${NC}"
        echo -e "${CYAN}1. Acesse https://my.zerotier.com${NC}"
        echo -e "${CYAN}2. Vá em Account → API Access Tokens${NC}"
        echo -e "${CYAN}3. Gere um novo token${NC}"
        echo ""
        read -p "Cole seu API Token aqui: " API_TOKEN
        if [ -z "$API_TOKEN" ]; then
            echo -e "${RED}Token não pode estar vazio!${NC}"
            exit 1
        fi
        echo "$API_TOKEN" > "$API_TOKEN_FILE"
        echo -e "${GREEN}✓ Token salvo!${NC}"
    fi
    API_TOKEN=$(cat "$API_TOKEN_FILE")
}

create_network() {
    header
    echo -e "${YELLOW}CRIAR NOVA REDE ZEROTIER${NC}"
    echo ""

    load_token

    read -p "Nome da rede: " NETWORK_NAME
    if [ -z "$NETWORK_NAME" ]; then
        echo -e "${RED}Nome não pode estar vazio!${NC}"
        exit 1
    fi

    read -p "Descrição (opcional): " NETWORK_DESC

    echo -e "${YELLOW}Criando rede via API...${NC}"
    RESPONSE=$(curl -s -X POST \
        -H "Authorization: bearer $API_TOKEN" \
        -H "Content-Type: application/json" \
        -d "{\"name\": \"$NETWORK_NAME\", \"description\": \"$NETWORK_DESC\", \"private\": true}" \
        "https://api.zerotier.com/api/v1/network")

    NETWORK_ID=$(echo "$RESPONSE" | jq -r '.id // empty')

    if [ -z "$NETWORK_ID" ] || [ "$NETWORK_ID" = "null" ]; then
        echo -e "${RED}Erro ao criar rede. Verifique seu token.${NC}"
        echo "$RESPONSE" | jq '.' 2>/dev/null || echo "$RESPONSE"
        exit 1
    fi

    echo -e "${GREEN}✅ Rede criada! ID: $NETWORK_ID${NC}"

    # Entrar na rede automaticamente
    echo -e "${YELLOW}Entrando na rede...${NC}"
    sudo zerotier-cli join "$NETWORK_ID" 2>/dev/null || true
    sleep 3

    # Obter Node ID e autorizar automaticamente
    NODE_ID=$(sudo zerotier-cli info 2>/dev/null | cut -d' ' -f3 || echo "")

    if [ -n "$NODE_ID" ]; then
        echo -e "${YELLOW}Autorizando dispositivo local...${NC}"
        curl -s -X POST \
            -H "Authorization: bearer $API_TOKEN" \
            -H "Content-Type: application/json" \
            -d '{"config": {"authorized": true}}' \
            "https://api.zerotier.com/api/v1/network/$NETWORK_ID/member/$NODE_ID" > /dev/null
        echo -e "${GREEN}✓ Dispositivo autorizado!${NC}"
    fi

    # Salvar localmente
    echo "$NETWORK_ID:$NETWORK_NAME:$(date +%Y-%m-%d)" >> "$NETWORKS_FILE"

    IP_LOCAL=$(ip route get 1 2>/dev/null | awk '{print $7;exit}' || echo "N/A")
    PUBLIC_IP=$(curl -s https://api.ipify.org 2>/dev/null || echo "N/A")

    echo ""
    echo -e "${CYAN}=== INFORMAÇÕES DA REDE ===${NC}"
    echo -e "Web Interface: ${YELLOW}https://my.zerotier.com/network/$NETWORK_ID${NC}"
    echo -e "API URL: ${CYAN}https://api.zerotier.com/api/v1${NC}"
    echo -e "Seu IP Público: ${GREEN}$PUBLIC_IP${NC}"
    echo -e "IP Local do Servidor: ${GREEN}$IP_LOCAL${NC}"
    echo ""
    echo -e "${CYAN}=== CREDENCIAIS ===${NC}"
    echo -e "API Key (para UI): ${CYAN}$API_TOKEN${NC}"
    echo -e "Chave para Amigos: ${GREEN}$NETWORK_ID${NC}"
    echo ""
    echo -e "${YELLOW}⚠️  Compartilhe o ID da rede ($NETWORK_ID) com seus amigos${NC}"
    echo -e "${YELLOW}Eles usam a opção 'Conectar' e informam o ID da rede${NC}"
    echo -e "${BLUE}====================================================${NC}"
}

join_network() {
    header
    echo -e "${YELLOW}ENTRAR EM REDE ZEROTIER (Cliente/Guest)${NC}"
    echo ""

    check_deps

    read -p "ID da Rede ZeroTier (16 caracteres): " NETWORK_ID
    if [ -z "$NETWORK_ID" ]; then
        echo -e "${RED}ID da rede não pode estar vazio!${NC}"
        exit 1
    fi

    echo -e "${YELLOW}Entrando na rede $NETWORK_ID...${NC}"
    sudo zerotier-cli join "$NETWORK_ID"
    sleep 5

    # Verificar status
    STATUS=$(sudo zerotier-cli listnetworks 2>/dev/null | grep "$NETWORK_ID" | awk '{print $6}' || echo "unknown")

    echo ""
    echo -e "${CYAN}=== STATUS DA CONEXÃO ===${NC}"
    sudo zerotier-cli listnetworks 2>/dev/null || true

    NODE_ID=$(sudo zerotier-cli info 2>/dev/null | cut -d' ' -f3 || echo "N/A")
    echo ""
    echo -e "Seu Node ID: ${GREEN}$NODE_ID${NC}"
    echo -e "${YELLOW}⚠️  Você precisa ser autorizado pelo administrador da rede!${NC}"
    echo -e "${CYAN}Informe ao administrador seu Node ID: ${GREEN}$NODE_ID${NC}"

    echo -e "${BLUE}====================================================${NC}"
    echo -e "${GREEN}Solicitação de entrada enviada!${NC}"
    echo -e "${YELLOW}Aguarde autorização do administrador${NC}"
}

# --- MENU PRINCIPAL ---
main_menu() {
    header
    check_deps
    echo "Selecione uma opção:"
    echo "1) Ser o HOST (Criar e Gerenciar a Rede)"
    echo "2) Ser o GUEST (Entrar na rede de um amigo)"
    echo "3) Ver status da rede"
    echo "4) Sair"
    read -p "Opção: " OPT

    case $OPT in
        1) create_network ;;
        2) join_network ;;
        3)
            echo -e "${CYAN}=== STATUS ZEROTIER ===${NC}"
            sudo zerotier-cli info 2>/dev/null || echo "ZeroTier não conectado"
            sudo zerotier-cli listnetworks 2>/dev/null || true
            ;;
        *) exit 0 ;;
    esac
}

main_menu
