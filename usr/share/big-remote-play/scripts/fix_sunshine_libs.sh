#!/bin/bash
# Scripts to fix missing dependencies for Sunshine (libicuuc.so.76)
# This handles missing ICU libraries common after system updates.

if [ "$EUID" -ne 0 ]; then 
    echo "⚠️  Este script precisa de permissões de root para instalar pacotes ou criar links em /usr/lib."
    echo "   Por favor, execute: sudo $0"
    exit 1
fi

echo "🔍 Verificando bibliotecas ICU..."

# 1. Try to install legacy package if on Arch-based system (BigLinux/Manjaro)
if command -v pacman &> /dev/null; then
    echo "📦 Tentando instalar icu76 via pacman..."
    if pacman -S --needed --noconfirm icu76; then
        echo "✅ Pacote icu76 instalado com sucesso."
        exit 0
    else
        echo "⚠️  Não foi possível instalar icu76 automaticamente via pacman."
    fi
fi

echo "ℹ️  O Sunshine requer especificamente a versão 76 devido a símbolos versionados."
echo "🔗 Criar links simbólicos (ex: v78 -> v76) NEM SEMPRE funciona para o ICU."

found=false

# Check for version 78 to link to 76
if [ -f "/usr/lib/libicuuc.so.78" ]; then
    echo "✅ Encontrado libicuuc.so.78"
    
    echo "🔗 Deseja criar links simbólicos (78 -> 76) como último recurso?"
    read -p "[s/N] " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Ss]$ ]]; then
        ln -sf /usr/lib/libicuuc.so.78 /usr/lib/libicuuc.so.76
        ln -sf /usr/lib/libicudata.so.78 /usr/lib/libicudata.so.76
        ln -sf /usr/lib/libicui18n.so.78 /usr/lib/libicui18n.so.76
        echo "   Links criados em /usr/lib/"
        found=true
    fi
else
    # Try finding whatever version IS installed
    CURRENT_LIB=$(find /usr/lib -name "libicuuc.so.*" | head -n 1)
    if [ -n "$CURRENT_LIB" ]; then
        VERSION=$(echo "$CURRENT_LIB" | grep -oE '[0-9]+$' | head -n 1)
        echo "⚠️  Encontrada versão $VERSION em $CURRENT_LIB"
        
        echo "🔗 Tentar criar links baseados nessa versão?"
        read -p "[s/N] " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Ss]$ ]]; then
             ln -sf "/usr/lib/libicuuc.so.$VERSION" /usr/lib/libicuuc.so.76
             ln -sf "/usr/lib/libicudata.so.$VERSION" /usr/lib/libicudata.so.76
             ln -sf "/usr/lib/libicui18n.so.$VERSION" /usr/lib/libicui18n.so.76
             found=true
        fi
    else
        echo "❌ Nenhuma versão do libicu encontrada!"
    fi
fi

if [ "$found" = true ]; then
    echo ""
    echo "✅ Correção (links) aplicada. Tente iniciar o Sunshine agora."
    echo "💡 Se o Sunshine ainda falhar, instale 'icu76' do AUR."
else
    echo ""
    echo "❌ Não foi possível aplicar a correção automática."
    echo "💡 Dica: No BigLinux/AUR, execute: yay -S icu76"
fi

