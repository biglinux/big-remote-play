#!/bin/bash

# Tailscale Network Manager Script
# Versão: 1.0
# Desenvolvido para BigLinux/Manjaro

# Cores para melhor visualização
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
PURPLE='\033[0;35m'
CYAN='\033[0;36m'
WHITE='\033[1;37m'
NC='\033[0m' # No Color

# Variáveis globais
TAILSCALE_CONFIG="$HOME/.tailscale-script"
AUTH_KEY_FILE="$TAILSCALE_CONFIG/auth_keys.txt"
ACCOUNTS_FILE="$TAILSCALE_CONFIG/accounts.txt"

# Função para verificar dependências
check_dependencies() {
    echo -e "${YELLOW}Verificando dependências...${NC}"

    # Verificar se o Tailscale está instalado
    if ! command -v tailscale &> /dev/null; then
        echo -e "${RED}Tailscale não encontrado. Instalando...${NC}"

        # Adicionar repositório AUR (yay necessário)
        if ! command -v yay &> /dev/null; then
            echo -e "${YELLOW}Instalando yay (AUR helper)...${NC}"
            sudo pacman -S --needed git base-devel --noconfirm
            git clone https://aur.archlinux.org/yay.git /tmp/yay
            cd /tmp/yay || exit 1
            makepkg -si --noconfirm
            cd - || exit 1
        fi

        # Instalar tailscale do AUR
        yay -S tailscale-bin --noconfirm

        # Habilitar e iniciar serviço
        sudo systemctl enable tailscaled
        sudo systemctl start tailscaled
    fi

    # Verificar se o jq está instalado
    if ! command -v jq &> /dev/null; then
        echo -e "${RED}jq não encontrado. Instalando...${NC}"
        sudo pacman -S jq --noconfirm
    fi

    # Verificar se o curl está instalado
    if ! command -v curl &> /dev/null; then
        echo -e "${RED}curl não encontrado. Instalando...${NC}"
        sudo pacman -S curl --noconfirm
    fi

    # Criar diretório de configuração
    mkdir -p "$TAILSCALE_CONFIG"
}

