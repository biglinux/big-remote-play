# Gamer theme design

The **Gamer** appearance should read as part of the same family as BigAMode,
while its imagery and hierarchy remain about remote play: game computer,
network, receiving screen and controller. It is a dark, static preset layered
over the normal Big Remote Play design.

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

## Motion, accessibility and adaptation

The overlay defines no custom transitions or animation, so reduced-motion users
receive no Gamer-specific motion and GTK's own animation preference remains in
control. There are no timers, shaders, blur or continuously redrawn effects.

When libadwaita reports high contrast, the application removes the Gamer
provider but keeps `gamer` selected and persisted. Native high contrast plus the
base stylesheet then owns every colour, border and focus state. Turning high
contrast off reapplies the same single provider.

The theme does not add fixed content widths, fixed pixel font sizes, or new UI
structure. Existing 880sp/720sp breakpoints, the 360px minimum, system fonts,
RTL order, CJK wrapping and 150% text behaviour are therefore preserved. Visual
validation still has to cover those modes on a real desktop; automated tests
only protect the structural contracts.

