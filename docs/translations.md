# Translation workflow

The gettext domain is `big-remote-play`. `locale/LINGUAS` lists the 32 shipped catalogs. Runtime translations live in `usr/share/locale/<language>/LC_MESSAGES/big-remote-play.mo`.

Use gettext for visible text. Keep translated titles and explanations concise, match action names across pages, and distinguish a **pairing code** (authorization) from a **search code** (discovery). Do not translate technical identifiers, provider names or placeholder names. Do not copy Brazilian terminology indiscriminately into European Portuguese, or simplified Chinese text into the Traditional Chinese catalog.

The generic `zh` catalog targets simplified Chinese; `zh_CN` is the regional simplified variant and `zh_TW` uses Traditional Chinese. Their existing distinct catalogs are retained.

The release-preparation review synchronizes every active source message across all 32 catalogs, removes obsolete entries, rebuilds every runtime MO, and verifies placeholders, markup, protocol tokens and regional distinctions. These structural and editorial checks do not substitute for final approval by native speakers of every supported language.

```bash
python3 tools/i18n/validate_catalogs.py
# After an intentional PO edit, rebuild its runtime MO:
msgfmt --check -o usr/share/locale/pt_BR/LC_MESSAGES/big-remote-play.mo locale/pt_BR.po
```

Render changed strings in context, especially action rows, small windows, RTL languages and 150% text. Keep PO and MO updates together in a patch. Usernames, network addresses and app names are dynamic text: they must not be interpreted as Pango markup.