# Função para fazer login no Tailscale
login_tailscale() {
    echo -e "${BLUE}=== LOGIN NO TAILSCALE ===${NC}"

    # Verificar e iniciar o serviço primeiro
    echo -e "${YELLOW}Verificando serviço tailscaled...${NC}"

    if ! systemctl is-active --quiet tailscaled; then
        echo -e "${YELLOW}Serviço tailscaled não está rodando. Iniciando...${NC}"
        sudo systemctl start tailscaled
        sleep 3

        if ! systemctl is-active --quiet tailscaled; then
            echo -e "${RED}✗ Falha ao iniciar tailscaled. Verifique manualmente:${NC}"
            echo "sudo systemctl status tailscaled"
            echo "sudo journalctl -u tailscaled -f"
            return 1
        fi

        sudo systemctl enable tailscaled
    fi

    echo -e "${GREEN}✓ Serviço tailscaled está rodando${NC}"
    sleep 2

    echo -e "${YELLOW}Escolha o método de login:${NC}"
    echo "1) Login via browser (recomendado)"
    echo "2) Login com chave de autenticação"
    echo "3) Voltar"

    read -r LOGIN_OPTION

    case $LOGIN_OPTION in
        1)
            echo -e "${GREEN}Iniciando login via browser...${NC}"
            echo -e "${YELLOW}Uma URL será aberta no seu navegador. Faça login com sua conta.${NC}"

            if sudo tailscale up --reset 2>&1 | grep -q "https://"; then
                echo -e "${GREEN}URL de login gerada. Siga as instruções no navegador.${NC}"
            else
                sudo tailscale login
            fi

            echo -e "${YELLOW}Aguardando autenticação...${NC}"
            for i in {1..15}; do
                sleep 2
                if sudo tailscale status &>/dev/null; then
                    echo -e "${GREEN}✓ Login confirmado!${NC}"
                    break
                fi
                echo -n "."
            done
            echo ""
            ;;
        2)
            echo -e "${CYAN}Digite sua chave de autenticação:${NC}"
            echo -e "${YELLOW}(Formato esperado: tskey-auth-xxxxxx-yyyyyy)${NC}"
            read -r AUTH_KEY

            if [ -n "$AUTH_KEY" ]; then
                echo -e "${YELLOW}Autenticando com chave...${NC}"

                if ! sudo tailscale status &>/dev/null; then
                    echo -e "${YELLOW}Serviço não responde. Tentando reconectar...${NC}"
                    sudo systemctl restart tailscaled
                    sleep 3
                fi

                OUTPUT=$(sudo tailscale up --reset --auth-key="$AUTH_KEY" 2>&1)
                EXIT_CODE=$?

                if [ $EXIT_CODE -ne 0 ]; then
                    echo -e "${YELLOW}Primeira tentativa falhou. Tentando método alternativo...${NC}"

                    if echo "$OUTPUT" | grep -q "interactive"; then
                        sudo tailscale up --auth-key="$AUTH_KEY"
                    elif echo "$OUTPUT" | grep -q "unauthenticated"; then
                        sudo tailscale login --auth-key="$AUTH_KEY"
                    else
                        echo -e "${YELLOW}Reiniciando serviço e tentando novamente...${NC}"
                        sudo systemctl stop tailscaled
                        sleep 2
                        sudo systemctl start tailscaled
                        sleep 3
                        sudo tailscale up --auth-key="$AUTH_KEY"
                    fi
                fi

                if sudo tailscale status &>/dev/null; then
                    echo -e "${GREEN}✓ Login realizado com sucesso!${NC}"
                    echo "$(date): $AUTH_KEY" >> "$AUTH_KEY_FILE"
                else
                    echo -e "${RED}✗ Erro ao fazer login. Diagnóstico:${NC}"
                    echo "1. Verifique se a chave é válida (não expirada)"
                    echo "2. Verifique a conectividade: ping 8.8.8.8"
                    echo "3. Verifique o serviço: sudo journalctl -u tailscaled -n 20"
                    echo ""
                    echo -e "${YELLOW}Comando manual alternativo:${NC}"
                    echo "sudo tailscale up --auth-key=\"$AUTH_KEY\" --reset"
                fi
            else
                echo -e "${RED}Chave não pode estar vazia!${NC}"
                return 1
            fi
            ;;
        3)
            return
            ;;
        *)
            echo -e "${RED}Opção inválida!${NC}"
            ;;
    esac

    # Verificação de sucesso
    echo -e "${YELLOW}Verificando conexão...${NC}"

    MAX_RETRIES=8
    RETRY_COUNT=0
    LOGIN_SUCCESS=false

    while [ $RETRY_COUNT -lt $MAX_RETRIES ]; do
        if sudo tailscale status &>/dev/null; then
            LOGIN_SUCCESS=true
            break
        fi
        echo -e "${YELLOW}Aguardando conexão... (tentativa $((RETRY_COUNT+1))/$MAX_RETRIES)${NC}"
        sleep 3
        RETRY_COUNT=$((RETRY_COUNT + 1))
    done

    if [ "$LOGIN_SUCCESS" = true ]; then
        echo -e "${GREEN}✓ Login realizado e conexão estabelecida com sucesso!${NC}"
        echo ""
        echo -e "${CYAN}Informações do dispositivo:${NC}"

        if command -v jq &> /dev/null; then
            DEVICE_NAME=$(tailscale status --json 2>/dev/null | jq -r '.Self.DNSName' 2>/dev/null | sed 's/\.$//')
        fi

        if [ -z "$DEVICE_NAME" ]; then
            DEVICE_NAME=$(tailscale status 2>/dev/null | head -1 | awk '{print $2}')
        fi

        [ -n "$DEVICE_NAME" ] && echo -e "Nome: ${GREEN}$DEVICE_NAME${NC}"

        IPV4=$(tailscale ip -4 2>/dev/null)
        IPV6=$(tailscale ip -6 2>/dev/null)

        [ -n "$IPV4" ] && echo -e "IPv4: ${GREEN}$IPV4${NC}"
        [ -n "$IPV6" ] && echo -e "IPv6: ${GREEN}$IPV6${NC}"

        echo ""
        echo -e "${CYAN}Dispositivos na rede:${NC}"
        tailscale status 2>/dev/null | head -5 || echo "Nenhum dispositivo encontrado"

        if [ -n "$DEVICE_NAME" ] && [ -n "$IPV4" ]; then
            echo "$DEVICE_NAME:$IPV4:$(date)" >> "$ACCOUNTS_FILE"
        fi
    else
        echo -e "${RED}✗ Falha na conexão. Diagnóstico completo:${NC}"
        echo ""
        echo -e "${YELLOW}1. Status do serviço:${NC}"
        systemctl status tailscaled --no-pager | head -3

        echo -e "${YELLOW}2. Logs recentes:${NC}"
        sudo journalctl -u tailscaled -n 5 --no-pager

        echo -e "${YELLOW}3. Conectividade:${NC}"
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
        echo -e "${CYAN}Após executar os comandos acima, tente fazer login novamente.${NC}"
    fi
}

