#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
APP_NAME="big-remoteplay"
INSTALL_DIR="/opt/big-remoteplay-together"
BIN_DIR="/usr/local/bin"
DESKTOP_DIR="/usr/share/applications"
ICON_DIR="/usr/share/icons/hicolor"

echo "╔════════════════════════════════════════════════╗"
echo "║   Big Remote Play Together - Instalador        ║"
echo "║   Sistema de Jogo Cooperativo Remoto           ║"
echo "╚════════════════════════════════════════════════╝"
echo ""

if [ "$EUID" -ne 0 ]; then 
    echo "⚠️  Este script precisa de permissões de root."
    echo "   Execute: sudo $0"
    exit 1
fi

echo "📦 Verificando dependências..."

MISSING_DEPS=()

check_dependency() {
    if ! command -v "$1" &> /dev/null; then
        MISSING_DEPS+=("$2")
    fi
}

check_dependency "python3" "python"
check_dependency "glib-compile-schemas" "glib2"

if ! python3 -c "import gi" 2>/dev/null; then
    MISSING_DEPS+=("python-gobject")
fi

if ! python3 -c "import gi; gi.require_version('Gtk', '4.0')" 2>/dev/null; then
    MISSING_DEPS+=("gtk4")
fi

if ! python3 -c "import gi; gi.require_version('Adw', '1')" 2>/dev/null; then
    MISSING_DEPS+=("libadwaita")
fi

check_dependency "avahi-daemon" "avahi"
check_dependency "systemctl" "systemd"

if [ ${#MISSING_DEPS[@]} -ne 0 ]; then
    echo "❌ Dependências faltando: ${MISSING_DEPS[*]}"
    echo ""
    echo "Instalando dependências com pacman..."
    pacman -S --needed --noconfirm "${MISSING_DEPS[@]}"
fi

echo "✅ Todas as dependências básicas instaladas!"
echo ""

echo "🔍 Verificando Sunshine e Moonlight..."

if ! command -v sunshine &> /dev/null; then
    echo "⚠️  Sunshine não encontrado."
    echo "   Você pode instalar com: yay -S sunshine"
    read -p "   Deseja continuar sem Sunshine? (modo Host não funcionará) [s/N]: " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Ss]$ ]]; then
        exit 1
    fi
else
    echo "✅ Sunshine instalado"
fi

if ! command -v moonlight &> /dev/null && ! command -v moonlight-qt &> /dev/null; then
    echo "⚠️  Moonlight não encontrado."
    echo "   Você pode instalar com: yay -S moonlight-qt"
    read -p "   Deseja continuar sem Moonlight? (modo Guest não funcionará) [s/N]: " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Ss]$ ]]; then
        exit 1
    fi
else
    echo "✅ Moonlight instalado"
fi

echo ""
echo "📂 Criando estrutura de diretórios..."

mkdir -p "$INSTALL_DIR"/{bin,config,scripts,ui,docker,games,logs}
mkdir -p "$INSTALL_DIR/config"/{sunshine,moonlight}
mkdir -p ~/.config/big-remoteplay

echo "📋 Copiando arquivos..."

cp -r "$PROJECT_ROOT/src/"* "$INSTALL_DIR/"
cp -r "$PROJECT_ROOT/scripts/"* "$INSTALL_DIR/scripts/"
cp -r "$PROJECT_ROOT/data/"* "$INSTALL_DIR/" 2>/dev/null || true

if [ -d "$PROJECT_ROOT/docker" ]; then
    cp -r "$PROJECT_ROOT/docker/"* "$INSTALL_DIR/docker/" 2>/dev/null || true
fi

echo "🔗 Criando executável..."

cat > "$BIN_DIR/$APP_NAME" << 'EOF'
#!/bin/bash
cd /opt/big-remoteplay-together
exec python3 main.py "$@"
EOF

chmod +x "$BIN_DIR/$APP_NAME"

echo "🖼️  Instalando ícone e entrada desktop..."

if [ -f "$PROJECT_ROOT/data/icons/big-remoteplay.svg" ]; then
    mkdir -p "$ICON_DIR/scalable/apps"
    cp "$PROJECT_ROOT/data/icons/big-remoteplay.svg" "$ICON_DIR/scalable/apps/"
elif [ -f "$PROJECT_ROOT/data/icons/big-remoteplay.png" ]; then
    mkdir -p "$ICON_DIR/256x256/apps"
    cp "$PROJECT_ROOT/data/icons/big-remoteplay.png" "$ICON_DIR/256x256/apps/"
fi

cat > "$DESKTOP_DIR/$APP_NAME.desktop" << EOF
[Desktop Entry]
Type=Application
Name=Big Remote Play Together
GenericName=Remote Gaming
Comment=Jogue cooperativamente através da rede
Icon=big-remoteplay
Exec=$APP_NAME
Terminal=false
Categories=Game;Network;
Keywords=gaming;remote;streaming;sunshine;moonlight;
StartupNotify=true
EOF

chmod +x "$DESKTOP_DIR/$APP_NAME.desktop"

echo "🔒 Configurando firewall (opcional)..."

if command -v ufw &> /dev/null; then
    read -p "Configurar firewall (UFW) automaticamente? [S/n]: " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Nn]$ ]]; then
        ufw allow 47984:47990/tcp comment "Sunshine Control"
        ufw allow 48010/tcp comment "Sunshine Streaming"
        ufw allow 47998:48000/udp comment "Sunshine Streaming"
        echo "✅ Regras de firewall configuradas"
    fi
fi

echo "🌐 Habilitando serviço Avahi (descoberta de rede)..."
systemctl enable --now avahi-daemon 2>/dev/null || true

echo ""
echo "════════════════════════════════════════════════"
echo "✅ Instalação concluída com sucesso!"
echo "════════════════════════════════════════════════"
echo ""
echo "🚀 Para iniciar o aplicativo, execute:"
echo "   $APP_NAME"
echo ""
echo "Ou procure por 'Big Remote Play Together' no menu de aplicativos."
echo ""
echo "📚 Documentação: $PROJECT_ROOT/docs/"
echo "⚙️  Configurações: ~/.config/big-remoteplay/"
echo ""
echo "Divirta-se! 🎮✨"
