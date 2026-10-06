# Visual design

The appearance presets, the **Gamer** palette and the icon rules. The person
chooses the appearance in the application menu (**Appearance**: **Gamer**,
**Automatic**, **Light**, **Dark**); see the
[user guide](user-guide.md#choose-the-appearance). UI rules for contributors
are in [`src/big_remote_play/ui/AGENTS.md`](../src/big_remote_play/ui/AGENTS.md).

## The Gamer appearance

**Gamer** is a dark, static preset layered over the normal Big Remote Play
design. Its imagery and hierarchy stay about remote play: game computer,
network, receiving screen and controller.

`app.py` keeps the base stylesheet loaded for the application's lifetime and
adds `usr/share/big-remote-play/ui/gamer.css` as one higher-priority provider
only while **Gamer** is effective. Selecting another appearance removes that
provider; nothing else changes.

## Design tokens

GTK named colours in `gamer.css` are the implementation tokens. The core
palette is:

| Role | Value | Use |
| --- | --- | --- |
| Violet | `#835dff` | selection and major accents |
| Strong violet | `#6f4aff` | accessible primary-action base |
| Cyan | `#22d3ee` | receiving/device accents and progress |
| Magenta | `#d946ef` | the middle of signature gradients only |
| Blue | `#4f6bff` | primary-action gradient end |
| Window | `#151a35` | main background |
| View/sidebar | `#10142b` / `#11152d` | deeper navigation and content layers |
| Surface | `#232846` | cards and lists |
| Elevated surface | `#2a2f50` | popovers and hover layers |
| Primary text | `#eceeff` | headings and body text |
| Secondary text | `#b8bdda` | supporting text |

Primary text on the window is 14.82:1, secondary text on the window is 9.20:1,
primary text on cards is 12.46:1, and white on the primary violet gradient's
darkest relevant stop is at least 4.59:1. These calculated sRGB contrast ratios
meet WCAG AA for normal text.

## Component mapping

- The Home page uses a quiet violet wash. Its hero carries the only large
  violet-magenta-cyan signature and keeps copy on a solid-enough dark surface.
- Share uses violet/blue; Connect uses cyan. Both retain text, icons and status
  words, so colour never carries state alone.
- Guided setup has a stronger edge and signature highlight, while its choices
  continue to use native buttons and focus behaviour.
- Navigation rows remain still on hover. Selection adds a three-pixel violet
  inset indicator, a tinted surface, brighter text and icon.
- Suggested actions use a violet-to-blue gradient. Destructive actions remain
  red. Cards, lists, dialogs and popovers use one restrained border/elevation
  family with 12–22px radii according to hierarchy.
- Switches, progress bars and focus use violet/cyan accents with visible knobs,
  tracks and outlines. Status pills retain their semantic success, warning and
  neutral colours plus their existing icon and text.
- Service status cards repeat the tone rules of the
  [base design](service-status-cards.md#visual-and-accessibility-contract) in
  this palette: an active service is lit with a cyan-violet tint, a cyan border
  and a cyan icon tile; the others stay quiet.

## Motion, accessibility and adaptation

The overlay defines no custom transitions or animation, so reduced-motion users
receive no Gamer-specific motion and GTK's own animation preference remains in
control. There are no timers, shaders, blur or continuously redrawn effects.

When libadwaita reports high contrast, the application removes the Gamer
provider but keeps `gamer` selected and persisted. Native high contrast plus the
base stylesheet then owns every colour, border and focus state. Turning high
contrast off reapplies the same single provider.

The theme does not add fixed content widths, fixed pixel font sizes, or new UI
structure. The 880sp/720sp breakpoints, the 360px minimum, system fonts,
RTL order, CJK wrapping and 150% text behaviour are therefore preserved. Visual
validation still has to cover those modes on a real desktop; automated tests
only protect the structural contracts.

## Icons

Use the existing symbolic icon set through `create_icon_widget` and `set_icon`
(`utils/icons.py`) and `set_row_icon` (`ui/components.py`). Routine navigation,
tabs and action rows are monochrome and inherit the theme colour. Accent tiles
are reserved for primary Home roles and consistently styled provider choices.
Status colour must always have a textual equivalent.

Do not load a symbolic SVG as an ordinary colour image or force black/blue
fills through CSS. The application logo is intentionally full-colour and
separate from symbolic controls. Decorative images have the presentation role;
icon-only controls need an accessible name and a tooltip.

The local `usr/share/big-remote-play/icons/hicolor/scalable/actions` overlay
exposes every symbolic source SVG through a relative link. **Do not add a
reduced local `hicolor/index.theme`**: it can hide directory definitions needed
by Adwaita's embedded icons, including its expander arrow. Use the system
hicolor index. Tests check both the local assets and that
`adw-expander-arrow-symbolic` still resolves (`tests/test_guided_home_review.py`).

Keep rounded native groups, flat main toolbar styling and a distinct sidebar
surface. The header title stack must not reserve the width of hidden desktop
tabs when the compact title is active. In small windows the same task tabs use
the native lower view switcher.
