#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Internationalization support for Big Remote Play."""

import gettext

from big_remote_play import paths

DOMAIN = "big-remote-play"
locale_dir = str(paths.LOCALE_DIR)

# Configure the translation text domain (used by C-side libraries / Gtk too).
gettext.bindtextdomain(DOMAIN, locale_dir)
gettext.textdomain(DOMAIN)

# Resolve catalogs explicitly so the domain works regardless of process locale
# setup; fallback=True returns the source string when no translation exists.
_translation = gettext.translation(DOMAIN, localedir=locale_dir, fallback=True)
_ = _translation.gettext
