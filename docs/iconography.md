# Icons and visual hierarchy

Use the existing symbolic icon set via `create_icon_widget`, `set_icon` and `set_row_icon`. Routine navigation, tabs and action rows are monochrome and inherit theme color. Accent tiles are reserved for primary Home roles and consistently styled provider choices. Status color must always have a textual equivalent.

Do not load a symbolic SVG as an ordinary color image or force black/blue fills through CSS. The app logo is intentionally full-color and separate from symbolic controls. Decorative images have the presentation role; icon-only controls need an accessible name and a tooltip.

The local `icons/hicolor/scalable/actions` overlay exposes the 55 symbolic source SVGs through relative links. **Do not add a reduced local `hicolor/index.theme`**: it can hide directory definitions needed by Adwaita's embedded icons, including its expander arrow. Use the system hicolor index. Tests check both local assets and `adw-expander-arrow-symbolic`.

Keep rounded native groups, FLAT main toolbar styling and a distinct sidebar surface. The header title stack must not reserve the width of hidden desktop tabs when the compact title is active. In small windows the same task tabs use the native lower view switcher.