# Função para criar nova rede/conta
create_network() {
    echo -e "${BLUE}=== CRIAR NOVA REDE TAILSCALE ===${NC}"
    echo -e "${YELLOW}Nota: No Tailscale, 'redes' são contas/orgs diferentes.${NC}"
    echo -e "${YELLOW}Você precisa de uma conta diferente para cada rede.${NC}"

    echo -e "${CYAN}Nome da rede/empresa:${NC}"
    read -r NETWORK_NAME

    if [ -z "$NETWORK_NAME" ]; then
        echo -e "${RED}Nome não pode estar vazio!${NC}"
        return 1
    fi

    echo -e "${YELLOW}Para criar uma nova rede:${NC}"
    echo "1. Acesse https://login.tailscale.com"
    echo "2. Crie uma nova conta com um email diferente"
    echo "3. Ou use um domínio diferente para criar um org separado"
    echo ""
    echo -e "${CYAN}Deseja fazer logout e login com nova conta? (s/N):${NC}"
    read -r CONFIRM

    if [[ "$CONFIRM" =~ ^[Ss]$ ]]; then
        logout_tailscale
        login_tailscale
    fi
}

# Função para listar redes/dispositivos
list_networks() {
    echo -e "${BLUE}=== STATUS DA REDE TAILSCALE ===${NC}"

    if ! sudo tailscale status &>/dev/null; then
        echo -e "${RED}Não conectado ao Tailscale. Faça login primeiro.${NC}"
        return 1
    fi

    echo -e "${GREEN}Status atual:${NC}"
    sudo tailscale status

    echo ""
    echo -e "${YELLOW}Endereços IP:${NC}"
    sudo tailscale ip

    echo ""
    if command -v jq &> /dev/null; then
        echo -e "${CYAN}Informações detalhadas:${NC}"
        sudo tailscale status --json | jq '.Self' 2>/dev/null || echo "Não foi possível obter detalhes"
    fi
}

# Função para listar todos os dispositivos
list_devices() {
    echo -e "${BLUE}=== DISPOSITIVOS NA REDE ===${NC}"

    if ! sudo tailscale status &>/dev/null; then
        echo -e "${RED}Não conectado ao Tailscale. Faça login primeiro.${NC}"
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
        echo -e "${YELLOW}Apenas este dispositivo conectado à rede${NC}"
        THIS_DEVICE=$(sudo tailscale status | head -1)
        echo -e "${CYAN}→ $THIS_DEVICE${NC}"
    fi
}

# Função para adicionar novo dispositivo
add_device() {
    echo -e "${BLUE}=== ADICIONAR NOVO DISPOSITIVO ===${NC}"
    echo ""
    echo -e "${YELLOW}Método 1: URL de convite${NC}"
    echo "1. Acesse https://login.tailscale.com/admin/invite"
    echo "2. Gere um link de convite"
    echo "3. Execute no novo dispositivo: curl -fsSL <LINK> | sh"
    echo ""
    echo -e "${YELLOW}Método 2: Chave de autenticação${NC}"
    echo "1. Acesse https://login.tailscale.com/admin/settings/keys"
    echo "2. Gere uma chave de autenticação"
    echo "3. No novo dispositivo: sudo tailscale up --auth-key=<CHAVE>"
    echo ""
    echo -e "${YELLOW}Método 3: Login com mesma conta${NC}"
    echo "Basta fazer login com a mesma conta no novo dispositivo"
    echo ""
    echo -e "${CYAN}Pressione ENTER para voltar ao menu principal${NC}"
    read -r
}

