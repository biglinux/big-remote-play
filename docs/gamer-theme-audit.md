# Gamer theme audit

This audit records the theme boundary and the visual contracts that must remain
stable while adding the **Gamer** appearance. The expected result is a visual
preset; streaming, network, audio and controller behaviour remain unchanged.

## Current architecture

`BigRemotePlayApp` in `src/big_remote_play/app.py` owns the `app.theme` stateful
action, persists its string value through `Config`, selects libadwaita's colour
scheme, and loads the application stylesheet. `MainWindow` only builds the
native radio menu. `paths.py` is the source of truth for installed, development
and relocated data paths.

Before this change the accepted values were `auto`, `light` and `dark`.
`style.css` is a small semantic layer over libadwaita. Its classes already cover
the Home hero, stream diagram, role cards, guided setup, Share and Connect
heroes, network panels, navigation, status pills, metrics, dialogs and boxed
lists. The adaptive shell changes composition at 880sp and 720sp and keeps a
360px content minimum.

The base provider was installed once at startup, but no removable overlay
provider existed. An unknown saved theme selected libadwaita's default scheme
while leaving the menu action in an unknown state.

## BigAMode reference

The reference implementation in `bigamemode/style/gamer.css` and
`bigame-engine/bigame-ui/src/theme.rs` uses these durable ideas:

- the normal stylesheet remains the base;
- Gamer is one higher-priority provider held by identity and removed to restore
  the base exactly;
- violet is the primary accent, cyan is the secondary accent, and magenta is
  limited to signature gradients;
- layered blue-black surfaces replace pure black;
- navigation, primary actions, progress, cards, popovers and dialogs share one
  radius, border and elevation language;
- destructive, warning and success semantics remain distinct;
- high contrast suspends the decorative provider while preserving the saved
  choice;
- effects are static except for state changes, with no timers, blur or looping
  animation.

BigAMode currently relies on GTK 4.20 CSS custom properties and media queries.
Big Remote Play supports GTK 4.10+, so its adaptation uses GTK named colours and
compatible selectors. Gamer is intentionally a dark preset rather than a second
design/scheme matrix.

## Risks and strategy

The main lifecycle risk is accumulating display providers during repeated menu
changes. The application must therefore keep both the provider and the display
used to register it, make add/remove idempotent, and remove the exact provider
when Gamer is no longer effective. High-contrast changes use the same sync path.

Global palette overrides are safe only while the provider is installed. The
Gamer stylesheet therefore owns every override and the base stylesheet has no
dependency on it. No page tests `theme == "gamer"`; existing semantic classes
are the component API. Missing or future theme values normalize to `auto` for a
safe menu state without rewriting user configuration during startup.

The new CSS file ships under the existing native data directory, so source,
PKGBUILD and Flatpak packaging already include it recursively. The Python wheel
remains code-only by design.

