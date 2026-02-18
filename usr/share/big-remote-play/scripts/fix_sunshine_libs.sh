#!/bin/bash
# Fix Sunshine ICU libraries by downloading correct version
# Version: 2.0

TARGET_DIR="/usr/share/big-remote-play/libs"
# URL using Arch Linux Archive
ICU_URL="https://archive.archlinux.org/packages/i/icu/icu-76.1-1-x86_64.pkg.tar.zst"
TEMP_DIR=$(mktemp -d)

echo "Starting Sunshine Library Fix..."

# Create target directory
mkdir -p "$TARGET_DIR"

# Check if libraries already exist in system (real files, not symlinks to 78)
# We check if .so.76 exists and is NOT a symlink to something else (unless it's to our own files)
# But simple check: if strict .76 exists we assume it's good? NO. 
# The issue is that the user MIGHT have broken symlinks now.
# So we should force overwrite if we are running this script.

echo "Downloading ICU 76 from Arch Archive..."
if curl -L -o "$TEMP_DIR/icu.pkg.tar.zst" "$ICU_URL"; then
    echo "Download successful. Extracting..."
    
    # Extract only the needed libraries
    # We use tar with zstd.
    tar --zstd -xvf "$TEMP_DIR/icu.pkg.tar.zst" -C "$TEMP_DIR" usr/lib/libicuuc.so.76.1 usr/lib/libicudata.so.76.1 usr/lib/libicui18n.so.76.1
    
    # Move to target
    mv "$TEMP_DIR"/usr/lib/libicu*.so.76.1 "$TARGET_DIR/"
    
    # Create symlinks for .so.76
    ln -sf "$TARGET_DIR/libicuuc.so.76.1" "$TARGET_DIR/libicuuc.so.76"
    ln -sf "$TARGET_DIR/libicudata.so.76.1" "$TARGET_DIR/libicudata.so.76"
    ln -sf "$TARGET_DIR/libicui18n.so.76.1" "$TARGET_DIR/libicui18n.so.76"
    
    echo "Libraries installed to $TARGET_DIR"
    
    # Setup global symlinks in /usr/lib ONLY IF they don't exist or are broken
    # Actually, modifying /usr/lib is risky. 
    # Better to just use LD_LIBRARY_PATH in the app.
    # But if the user runs sunshine CLI manually, it might fail.
    # We will try to link in /usr/lib as fallback for convenience, but the app uses LD_LIBRARY_PATH
    
    for lib in libicuuc.so.76 libicudata.so.76 libicui18n.so.76; do
        if [ ! -f "/usr/lib/$lib" ]; then
            echo "Linking /usr/lib/$lib -> $TARGET_DIR/$lib"
            ln -sf "$TARGET_DIR/$lib" "/usr/lib/$lib"
        else
            # Check if it is a symlink to .78 (the broken fix)
            TARGET=$(readlink -f "/usr/lib/$lib")
            if [[ "$TARGET" == *"78"* ]]; then
                 echo "Fixing broken symlink /usr/lib/$lib (was pointing to $TARGET)"
                 ln -sf "$TARGET_DIR/$lib" "/usr/lib/$lib"
            fi
        fi
    done
    
    # Cleanup
    rm -rf "$TEMP_DIR"
    echo "Done."
else
    echo "Failed to download ICU package."
    rm -rf "$TEMP_DIR"
    exit 1
fi
