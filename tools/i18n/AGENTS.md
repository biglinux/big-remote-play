# Translation-tool guidance

These rules supplement the repository-root `AGENTS.md`.

- `locale/big-remote-play.pot` is generated from active Python and shell sources.
- `locale/LINGUAS` is the exact source-catalog inventory; `usr/share/locale/` must match it exactly.
- Updates must be transactional: do not replace the live catalogs until extraction, merge, validation and compilation all succeed.
- With fixed `SOURCE_DATE_EPOCH`, running the updater twice must produce byte-identical POT, PO and MO files.
- Preserve translations by exact locale. Never merge `pt` with `pt_BR` or `zh_TW` with Simplified Chinese catalogs.
- Remove obsolete entries only after confirming they are absent from the active extraction.
- Preserve Python, printf and shell placeholders, markup, URLs, command names and protocol markers.
- Compile with `msgfmt --check --check-format --check-header`.
- Human-language quality is editorial work. Validators may detect omissions, copies and placeholder damage, but may not claim native-level prose.
- Commit PO and MO changes together and render changed strings in context, including compact, RTL, CJK and large-text layouts.
