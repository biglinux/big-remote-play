#!/bin/bash

SUNSHINE_PORTS="47984:47990/tcp 48010/tcp 47998:48000/udp 48011/udp"

configure_ufw() {
    echo "🔥 Configurando UFW..."
    
    if ! command -v ufw &> /dev/null; then
        echo "❌ UFW não está instalado"
        return 1
    fi
    
    for port in $SUNSHINE_PORTS; do
        ufw allow $port comment "Big Remote Play - Sunshine"
    done
    
    # Permitir tráfego em interfaces virtuais (VPNs, ZeroTier)
    ufw allow in on tun+ comment "Allow VPN/Tunnel"
    ufw allow in on tap+ comment "Allow VPN/Tunnel"
    ufw allow in on zt+ comment "Allow ZeroTier"
    
    ufw reload 2>/dev/null || true
    echo "✅ Regras UFW configuradas"
}

configure_iptables() {
    echo "🔥 Configurando iptables..."
    
    if ! command -v iptables &> /dev/null; then
        echo "❌ iptables não está instalado"
        return 1
    fi
    
    iptables -A INPUT -p tcp --dport 47984:47990 -j ACCEPT -m comment --comment "Big Remote Play"
    iptables -A INPUT -p tcp --dport 48010 -j ACCEPT -m comment --comment "Big Remote Play"
    iptables -A INPUT -p udp --dport 47998:48000 -j ACCEPT -m comment --comment "Big Remote Play"
    iptables -A INPUT -p udp --dport 48011 -j ACCEPT -m comment --comment "Big Remote Play - PIN"
    
    ip6tables -A INPUT -p tcp --dport 47984:47990 -j ACCEPT -m comment --comment "Big Remote Play"
    ip6tables -A INPUT -p tcp --dport 48010 -j ACCEPT -m comment --comment "Big Remote Play"
    ip6tables -A INPUT -p udp --dport 47998:48000 -j ACCEPT -m comment --comment "Big Remote Play"
    ip6tables -A INPUT -p udp --dport 48011 -j ACCEPT -m comment --comment "Big Remote Play - PIN"
    
    # Permitir tráfego em interfaces virtuais (VPNs, ZeroTier)
    iptables -A INPUT -i tun+ -j ACCEPT -m comment --comment "Allow VPN/Tunnel"
    iptables -A INPUT -i tap+ -j ACCEPT -m comment --comment "Allow VPN/Tunnel"
    iptables -A INPUT -i zt+ -j ACCEPT -m comment --comment "Allow ZeroTier"
    
    ip6tables -A INPUT -i tun+ -j ACCEPT -m comment --comment "Allow VPN/Tunnel"
    ip6tables -A INPUT -i tap+ -j ACCEPT -m comment --comment "Allow VPN/Tunnel"
    ip6tables -A INPUT -i zt+ -j ACCEPT -m comment --comment "Allow ZeroTier"
    
    echo "✅ Regras iptables configuradas"
    echo "⚠️  Para tornar permanente, salve com: iptables-save > /etc/iptables/iptables.rules"
}

remove_ufw() {
    echo "🗑️  Removendo regras UFW..."
    
    for port in $SUNSHINE_PORTS; do
        ufw delete allow $port 2>/dev/null || true
    done
    
    ufw reload 2>/dev/null || true
    echo "✅ Regras UFW removidas"
}

remove_iptables() {
    echo "🗑️  Removendo regras iptables..."
    
    iptables -D INPUT -p tcp --dport 47984:47990 -j ACCEPT -m comment --comment "Big Remote Play" 2>/dev/null || true
    iptables -D INPUT -p tcp --dport 48010 -j ACCEPT -m comment --comment "Big Remote Play" 2>/dev/null || true
    iptables -D INPUT -p udp --dport 47998:48000 -j ACCEPT -m comment --comment "Big Remote Play" 2>/dev/null || true
    iptables -D INPUT -p udp --dport 48011 -j ACCEPT -m comment --comment "Big Remote Play - PIN" 2>/dev/null || true
    
    ip6tables -D INPUT -p tcp --dport 47984:47990 -j ACCEPT -m comment --comment "Big Remote Play" 2>/dev/null || true
    ip6tables -D INPUT -p tcp --dport 48010 -j ACCEPT -m comment --comment "Big Remote Play" 2>/dev/null || true
    ip6tables -D INPUT -p udp --dport 47998:48000 -j ACCEPT -m comment --comment "Big Remote Play" 2>/dev/null || true
    ip6tables -D INPUT -p udp --dport 48011 -j ACCEPT -m comment --comment "Big Remote Play - PIN" 2>/dev/null || true
    
    echo "✅ Regras iptables removidas"
}

show_status() {
    echo "🔍 Status do Firewall:"
    echo ""
    
    if command -v ufw &> /dev/null; then
        echo "═══ UFW ═══"
        ufw status | grep -i "big\|sunshine\|47989\|48010" || echo "Nenhuma regra encontrada"
        echo ""
    fi
    
    if command -v iptables &> /dev/null; then
        echo "═══ iptables (IPv4) ═══"
        iptables -L INPUT -n --line-numbers | grep -i "big\|47989\|48010" || echo "Nenhuma regra encontrada"
        echo ""
        
        echo "═══ ip6tables (IPv6) ═══"
        ip6tables -L INPUT -n --line-numbers | grep -i "big\|47989\|48010" || echo "Nenhuma regra encontrada"
    fi
}

show_usage() {
    cat << EOF
Big Remote Play Together - Configurador de Firewall

Uso: sudo $0 [COMANDO]

Comandos:
  configure   Configura regras do firewall
  remove      Remove regras do firewall
  status      Mostra status das regras
  help        Mostra esta ajuda

Portas configuradas:
  TCP 47984-47990   (Controle Sunshine)
  TCP 48010          (Streaming de vídeo)
  UDP 47998-48000   (Streaming de dados)

Nota: Este script requer privilégios de root
EOF
}

if [ "$EUID" -ne 0 ] && [ "${1:-help}" != "help" ]; then 
    echo "⚠️  Este script precisa de permissões de root"
    echo "   Execute: sudo $0 $1"
    exit 1
fi

case "${1:-help}" in
    configure)
        if command -v ufw &> /dev/null; then
            configure_ufw
        elif command -v iptables &> /dev/null; then
            configure_iptables
        else
            echo "❌ Nenhum firewall suportado encontrado (UFW ou iptables)"
            exit 1
        fi
        ;;
    remove)
        if command -v ufw &> /dev/null; then
            remove_ufw
        fi
        if command -v iptables &> /dev/null; then
            remove_iptables
        fi
        ;;
    status)
        show_status
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
