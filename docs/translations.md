# Translations

Big Remote Play is written in English. Every text a person can see, or an assistive technology can read, is English in the source code and translated through GNU gettext. The domain is `big-remote-play`, and `locale/LINGUAS` lists the 32 shipped catalogs.

```text
Python and shell sources (English)  ─┐
build-aux/i18n/*.desktop.in          ├─ xgettext ─▶ locale/big-remote-play.pot
build-aux/i18n/*.metainfo.xml.in    ─┘                     │ msgmerge
                                                           ▼
                                                   locale/<language>.po
                                                           │ msgfmt
              ┌────────────────────────────────────────────┼──────────────────────────────┐
              ▼                                            ▼                              ▼
usr/share/locale/<language>/LC_MESSAGES/   usr/share/applications/*.desktop   usr/share/metainfo/*.metainfo.xml
big-remote-play.mo                         (Name, GenericName, Comment,       (summary and description in
                                           Keywords in every language)        every language)
```

## How the application chooses a language

`src/big_remote_play/utils/i18n.py` loads one compiled catalog chain at import, following gettext's own precedence: `LANGUAGE`, then `LC_ALL`, `LC_MESSAGES` and `LANG`. `pt_BR.UTF-8` loads `pt_BR` and then `pt`; `de_AT` loads `de`. A few regional locales are mapped to the catalog shipped for them:

| Locale | Catalog |
|---|---|
| `nb`, `nn` (Norwegian Bokmål and Nynorsk) | `no` |
| `zh_HK`, `zh_MO` | `zh_TW` (Traditional) |
| `zh_SG` | `zh_CN` (Simplified) |

The C or POSIX locale, an unknown language, or a missing catalog shows the English source text. A key, `None` or an empty string is never shown. Translations are read only from the compiled `.mo` files; nothing parses `.po` files at run time.

Packages install the catalogs at `/usr/share/locale/<language>/LC_MESSAGES/big-remote-play.mo`. The native package, Nix and Flatpak compile them from `locale/LINGUAS` during the build. A checkout uses `usr/share/locale/`, and `BIG_REMOTE_PLAY_LOCALEDIR` overrides both locations.

## Writing translatable code

```python
from big_remote_play.utils.i18n import _, ngettext, pgettext

Gtk.Button(label=_("Connect"))
row.set_title(_("Connected to {name}").format(name=device.name))
row.set_title(ngettext("{count} more paired device", "{count} more paired devices", count).format(count=count))
page_title = pgettext("navigation", "Share")
```

- **Named placeholders.** Write `{name}`, `{count}`, `{path}` and pass them by keyword. Never build a sentence from translated fragments with `+`: word order differs between languages.
- **Plurals.** Use `ngettext` whenever a number is followed by a counted noun. Put `{count}` in both forms; languages such as Russian, Polish or Hebrew have three or four forms.
- **Context.** Use `pgettext(context, text)` when one English word has two meanings. `navigation` marks Share and Connect as page names; buttons that perform the action use the plain words.
- **Notes for translators.** A comment that starts with `# TRANSLATORS:` on the line just above the string reaches the catalog.
- **Identifiers are not labels.** Logic compares stable ids, enum values or list positions, never displayed text. For example, the Custom choice of a list is recognized by its position, not by the translated word "Custom".
- **Logs stay English.** Messages passed only to the logger are not translated, so a support log is readable whatever the user's language. When the same problem is also shown in a dialog, that dialog text goes through gettext.
- **Technical details stay as reported.** Command output, protocol markers (`BRP_DATA`, `BRP_PHASE`), error codes and the text shown under **Technical details** are copied as the tool printed them.
- **Not translated:** product and protocol names (Big Remote Play, Sunshine, Moonlight, Tailscale, ZeroTier, Headscale, Steam, Wayland, PipeWire, Vulkan, VA-API), addresses, URLs, commands, paths, Network IDs, codecs, resolutions and units (FPS, Mbps, ms). The sentences around them are translated.

Shell helpers use `gettext` and `eval_gettext` from `gettext.sh`. A `(y/N)` prompt is a protocol token, because the script reads exactly those letters.

The desktop entry and the AppStream metainfo are edited only in `build-aux/i18n/`. Do not edit the files under `usr/share/applications/` or `usr/share/metainfo/`: they are regenerated with every translation. `Name` and elements marked `translate="no"` keep the product name. A translated `Keywords` value must keep its `;` separators and end with `;`.

