#!/bin/bash
# Scripts to fix missing dependencies for Sunshine (libicuuc.so.76)
# This creates symlinks from the installed version (78) to the version Sunshine expects (76)

if [ "$EUID" -ne 0 ]; then 
    echo "⚠️  Este script precisa de permissões de root para criar links simbólicos em /usr/lib."
    echo "   Por favor, execute: sudo $0"
    exit 1
fi

echo "🔍 Verificando bibliotecas ICU..."

found=false

# Check for version 78 to link to 76
if [ -f "/usr/lib/libicuuc.so.78" ]; then
    echo "✅ Encontrado libicuuc.so.78"
    
    echo "🔗 Criando links simbólicos para versão 76..."
    
    ln -sf /usr/lib/libicuuc.so.78 /usr/lib/libicuuc.so.76
    echo "   Created /usr/lib/libicuuc.so.76"
    
    ln -sf /usr/lib/libicudata.so.78 /usr/lib/libicudata.so.76
    echo "   Created /usr/lib/libicudata.so.76"
    
    ln -sf /usr/lib/libicui18n.so.78 /usr/lib/libicui18n.so.76
    echo "   Created /usr/lib/libicui18n.so.76"
    
    found=true
else
    # Try finding whatever version IS installed
    CURRENT_LIB=$(find /usr/lib -name "libicuuc.so.*" | head -n 1)
    if [ -n "$CURRENT_LIB" ]; then
        VERSION=$(echo "$CURRENT_LIB" | grep -oE '[0-9]+$')
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
    echo "✅ Correção aplicada. Tente iniciar o Sunshine agora."
else
    echo ""
    echo "❌ Não foi possível aplicar a correção automática."
fi
