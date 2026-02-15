#!/bin/bash

CONFIG_DIR="$HOME/.config/big-remoteplay"
SUNSHINE_CONFIG="$CONFIG_DIR/sunshine"
MOONLIGHT_CONFIG="$CONFIG_DIR/moonlight"

mkdir -p "$CONFIG_DIR" "$SUNSHINE_CONFIG" "$MOONLIGHT_CONFIG"

configure_sunshine() {
    echo "🎮 Configurando Sunshine (Host)..."
    
    cat > "$SUNSHINE_CONFIG/sunshine.conf" << EOF
# Big Remote Play - Sunshine Configuration
sunshine_name = $(hostname)-remoteplay
min_log_level = info
channels = 5

# Streaming
fps = [10, 30, 60, 90, 120]
resolutions = [
    352x240,
    480x360,
    858x480,
    1280x720,
    1920x1080,
    2560x1440,
    3840x2160
]
enc_bitrate = 20000
min_bitrate = 5000

# Network
upnp = true
port = 47989
pkey = $SUNSHINE_CONFIG/key.pem
cert = $SUNSHINE_CONFIG/cert.pem
file_state = $SUNSHINE_CONFIG/state.json

# Audio
audio_sink = auto
virtual_sink = big-remoteplay-sink

# Input
gamepad = auto
ds4_back_as_touchpad_click = enabled

# Advanced
min_threads = 2
capture = kms
adapter_name = auto
output_name = auto
EOF

    if [ ! -f "$SUNSHINE_CONFIG/apps.json" ]; then
        cat > "$SUNSHINE_CONFIG/apps.json" << 'EOF'
{
  "env": {},
  "apps": [
    {
      "name": "Desktop",
      "output": "",
      "cmd": "",
      "prep-cmd": [],
      "detached": []
    }
  ]
}
EOF
    fi

    echo "✅ Sunshine configurado em: $SUNSHINE_CONFIG"
}

configure_moonlight() {
    echo "🌙 Configurando Moonlight (Guest)..."
    
    mkdir -p "$MOONLIGHT_CONFIG"
    
    cat > "$MOONLIGHT_CONFIG/moonlight.conf" << EOF
# Big Remote Play - Moonlight Configuration

# Video
resolution = 1920x1080
fps = 60
bitrate = 20000
codec = auto
vdec = auto

# Audio
audio = true
audio_device = auto
surround = 5.1

# Input
input = auto
gamepad = auto

# Network
remote = false
lan_optimize = true

# Display
fullscreen = false
vsync = true
framepacing = true

# Advanced
quit_after = true
viewonly = false
EOF

    echo "✅ Moonlight configurado em: $MOONLIGHT_CONFIG"
}

configure_network() {
    echo "🌐 Configurando rede..."
    
    cat > "$CONFIG_DIR/network.conf" << EOF
# Network Configuration
UPNP_ENABLED=true
IPV6_ENABLED=true

# Sunshine Ports
SUNSHINE_CONTROL_PORT=47989
SUNSHINE_STREAM_PORT_START=47984
SUNSHINE_STREAM_PORT_END=47990
SUNSHINE_VIDEO_PORT=48010

# Firewall Zone
FIREWALL_ZONE=trusted
AUTO_CONFIGURE_FIREWALL=true
EOF

    echo "✅ Configuração de rede salva"
}

configure_games() {
    echo "🎯 Criando perfis de jogos..."
    
    mkdir -p "$CONFIG_DIR/games"
    
    cat > "$CONFIG_DIR/games/template.json" << 'EOF'
{
  "name": "Game Template",
  "executable": "",
  "args": [],
  "working_dir": "",
  "env": {},
  "max_players": 2,
  "split_screen": false,
  "prep_commands": [],
  "post_commands": []
}
EOF

    echo "✅ Template de jogos criado"
}

show_summary() {
    echo ""
    echo "════════════════════════════════════════════════"
    echo "✅ Configuração concluída!"
    echo "════════════════════════════════════════════════"
    echo ""
    echo "📂 Arquivos de configuração:"
    echo "   Principal: $CONFIG_DIR"
    echo "   Sunshine:  $SUNSHINE_CONFIG"
    echo "   Moonlight: $MOONLIGHT_CONFIG"
    echo ""
    echo "⚙️  Para editar manualmente:"
    echo "   Sunshine:  $SUNSHINE_CONFIG/sunshine.conf"
    echo "   Moonlight: $MOONLIGHT_CONFIG/moonlight.conf"
    echo "   Rede:      $CONFIG_DIR/network.conf"
    echo ""
}

main() {
    echo "╔════════════════════════════════════════════════╗"
    echo "║   Big Remote Play - Configuração      ║"
    echo "╚════════════════════════════════════════════════╝"
    echo ""
    
    configure_sunshine
    configure_moonlight
    configure_network
    configure_games
    show_summary
}

main "$@"
