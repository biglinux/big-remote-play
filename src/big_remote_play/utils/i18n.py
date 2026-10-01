#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Internationalization support for Big Remote Play.

English is the source language: every visible string is written in English
and wrapped in ``_()``, ``ngettext()`` or ``pgettext()``. Compiled catalogs
are looked up once, at import, from the standard locale variables.
"""

from __future__ import annotations

from collections.abc import Mapping
import gettext
import os

from big_remote_play import paths

DOMAIN = "big-remote-play"
locale_dir = str(paths.LOCALE_DIR)

# Locales whose catalog is shipped under another name. Without these,
# Norwegian Bokmål desktops (nb_NO) got English and Hong Kong/Macau desktops
# got Simplified instead of Traditional Chinese.
LANGUAGE_ALIASES = {
    "nb": "no",
    "nn": "no",
    "zh_HK": "zh_TW",
    "zh_MO": "zh_TW",
    "zh_SG": "zh_CN",
}


def requested_languages(environ: Mapping[str, str] = os.environ) -> list[str]:
    """Catalog names to try, in order, for the user's message locale.

    Uses the variables and precedence of gettext itself (LANGUAGE, LC_ALL,
    LC_MESSAGES, LANG). An empty list, or the C/POSIX locale, means the
    English source text.
    """
    values: list[str] = []
    for name in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        value = environ.get(name, "").strip()
        if value:
            values = [item for item in value.split(":") if item]
            break
    languages: list[str] = []
    for value in values:
        code = value.split(".", 1)[0].split("@", 1)[0]
        if code in ("C", "POSIX"):
            break
        base = code.split("_", 1)[0]
        # The alias goes first: gettext expands every name to its base
        # language too, so "zh_HK" alone would reach Simplified "zh".
        alias = LANGUAGE_ALIASES.get(code) or LANGUAGE_ALIASES.get(base)
        for candidate in (alias, code, base):
            if candidate and candidate not in languages:
                languages.append(candidate)
    return languages


# Configure the translation text domain (used by C-side libraries / Gtk too).
gettext.bindtextdomain(DOMAIN, locale_dir)
gettext.textdomain(DOMAIN)

# Resolve catalogs explicitly so the domain works regardless of process locale
# setup; fallback=True returns the English source when no catalog exists.
_translation = gettext.translation(DOMAIN, localedir=locale_dir, languages=requested_languages() or ["C"], fallback=True)
_ = _translation.gettext
ngettext = _translation.ngettext
pgettext = _translation.pgettext
