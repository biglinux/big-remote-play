"""Connect → Shortcuts & Instructions, per Moonlight app.

The old dialog listed only computer shortcuts, which a phone cannot send.
Each platform must list only what that Moonlight app does (checked against
Moonlight's guide and the Android and iOS sources).
"""

from __future__ import annotations

from big_remote_play.ui import controls_help as help_
from test_ui_task_flows import drain, ui as _ui_fixture  # noqa: F401

ui = _ui_fixture


def _all(sections) -> list[str]:
    return [f"{control.how} {control.what}" for section in sections for control in section.controls]


def test_computer_shortcuts_are_moonlight_combinations_only():
    keys = [control.how for section in help_.computer_sections() for control in section.controls]
    assert keys and all(key.startswith("Ctrl+Alt+Shift+") for key in keys)
    assert "Ctrl+Alt+Shift+Q" in keys


def test_each_phone_lists_its_own_way_out_and_keyboard():
    android, apple = " ".join(_all(help_.android_sections())), " ".join(_all(help_.apple_sections()))
    assert "Back button" in android and "left edge" not in android
    assert "left edge" in apple and "Back button" not in apple
    # iPhone's keyboard has the extra keys bar; Android's system keyboard does not.
    assert "Ctrl" in apple and "Ctrl+Alt+Shift" not in android + apple
    assert "three fingers" in android and "three fingers" in apple


def test_the_dialog_opens_with_one_tab_per_platform_and_a_phone_guide(ui):
    dialog = ui.guest_view.show_shortcuts_dialog()
    try:
        drain()
        stack = dialog.get_child().get_content()
        names = [page.get_name() for page in stack.get_pages()]
        assert names == ["computer", "android", "apple", "controller", "phone_guide"]
        assert help_.GUIDE_URL.startswith("https://") and help_.GUIDE_URL.endswith("docs/mobile-controls.md")
    finally:
        dialog.force_close()
