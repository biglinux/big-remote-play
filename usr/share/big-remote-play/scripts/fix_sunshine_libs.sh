#!/bin/bash

# Fix Sunshine ICU library dependencies by creating symlinks to newer versions
# This is a workaround for when Sunshine is built against older ICU versions than what is installed

if [ "$EUID" -ne 0 ]; then 
    echo "Please run as root (sudo)"
    exit 1
fi

echo "Checking for missing Sunshine libraries..."

# Function to find the latest available version of a library
find_latest_version() {
    local lib_name=$1
    # Look for files like /usr/lib/libicuuc.so.*
    # Sort nicely using version sort (-V) and pick the last one
    local latest=$(ls /usr/lib/${lib_name}.so.* 2>/dev/null | grep -E '\.so\.[0-9]+$' | sort -V | tail -n 1)
    
    if [ -n "$latest" ]; then
        # Extract version number
        echo "$latest" | awk -F'.so.' '{print $2}'
    else
        echo ""
    fi
}

fix_lib() {
    local lib_name=$1
    local needed_ver="76" # Hardcoded for now based on user report, could be dynamic
    
    local missing_lib="/usr/lib/${lib_name}.so.${needed_ver}"
    
    if [ -f "$missing_lib" ]; then
        echo "  [OK] $missing_lib already exists."
        return
    fi
    
    echo "  [MISSING] $missing_lib not found."
    
    # helper to find what we HAVE
    local latest_ver=$(find_latest_version "$lib_name")
    
    if [ -n "$latest_ver" ]; then
        local existing_lib="/usr/lib/${lib_name}.so.${latest_ver}"
        echo "  [FOUND] Newer version available: $existing_lib"
        
        echo "  -> Creating symlink..."
        ln -s "$existing_lib" "$missing_lib"
        
        if [ $? -eq 0 ]; then
            echo "  [SUCCESS] Linked $missing_lib -> $existing_lib"
        else
            echo "  [ERROR] Failed to create symlink."
        fi
    else
        echo "  [ERROR] No version of $lib_name found in /usr/lib/"
    fi
}

# Fix the main ICU libraries
fix_lib "libicuuc"
fix_lib "libicudata"
fix_lib "libicui18n"

echo "Library fix complete. Try running Sunshine again."