## Commands

```bash
make pot          # refresh only locale/big-remote-play.pot to review new messages
make update-po    # merge every catalog, rebuild the .mo files and the translated metadata
make check-i18n   # the checks below; make translations-check runs the same
```

`make check-i18n` runs three tools:

- `tools/i18n/validate_catalogs.py` checks that every language in `LINGUAS` has a catalog and a compiled `.mo`. It requires each catalog to match the template exactly, with no fuzzy, untranslated or obsolete entries. Each catalog must keep the right number of plural forms and identical placeholders and markup. `msgfmt --check` must accept it, the `.mo` must match its `.po`, and the desktop entry and metainfo must match the catalogs. `pt` must stay distinct from `pt_BR`, and `zh_TW` from `zh_CN`.
- `tools/i18n/check_untranslated_human_text.py` finds English text copied into a catalog. When a reviewed translation really is spelled like the English (Danish "Start ZeroTier", Spanish "Software (CPU)"), add it for that catalog only to `SAME_AS_SOURCE`.
- `tools/i18n/check_hardcoded_text.py` reads the Python sources. It reports English text that reaches a visible property without gettext: labels, titles, subtitles, tooltips, placeholders, dialog responses, toasts, string-list models, accessible labels and the project's row helpers. Product names, numbers, units, placeholders and URLs are accepted. The checker cannot follow text stored in data tables, such as `(value, label)` option lists, so wrap those labels in `_()` where they are written.

The test suite runs the same checks. It also verifies that the committed template matches the sources, that an update run twice changes nothing, and how languages are chosen at run time.

## Adding or changing text

1. Write the English text in the code with `_()`, `ngettext()` or `pgettext()`.
2. Run `make update-po`. New messages appear in every catalog with an empty translation.
3. Translate them in every `locale/<language>.po`, including every plural form, and run `make update-po` again to compile.
4. Run `make check-i18n` and the tests.
5. Render the changed screens in a long language (German, Russian), in Hebrew (right to left) and in Japanese or Chinese. Check compact width and 150 % text. Long words must wrap, and no button may be cut or widened past its row.
6. Commit the sources, the `.po` files, the `.mo` files and the regenerated metadata together.

To add a language, add its code to `locale/LINGUAS`, create the catalog with `msginit` from the template, add its plural count to `EXPECTED_PLURAL_COUNTS` in the validator, translate it and run `make update-po`.

## Language conventions

| Language | Form of address |
|---|---|
| German (`de`) | *Sie* |
| French (`fr`) | *vous* |
| Russian, Ukrainian, Belarusian | polite plural (*вы*, *ви*, *вы*) |
| Dutch (`nl`) | *je* |
| Spanish (`es`) | *tú* |
| Italian (`it`) | *tu* |
| Brazilian Portuguese (`pt_BR`) | *você*, Brazilian terms (*arquivo*, *tela*, *usuário*) |
| European Portuguese (`pt`) | European terms (*ficheiro*, *ecrã*, *utilizador*); not a copy of `pt_BR` |

`zh` and `zh_CN` are Simplified Chinese; `zh_TW` is Traditional Chinese with Taiwanese terms (軟體, 設定, 連線). Do not copy one into the other.

## Terminology

Use these English terms in new text, and keep each language's established translation of them:

| Term | Meaning |
|---|---|
| **Share** | The page and action on the computer that runs the game (Sunshine). |
| **Connect** | The page and action on the computer that plays remotely (Moonlight). |
| **game PC** | The computer that runs the game, seen from the other computer. |
| **this computer** | The computer the person is using now. |
| **Play over the internet** | The page for private networks between distant computers. |
| **private network** | Tailscale, ZeroTier or Headscale; "VPN" only where the technical term is clearer. |
| **pair**, **PIN** | Authorizing a device once with Moonlight's four-digit PIN. |
| **pairing code** / **search code** | Authorization, as opposed to finding a computer. |
| **Network ID** | ZeroTier's 16-character network identifier. |
| **Game Window** | The source that shares one game window instead of the desktop. |

## Review limits

The structural checks prove that catalogs are complete and safe to compile. They do not prove natural, idiomatic wording. Native speakers should review new and changed messages in context, and changes reported by translators take precedence over machine-assisted wording.