# Função para remover dispositivo
remove_device() {
    echo -e "${BLUE}=== REMOVER DISPOSITIVO ===${NC}"
    echo -e "${RED}CUIDADO: Esta ação removerá o dispositivo da rede!${NC}"

    list_devices

    echo -e "${CYAN}Digite o nome do dispositivo para remover:${NC}"
    read -r DEVICE_NAME

    if [ -z "$DEVICE_NAME" ]; then
        echo -e "${RED}Nome não pode estar vazio!${NC}"
        return 1
    fi

    echo -e "${YELLOW}Tem certeza que deseja remover '$DEVICE_NAME'? (s/N):${NC}"
    read -r CONFIRM

    if [[ "$CONFIRM" =~ ^[Ss]$ ]]; then
        echo -e "${YELLOW}Para remover via API, você precisa:${NC}"
        echo "1. Acesse https://login.tailscale.com/admin/machines"
        echo "2. Encontre o dispositivo '$DEVICE_NAME'"
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

# Função para autorizar dispositivo
authorize_device() {
    echo -e "${BLUE}=== AUTORIZAR DISPOSITIVO ===${NC}"

    if ! sudo tailscale status &>/dev/null; then
        echo -e "${RED}Não conectado ao Tailscale. Faça login primeiro.${NC}"
        return 1
    fi

    echo -e "${YELLOW}Verificando dispositivos pendentes...${NC}"

    if command -v jq &> /dev/null; then
        PENDING_DEVICES=$(sudo tailscale status --json 2>/dev/null | jq -r '.Peer[] | select(.Authorized == false) | "\(.DNSName) (pendente)"' 2>/dev/null)

        if [ -n "$PENDING_DEVICES" ]; then
            echo -e "${GREEN}Dispositivos pendentes encontrados:${NC}"
            echo "$PENDING_DEVICES"
        else
            echo -e "${YELLOW}Nenhum dispositivo pendente encontrado${NC}"
        fi
    else
        echo -e "${YELLOW}Não foi possível verificar dispositivos pendentes (jq não instalado)${NC}"
    fi

    echo ""
    echo -e "${YELLOW}Para autorizar dispositivos manualmente:${NC}"
    echo "1. Acesse https://login.tailscale.com/admin/machines"
    echo "2. Procure por dispositivos com status 'Pending'"
    echo "3. Clique em 'Approve' ou 'Authorize'"
    echo ""
    echo -e "${CYAN}Pressione ENTER para continuar${NC}"
    read -r
}

# Função para compartilhar rede
share_network() {
    echo -e "${BLUE}=== COMPARTILHAR REDE ===${NC}"
    echo -e "${YELLOW}Opções de compartilhamento:${NC}"
    echo "1) Compartilhar com usuário específico"
    echo "2) Criar link de convite"
    echo "3) Voltar"

    read -r SHARE_OPTION

    case $SHARE_OPTION in
        1)
            echo -e "${CYAN}Digite o email do usuário:${NC}"
            read -r USER_EMAIL

            if [ -n "$USER_EMAIL" ]; then
                echo -e "${GREEN}Para compartilhar com $USER_EMAIL:${NC}"
                echo "1. Acesse https://login.tailscale.com/admin/users"
                echo "2. Clique em 'Invite user'"
                echo "3. Digite o email e selecione as permissões"
            fi
            ;;
        2)
            echo -e "${GREEN}Criando link de convite:${NC}"
            echo "1. Acesse https://login.tailscale.com/admin/invite"
            echo "2. Configure as opções desejadas"
            echo "3. Compartilhe o link gerado"
            ;;
        3)
            return
            ;;
        *)
            echo -e "${RED}Opção inválida!${NC}"
            ;;
    esac

    echo ""
    echo -e "${CYAN}Pressione ENTER para continuar${NC}"
    read -r
}

