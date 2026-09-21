"""Parser for the machine-readable markers emitted by the network setup scripts.

The scripts print locale-independent ASCII markers next to their human prose so
that parsing is decoupled from translation (the prose can be localized via
gettext without breaking capture):

    BRP_DATA <key>=<value>    captured key/value data (key is [A-Za-z0-9_])
    BRP_PHASE <fraction>      progress fraction in 0..1

Any other line is human prose, shown verbatim in the progress UI.
"""

import re

_ANSI = re.compile(r"\x1b\[[0-9;]*[mK]")
_DATA = re.compile(r"^BRP_DATA\s+([A-Za-z0-9_]+)=(.*)$")
_PHASE = re.compile(r"^BRP_PHASE\s+([0-9]*\.?[0-9]+)$")


def parse_script_line(raw: str) -> tuple:
    """Classify one script output line.

    Returns one of:
        ("data", key, value)   - a BRP_DATA marker
        ("phase", fraction)    - a BRP_PHASE marker (float, clamped to 0..1)
        ("text", clean)        - human prose (ANSI-stripped, trimmed)
    """
    clean = _ANSI.sub("", raw).strip()
    m = _DATA.match(clean)
    if m:
        return ("data", m.group(1), m.group(2).strip())
    m = _PHASE.match(clean)
    if m:
        frac = float(m.group(1))
        return ("phase", max(0.0, min(1.0, frac)))
    return ("text", clean)
