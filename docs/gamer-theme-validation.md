# Gamer theme validation

This file is the release-oriented validation record for the **Gamer**
appearance. It is updated with executed commands and observed results; passing
automated checks does not substitute for a real assistive-technology or display
review.

## Automated checks

- **PASS** — focused theme/config/CSS/layout/accessibility suite:
  `python3 -m pytest -q -p no:cacheprovider tests/test_gamer_theme.py
  tests/test_config.py tests/test_css_regressions.py
  tests/test_ui_premium_layout_regressions.py
  tests/test_visual_accessibility_regressions.py` — 47 passed. The two warnings
  are upstream GI deprecations, not CSS or GTK runtime warnings.
- **PASS** — `make lint` — 137 Python files formatted; Ruff checks passed.
- **PASS** — `make translations-check` — all 32 PO catalogs and runtime MO
  files match, including the proper preset name **Gamer**.
- **PASS** — `make metadata-check`.
- **PASS** — direct GTK parser load of `style.css` and `gamer.css`; no parsing
  error or warning was emitted.
- **PASS** — Python syntax for 137 files and Bash syntax for bundled scripts
  and `pkgbuild/PKGBUILD`.
- **BLOCKED** — `make typecheck`: `pyright` is not installed in this checkout's
  environment.
- **BLOCKED** — `make test`: the required `xvfb-run` command is not installed.
  An offscreen Broadway fallback reached 31% and then segfaulted inside GTK
  while constructing an existing `action_row` test; this is not counted as a
  passing full-suite run.
- **FAILED (pre-existing workspace artifact)** — `make release-check`: the
  ignored `pkgbuild/src/big-remote-play/dist` wheel and nested release-tool
  `__pycache__` were already present before this work. Normal project caches
  created during validation were removed with `make clean`; the pre-existing
  packaging tree was preserved.

## Manual visual review

The source application was opened with an isolated temporary configuration in
Gamer. Home was reviewed at 1150×770, 880×720, 720×720 and the 360px content
minimum (410px including window decoration). The sidebar collapsed at the
existing breakpoint, cards stacked, text wrapped, keyboard focus remained
visible, and no new clipping or CSS/GTK warning appeared. The Home hero, Guided
setup, Share/Connect cards and private-network overview showed the same surface,
accent and status language. Temporary screenshots were deleted and are not
release artifacts.

Automated behaviour also exercised startup in Gamer, repeated Gamer selection,
Gamer → Light → Gamer → Automatic, persistence, safe fallback, and suppression
of the overlay under libadwaita's high-contrast debug mode.

## Limitations

Automated GTK tests cannot establish real streaming performance, GPU/audio
compatibility, VPN authorization, router reachability or screen-reader quality.
A target-machine release review is still required for Orca, a complete
keyboard-only tour, 150% text, real high contrast, RTL/CJK rendering, every
dialog/state, and repeated manual switching among all four appearances. The
Gamer overlay adds no custom animation, fixed width or fixed-size typography,
so reduced motion and the existing adaptive/text-direction contracts remain
owned by GTK and the base interface.