# Função para configurar ACLs
configure_acl() {
    echo -e "${BLUE}=== CONFIGURAR ACLs ===${NC}"
    echo -e "${YELLOW}ACLs controlam o acesso entre dispositivos.${NC}"
    echo ""
    echo "Para configurar ACLs:"
    echo "1. Acesse https://login.tailscale.com/admin/acls"
    echo "2. Edite o arquivo JSON de ACLs"
    echo ""
    echo "Exemplo básico:"
    cat <<'EOF'
{
  "acls": [
    {"action": "accept", "src": ["*"], "dst": ["*:*"]}
  ]
}
EOF
    echo ""
    echo -e "${CYAN}Deseja abrir o painel de ACLs no navegador? (s/N):${NC}"
    read -r OPEN_BROWSER

    if [[ "$OPEN_BROWSER" =~ ^[Ss]$ ]]; then
        xdg-open "https://login.tailscale.com/admin/acls" 2>/dev/null || \
            echo -e "${RED}Não foi possível abrir o navegador. Acesse manualmente: https://login.tailscale.com/admin/acls${NC}"
    fi
}

# Função para logout
logout_tailscale() {
    echo -e "${BLUE}=== SAIR DO TAILSCALE ===${NC}"
    echo -e "${YELLOW}Você realmente deseja sair do Tailscale? (s/N):${NC}"
    read -r CONFIRM

    if [[ "$CONFIRM" =~ ^[Ss]$ ]]; then
        sudo tailscale logout
        echo -e "${GREEN}Logout realizado com sucesso!${NC}"
    else
        echo -e "${YELLOW}Operação cancelada.${NC}"
    fi
}

# Função para mostrar status detalhado
show_status() {
    echo -e "${BLUE}=== STATUS DETALHADO DO TAILSCALE ===${NC}"

    echo -e "${YELLOW}Status do serviço:${NC}"
    systemctl status tailscaled --no-pager | grep "Active:"

    echo ""
    echo -e "${YELLOW}Versão:${NC}"
    tailscale version

    echo ""
    if sudo tailscale status &>/dev/null; then
        echo -e "${GREEN}✓ Conectado ao Tailscale${NC}"
        echo ""
        echo -e "${YELLOW}Informações do nó:${NC}"
        if command -v jq &> /dev/null; then
            sudo tailscale status --json | jq '.Self | {Name: .DNSName, IPs: .Addresses, Online: .Online}' 2>/dev/null
        else
            sudo tailscale status | head -1
        fi

        echo ""
        echo -e "${YELLOW}Estatísticas:${NC}"
        TOTAL_PEERS=$(sudo tailscale status | wc -l)
        TOTAL_PEERS=$((TOTAL_PEERS - 1))
        echo "Total de peers: $TOTAL_PEERS"

        echo ""
        echo -e "${YELLOW}Rotas:${NC}"
        ip route show | grep tailscale || echo "Nenhuma rota tailscale específica"
    else
        echo -e "${RED}✗ Desconectado do Tailscale${NC}"
    fi
}

# Função para alterar configurações
change_settings() {
    echo -e "${BLUE}=== ALTERAR CONFIGURAÇÕES ===${NC}"
    echo -e "${YELLOW}Opções de configuração:${NC}"
    echo "1) Ativar/Desativar roteamento de sub-redes"
    echo "2) Ativar/Desativar modo exit node"
    echo "3) Mudar nome do dispositivo"
    echo "4) Configurar servidor DNS"
    echo "5) Voltar"

    read -r SETTINGS_OPTION

    case $SETTINGS_OPTION in
        1)
            echo -e "${CYAN}Digite as sub-redes para rotear (ex: 192.168.1.0/24):${NC}"
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
            echo -e "${CYAN}Novo nome para o dispositivo:${NC}"
            read -r NEW_NAME
            if [ -n "$NEW_NAME" ]; then
                sudo tailscale set --hostname="$NEW_NAME"
                echo -e "${GREEN}Nome alterado para $NEW_NAME${NC}"
            fi
            ;;
        4)
            echo -e "${CYAN}Digite os servidores DNS (separados por vírgula):${NC}"
            read -r DNS_SERVERS
            sudo tailscale up --dns="$DNS_SERVERS"
            echo -e "${GREEN}DNS configurado!${NC}"
            ;;
        5)
            return
            ;;
        *)
            echo -e "${RED}Opção inválida!${NC}"
            ;;
    esac

    echo ""
    echo -e "${CYAN}Pressione ENTER para continuar${NC}"
    read -r
}

