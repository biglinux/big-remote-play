# UI-specific agent guidance

These rules supplement the repository-root `AGENTS.md` for files in this directory.

- GTK widgets and models are read or mutated only on the GTK main thread.
- Capture widget values before starting a worker. Workers receive plain data, not live widgets.
- Bound subprocess/network work, support cancellation and reject stale results after page changes or window close.
- Navigation is task-first: Home explains Share, Connect and private networking; selecting a role must not trigger side effects.
- Use libadwaita patterns and standard controls before custom containers or CSS.
- Keep one obvious primary action per state. Advanced controls belong in named pages/sheets, not the first screen.
- Always implement loading, empty, success, unavailable, cancelled and error states when applicable.
- Do not encode meaning only through color, icon, position or animation.
- Give interactive elements accessible names; add descriptions when the label alone does not explain the consequence.
- Preserve keyboard order and focus when rows are added, removed or replaced asynchronously.
- Dynamic user/network/app text must be escaped and must not be interpreted as Pango markup.
- Validate compact width, 150% text, high contrast, dark mode, RTL and at least one CJK locale after structural UI changes.
- Keep screenshots and visual fixtures sanitized and clearly mark simulated service data.
- Add behavior-focused tests in the nearest existing UI test module; avoid assertions tied only to CSS selectors or exact translated text.
