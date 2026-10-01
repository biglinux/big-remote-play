#!/usr/bin/env python3
"""Find visible text that reaches GTK/libadwaita without gettext.

The check reads the Python sources (it never imports them) and looks only at
the places where text becomes visible or is read by assistive technologies:
widget properties such as ``label=``/``title=``/``tooltip_text=``, setters
such as ``set_label()``, dialog responses, toasts, string-list models and
accessible labels. A literal there must be wrapped in ``_()``,
``ngettext()`` or ``pgettext()`` unless it is only product names, technical
identifiers, numbers, units, placeholders or URLs.

Exit status 1 lists ``path:line: property 'text'`` for each finding.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[2]
SOURCES = ROOT / "src" / "big_remote_play"

GETTEXT = {"_", "N_", "ngettext", "pgettext", "npgettext"}
VISIBLE_KEYWORDS = {
    "body",
    "button_label",
    "comments",
    "description",
    "heading",
    "label",
    "placeholder_text",
    "secondary_text",
    "subtitle",
    "text",
    "title",
    "tooltip_markup",
    "tooltip_text",
}
# Methods whose first argument is shown.
VISIBLE_SETTERS = {
    "add_link",
    "append_text",
    "new_with_label",
    "new_with_mnemonic",
    "set_body",
    "set_button_label",
    "set_comments",
    "set_description",
    "set_heading",
    "set_label",
    "set_markup",
    "set_placeholder_text",
    "set_subtitle",
    "set_text",
    "set_title",
    "set_tooltip_markup",
    "set_tooltip_text",
    "show_toast",
}
# Project helpers: positional arguments that are shown.
VISIBLE_HELPER_ARGUMENTS = {
    "action_row": (0, 1),
    "confirm": (1, 2, 3),
    "copy_row": (0,),
    "loading_row": (0,),
    "message_row": (0, 1),
    "name_icon_button": (1, 2),
    "note": (0,),
    "question_page": (1, 2, 3),
    "show_custom_input_dialog": (0, 1),
    "show_error_dialog": (0, 1),
    "_message_row": (0, 1),
    "_toast": (0, 1),
}
# Constructors whose first positional argument is shown.
VISIBLE_CONSTRUCTORS = {"Label", "Toast", "Banner", "StatusPage", "ButtonContent"}

# Spelled the same in every language: products, protocols and identifiers.
TECHNICAL_WORDS = {
    "AMF",
    "AppImage",
    "Bluetooth",
    "BigLinux",
    "Caddy",
    "Docker",
    "Flatpak",
    "Gamescope",
    "GNOME",
    "Headscale",
    "Heroic",
    "KWin",
    "Lutris",
    "Manjaro",
    "Moonlight",
    "NvFBC",
    "PipeWire",
    "Plasma",
    "Proton",
    "PulseAudio",
    "QuickSync",
    "Steam",
    "Sunshine",
    "Tailscale",
    "VideoToolbox",
    "Vulkan",
    "Wayland",
    "Wine",
    "WirePlumber",
    "ZeroTier",
    "wlroots",
    # Units.
    "FPS",
    "GB",
    "Hz",
    "kbps",
    "Mbps",
    "MB",
    "ms",
}
TECHNICAL_TEXT = {
    "Big Remote Play",
    "Intel Quick Sync",
    # A YouTube channel name in the About dialog.
    "System-infotech",
    "XDG Desktop Portal",
}

PLACEHOLDER = re.compile(r"\{[^{}]*\}|%(?:\([^)]+\))?[-#0 +]*\d*(?:\.\d+)?[sdifr%]")
URL = re.compile(r"\b(?:https?://|www\.)\S+|\S+@\S+\.\S+|\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:com|net|org|io|br|dev|app)\b")
WORD = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ][A-Za-zÀ-ÖØ-öø-ÿ'’-]*")


@dataclass(frozen=True)
class Finding:
    path: Path
    line: int
    sink: str
    text: str

    def __str__(self) -> str:
        path = self.path.relative_to(ROOT) if self.path.is_relative_to(ROOT) else self.path
        return f"{path}:{self.line}: {self.sink} {self.text!r}"


def is_human_text(text: str) -> bool:
    """True when the text contains words a person would need translated."""
    visible = text.strip()
    if not visible or visible in TECHNICAL_TEXT:
        return False
    visible = URL.sub(" ", PLACEHOLDER.sub(" ", visible))
    for name in TECHNICAL_TEXT:
        visible = visible.replace(name, " ")
    for word in WORD.findall(visible):
        if word == "OK":
            return True  # translated in many languages (ОК, 確定, حسنًا)
        if word in TECHNICAL_WORDS or word.isupper() or len(word) < 2:
            continue
        # Mixed-case identifiers such as renderD128 or x264 contain digits and
        # are matched separately by WORD; anything left with a lowercase
        # letter is prose.
        if any(character.islower() for character in word):
            return True
    return False


def _literal(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(part.value if isinstance(part, ast.Constant) else "{}" for part in node.values)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _literal(node.left), _literal(node.right)
        if left is not None or right is not None:
            return (left or "{}") + (right or "{}")
    return None


def _call_name(node: ast.Call) -> str:
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    if isinstance(node.func, ast.Name):
        return node.func.id
    return ""


def _shown_values(node: ast.Call) -> Iterator[tuple[str, ast.AST]]:
    name = _call_name(node)
    for keyword in node.keywords:
        if keyword.arg in VISIBLE_KEYWORDS:
            yield keyword.arg, keyword.value
    if name in VISIBLE_SETTERS and node.args:
        yield name, node.args[0]
    if name in VISIBLE_CONSTRUCTORS and node.args:
        yield name, node.args[0]
    for index in VISIBLE_HELPER_ARGUMENTS.get(name, ()):
        if index < len(node.args):
            yield name, node.args[index]
    # Gtk.StringList models (``model.append("…")``) feed combo rows.
    if name == "append" and isinstance(node.func, ast.Attribute) and node.args:
        receiver = node.func.value
        receiver_name = receiver.attr if isinstance(receiver, ast.Attribute) else receiver.id if isinstance(receiver, ast.Name) else ""
        if "model" in receiver_name.lower():
            yield "model.append", node.args[0]
    if name == "add_response" and len(node.args) >= 2:
        yield name, node.args[1]
    if name == "new" and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Attribute):
        owner = node.func.value.attr
        if owner in {"Toast", "Label", "Banner"} and node.args:
            yield f"{owner}.new", node.args[0]
        if owner == "NavigationPage" and len(node.args) >= 2:
            yield "NavigationPage.new", node.args[1]
        if owner == "StringList" and node.args and isinstance(node.args[0], (ast.List, ast.Tuple)):
            for element in node.args[0].elts:
                yield "StringList.new", element
    if name == "update_property" and len(node.args) == 2 and isinstance(node.args[1], (ast.List, ast.Tuple)):
        for element in node.args[1].elts:
            yield "accessible property", element


def _expressions(value: ast.AST) -> Iterator[ast.AST]:
    """The literal branches of a shown value (both sides of ``a if c else b``)."""
    if isinstance(value, ast.IfExp):
        yield from _expressions(value.body)
        yield from _expressions(value.orelse)
    elif isinstance(value, ast.BoolOp):
        for operand in value.values:
            yield from _expressions(operand)
    else:
        yield value


def _is_translated(node: ast.AST) -> bool:
    if isinstance(node, ast.Call):
        if isinstance(node.func, ast.Name) and node.func.id in GETTEXT:
            return True
        # _("…{name}…").format(name=value)
        if isinstance(node.func, ast.Attribute) and node.func.attr == "format":
            return _is_translated(node.func.value)
    return False


def findings_in(path: Path) -> list[Finding]:
    tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
    found: list[Finding] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for sink, value in _shown_values(node):
            for expression in _expressions(value):
                if _is_translated(expression):
                    continue
                text = _literal(expression)
                if text is not None and is_human_text(text):
                    found.append(Finding(path, getattr(expression, "lineno", node.lineno), sink, text))
    return found


def findings(root: Path = SOURCES) -> list[Finding]:
    result: list[Finding] = []
    for path in sorted(root.rglob("*.py")):
        result.extend(findings_in(path))
    return result


def main() -> int:
    found = findings()
    for finding in found:
        print(finding)
    print(f"checked {SOURCES.relative_to(ROOT)}; hardcoded visible text: {len(found)}")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