# Função para backup das configurações
backup_config() {
    echo -e "${BLUE}=== BACKUP DAS CONFIGURAÇÕES ===${NC}"

    BACKUP_FILE="$HOME/tailscale-backup-$(date +%Y%m%d-%H%M%S).tar.gz"
    TEMP_DIR="/tmp/tailscale-backup-$$"

    mkdir -p "$TEMP_DIR"

    if [ -d "$TAILSCALE_CONFIG" ]; then
        cp -r "$TAILSCALE_CONFIG" "$TEMP_DIR/script-config" 2>/dev/null
        echo -e "${GREEN}✓ Configurações do script salvas${NC}"
    fi

    if [ -d "/var/lib/tailscale" ]; then
        sudo tar -czf "$TEMP_DIR/tailscale-system.tar.gz" -C /var/lib tailscale 2>/dev/null
        sudo chown "$USER:$USER" "$TEMP_DIR/tailscale-system.tar.gz"
        echo -e "${GREEN}✓ Configurações do Tailscale salvas${NC}"
    fi

    tar -czf "$BACKUP_FILE" -C "$TEMP_DIR" . 2>/dev/null
    rm -rf "$TEMP_DIR"

    echo -e "${GREEN}✓ Backup criado: $BACKUP_FILE${NC}"
    echo -e "${YELLOW}Tamanho: $(du -h "$BACKUP_FILE" | cut -f1)${NC}"

    echo -e "${CYAN}Verificando integridade do backup...${NC}"
    if tar -tzf "$BACKUP_FILE" &>/dev/null; then
        echo -e "${GREEN}✓ Backup íntegro${NC}"
    else
        echo -e "${RED}✗ Backup corrompido${NC}"
    fi

    echo ""
    echo -e "${CYAN}Pressione ENTER para continuar${NC}"
    read -r
}

# Função para mostrar menu
show_menu() {
    clear
    echo -e "${BLUE}====================================${NC}"
    echo -e "${GREEN}    TAILSCALE NETWORK MANAGER v1.0  ${NC}"
    echo -e "${BLUE}====================================${NC}"
    echo -e "${WHITE}Bem-vindo ao gerenciador Tailscale${NC}"
    echo -e "${BLUE}====================================${NC}"
    echo ""
    echo -e "${YELLOW}1)${NC} Login/Fazer login"
    echo -e "${YELLOW}2)${NC} Criar nova rede/conta"
    echo -e "${YELLOW}3)${NC} Ver status da rede"
    echo -e "${YELLOW}4)${NC} Listar dispositivos"
    echo -e "${YELLOW}5)${NC} Adicionar novo dispositivo (instruções)"
    echo -e "${YELLOW}6)${NC} Remover dispositivo"
    echo -e "${YELLOW}7)${NC} Autorizar dispositivo pendente"
    echo -e "${YELLOW}8)${NC} Compartilhar rede"
    echo -e "${YELLOW}9)${NC} Configurar ACLs"
    echo -e "${YELLOW}10)${NC} Alterar configurações"
    echo -e "${YELLOW}11)${NC} Status detalhado"
    echo -e "${YELLOW}12)${NC} Logout/Sair"
    echo -e "${YELLOW}13)${NC} Backup das configurações"
    echo -e "${YELLOW}0)${NC} Sair do programa"
    echo ""
    echo -e "${CYAN}Escolha uma opção:${NC}"
}

# Função principal
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
                echo -e "${GREEN}Saindo...${NC}"
                exit 0
                ;;
            *)
                echo -e "${RED}Opção inválida!${NC}"
                ;;
        esac

        echo ""
        echo -e "${YELLOW}Pressione ENTER para continuar...${NC}"
        read -r
    done
}

# Executar função principal
main
