#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Internationalization support for LangForge."""

import gettext
import os

# Determine locale directory (works in AppImage and system install)
locale_dir = '/usr/share/locale'  # Default for system install

# Check if we're in an AppImage
if 'APPIMAGE' in os.environ or 'APPDIR' in os.environ:
    script_dir = os.path.dirname(os.path.abspath(__file__))  # utils/
    app_dir = os.path.dirname(script_dir)                    # big-remote-play/
    share_dir = os.path.dirname(app_dir)                     # share/
    appimage_locale = os.path.join(share_dir, 'locale')      # share/locale
    
    if os.path.isdir(appimage_locale):
        locale_dir = appimage_locale

# Configure the translation text domain for big-remote-play
gettext.bindtextdomain("big-remote-play", locale_dir)
gettext.textdomain("big-remote-play")

# Export _ directly as the translation function
_ = gettext.gettext
