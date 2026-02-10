#!/bin/bash

SERVICE_NAME="sunshine"
CONFIG_DIR="$HOME/.config/big-remoteplay"

start_sunshine() {
    echo "🚀 Iniciando Sunshine..."
    
    if pgrep -x sunshine > /dev/null; then
        echo "⚠️  Sunshine já está em execução"
        return 1
    fi
    
    if [ -f "$CONFIG_DIR/sunshine/sunshine.conf" ]; then
        sunshine --config "$CONFIG_DIR/sunshine/sunshine.conf" &
        echo "✅ Sunshine iniciado (PID: $!)"
        echo $! > "$CONFIG_DIR/sunshine.pid"
    else
        echo "❌ Arquivo de configuração não encontrado"
        echo "   Execute: big-remoteplay-configure.sh"
        return 1
    fi
}

stop_sunshine() {
    echo "🛑 Parando Sunshine..."
    
    if [ -f "$CONFIG_DIR/sunshine.pid" ]; then
        PID=$(cat "$CONFIG_DIR/sunshine.pid")
        if kill -0 "$PID" 2>/dev/null; then
            kill "$PID"
            rm "$CONFIG_DIR/sunshine.pid"
            echo "✅ Sunshine parado"
        else
            echo "⚠️  Processo não encontrado"
            rm "$CONFIG_DIR/sunshine.pid"
        fi
    else
        pkill -x sunshine
        echo "✅ Sunshine parado (fallback)"
    fi
}

status_sunshine() {
    if pgrep -x sunshine > /dev/null; then
        PID=$(pgrep -x sunshine)
        echo "✅ Sunshine está ATIVO (PID: $PID)"
        
        if command -v ss &> /dev/null; then
            echo ""
            echo "📡 Portas em uso:"
            ss -tulpn 2>/dev/null | grep sunshine || echo "   (sem informações disponíveis)"
        fi
        return 0
    else
        echo "❌ Sunshine está INATIVO"
        return 1
    fi
}

restart_sunshine() {
    echo "🔄 Reiniciando Sunshine..."
    stop_sunshine
    sleep 2
    start_sunshine
}

enable_autostart() {
    echo "⚙️  Habilitando início automático..."
    
    mkdir -p "$HOME/.config/systemd/user"
    
    cat > "$HOME/.config/systemd/user/big-remoteplay-sunshine.service" << EOF
[Unit]
Description=Big Remote Play Together - Sunshine Service
After=network-online.target

[Service]
Type=simple
ExecStart=/usr/bin/sunshine --config $CONFIG_DIR/sunshine/sunshine.conf
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
EOF

    systemctl --user daemon-reload
    systemctl --user enable big-remoteplay-sunshine.service
    
    echo "✅ Serviço habilitado"
    echo "   Inicie manualmente: systemctl --user start big-remoteplay-sunshine"
}

disable_autostart() {
    echo "🚫 Desabilitando início automático..."
    
    systemctl --user disable big-remoteplay-sunshine.service 2>/dev/null || true
    systemctl --user stop big-remoteplay-sunshine.service 2>/dev/null || true
    
    echo "✅ Serviço desabilitado"
}

show_usage() {
    cat << EOF
Big Remote Play Together - Gerenciador de Serviços

Uso: $0 [COMANDO]

Comandos:
  start       Inicia o Sunshine
  stop        Para o Sunshine
  restart     Reinicia o Sunshine
  status      Mostra status do Sunshine
  enable      Habilita início automático
  disable     Desabilita início automático
  help        Mostra esta ajuda

Exemplos:
  $0 start
  $0 status
  $0 enable
EOF
}

case "${1:-help}" in
    start)
        start_sunshine
        ;;
    stop)
        stop_sunshine
        ;;
    restart)
        restart_sunshine
        ;;
    status)
        status_sunshine
        ;;
    enable)
        enable_autostart
        ;;
    disable)
        disable_autostart
        ;;
    help|--help|-h)
        show_usage
        ;;
    *)
        echo "❌ Comando desconhecido: $1"
        echo ""
        show_usage
        exit 1
        ;;
esac
