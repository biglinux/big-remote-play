"""How the running application picks and uses its translations.

English is the source language. The catalogs compiled into usr/share/locale
are looked up from the standard locale variables; a missing catalog must give
the English text, never a key, ``None`` or an empty string.
"""

from __future__ import annotations

import gettext
from pathlib import Path

import pytest

from big_remote_play.utils import i18n

ROOT = Path(__file__).resolve().parents[1]
LOCALE = ROOT / "usr/share/locale"
LINGUAS = [value for line in (ROOT / "locale/LINGUAS").read_text(encoding="utf-8").splitlines() for value in line.split("#", 1)[0].split()]


def catalog(environ: dict[str, str]) -> gettext.NullTranslations:
    """The translation the application would load for this environment."""
    return gettext.translation(i18n.DOMAIN, localedir=str(LOCALE), languages=i18n.requested_languages(environ) or ["C"], fallback=True)


@pytest.mark.parametrize(
    ("environ", "expected"),
    [
        ({"LANG": "pt_BR.UTF-8"}, ["pt_BR", "pt"]),
        ({"LANG": "de_DE.UTF-8"}, ["de_DE", "de"]),
        ({"LANG": "en_US.UTF-8", "LANGUAGE": "fr:de"}, ["fr", "de"]),
        ({"LANG": "pt_BR.UTF-8", "LC_MESSAGES": "es_ES.UTF-8"}, ["es_ES", "es"]),
        ({"LANG": "ca_ES.UTF-8@valencia"}, ["ca_ES", "ca"]),
        ({"LANG": "C.UTF-8"}, []),
        ({"LANG": "POSIX"}, []),
        ({}, []),
    ],
)
def test_the_message_locale_follows_gettext_precedence(environ, expected):
    assert i18n.requested_languages(environ) == expected


@pytest.mark.parametrize(
    ("locale", "language"),
    [
        # Norwegian Bokmål and Nynorsk desktops; the catalog is "no".
        ("nb_NO.UTF-8", "no"),
        ("nn_NO.UTF-8", "no"),
        # Hong Kong and Macau read Traditional Chinese, Singapore Simplified.
        ("zh_HK.UTF-8", "zh_TW"),
        ("zh_MO.UTF-8", "zh_TW"),
        ("zh_SG.UTF-8", "zh_CN"),
        ("zh_TW.UTF-8", "zh_TW"),
        ("pt_PT.UTF-8", "pt"),
        ("de_AT.UTF-8", "de"),
    ],
)
def test_regional_locales_load_the_catalog_shipped_for_them(locale, language):
    assert catalog({"LANG": locale}).info().get("language") == language
    assert set(i18n.LANGUAGE_ALIASES.values()) <= set(LINGUAS)


@pytest.mark.parametrize("language", LINGUAS)
def test_every_listed_language_has_a_compiled_catalog_for_the_domain(language):
    path = LOCALE / language / "LC_MESSAGES" / f"{i18n.DOMAIN}.mo"
    with path.open("rb") as stream:
        translation = gettext.GNUTranslations(stream)
    assert translation.info().get("language") == language


def test_brazilian_portuguese_is_loaded_from_the_system_locale():
    translation = catalog({"LANG": "pt_BR.UTF-8"})
    assert translation.gettext("Paired devices") == "Dispositivos pareados"
    assert translation.pgettext("navigation", "Share") == "Compartilhar"
    # European Portuguese is a separate catalog.
    assert catalog({"LANG": "pt_PT.UTF-8"}).gettext("Paired devices") == "Dispositivos emparelhados"


@pytest.mark.parametrize("environ", [{"LANG": "en_US.UTF-8"}, {"LANG": "xx_YY.UTF-8"}, {"LANG": "C"}, {}])
def test_without_a_catalog_the_english_source_is_shown(environ):
    translation = catalog(environ)
    assert translation.gettext("Paired devices") == "Paired devices"
    assert translation.pgettext("navigation", "Share") == "Share"
    assert translation.ngettext("{count} more paired device", "{count} more paired devices", 1) == "{count} more paired device"
    assert translation.ngettext("{count} more paired device", "{count} more paired devices", 2) == "{count} more paired devices"


def test_plural_forms_follow_the_language_rules():
    russian = catalog({"LANG": "ru_RU.UTF-8"})
    forms = [russian.ngettext("{count} more paired device", "{count} more paired devices", count).format(count=count) for count in (1, 3, 5, 21)]
    assert forms[0] != forms[1] != forms[2]
    assert forms[3].replace("21", "1") == forms[0]
    japanese = catalog({"LANG": "ja_JP.UTF-8"})
    assert japanese.ngettext("{count} more paired device", "{count} more paired devices", 1).replace("1", "N") == japanese.ngettext(
        "{count} more paired device", "{count} more paired devices", 7
    ).replace("7", "N")


def test_the_test_session_uses_the_english_source():
    # tests/conftest.py sets LANGUAGE=C before the module is imported.
    assert i18n._("Paired devices") == "Paired devices"
    assert i18n.pgettext("navigation", "Connect") == "Connect"
    assert i18n.ngettext("{count} more paired device", "{count} more paired devices", 3) == "{count} more paired devices"
