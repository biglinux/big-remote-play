"""Connect → Shortcuts & Instructions: controls of each Moonlight app.

Every entry was checked against Moonlight itself (its setup guide, and the
source of Moonlight for Android and for iPhone/iPad), so a platform lists
only what that app really does. Shortcuts of Moonlight on a computer are
handled by Moonlight there and are not sent to the game PC; gestures and
keys on a phone are what Moonlight turns into mouse and keyboard input.
See docs/mobile-controls.md.
"""

from __future__ import annotations

from dataclasses import dataclass

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # type: ignore  # noqa: E402

from big_remote_play.utils.i18n import _  # noqa: E402

from .components import intro  # noqa: E402

# The guide, readable on the phone itself (static page, no service).
GUIDE_URL = "https://github.com/biglinux/big-remote-play/blob/main/docs/mobile-controls.md"


@dataclass(frozen=True)
class Control:
    how: str  # keys or gesture
    what: str


@dataclass(frozen=True)
class Section:
    title: str
    description: str
    controls: tuple[Control, ...]


def computer_sections() -> tuple[Section, ...]:
    return (
        Section(
            _("During the game"),
            _("Moonlight on this computer handles these keys; they are not sent to the game PC."),
            (
                Control("Ctrl+Alt+Shift+Q", _("Leave the game. It keeps running on the game PC.")),
                Control("Ctrl+Alt+Shift+Z", _("Release or capture the mouse and keyboard")),
                Control("Ctrl+Alt+Shift+X", _("Switch between full screen and a window")),
                Control("Ctrl+Alt+Shift+D", _("Minimize the game window")),
                Control("Ctrl+Alt+Shift+S", _("Show or hide the connection statistics")),
                Control("Ctrl+Alt+Shift+M", _("Switch the mouse between game mode and desktop mode")),
                Control("Ctrl+Alt+Shift+V", _("Type the text copied on this computer into the game PC")),
                Control("Ctrl+Alt+Shift+N", _("Show or hide the game PC's mouse pointer in the picture (handled by the game PC)")),
            ),
        ),
        Section(
            _("Desktop mouse mode"),
            _("When Moonlight's mouse is set for remote desktop use."),
            (
                Control("Ctrl+Alt+Shift+C", _("Show this computer's mouse pointer")),
                Control("Ctrl+Alt+Shift+L", _("Keep the pointer inside the game window")),
            ),
        ),
    )


def _touch_section(platform_note: str) -> Section:
    return Section(
        _("Touch: mouse"),
        platform_note,
        (
            Control(_("Slide one finger"), _("Move the pointer")),
            Control(_("Tap with one finger"), _("Left click")),
            Control(_("Hold one finger, then tap with a second"), _("Right click")),
            Control(_("Hold one finger for half a second, then move it"), _("Click and drag, for example to move a window")),
            Control(_("Drag two fingers up or down"), _("Scroll")),
            Control(_("Tap with three fingers"), _("Show or hide the keyboard")),
        ),
    )


def _pointer_section() -> Section:
    return Section(
        _("Mouse pointer"),
        _("The pointer you see is drawn by the game PC into the picture. If it is missing, open Share → Support → The mouse pointer is missing on the game PC."),
        (Control(_("No pointer, but taps still click"), _("The game PC's screen capture leaves it out; Big Remote Play picks one that draws it")),),
    )


def android_sections() -> tuple[Section, ...]:
    return (
        _touch_section(_("With Moonlight's setting “Use the touchscreen as a trackpad”. Without it, a tap clicks where you touch and a long press is a right click.")),
        _pointer_section(),
        Section(
            _("Keyboard"),
            _("The phone's own keyboard has no Ctrl, Alt, Esc or function keys."),
            (
                Control(_("Tap with three fingers"), _("Open the phone's keyboard to type on the game PC")),
                Control(_("Bluetooth or USB keyboard"), _("Every key and shortcut, such as Ctrl+C or Alt+Tab, goes to the game PC")),
                Control(_("A keyboard app with Ctrl and Alt"), _("Gives the phone those keys when there is no physical keyboard")),
            ),
        ),
        Section(
            _("Leaving"),
            "",
            (
                Control(_("Back button"), _("Leave the game. It keeps running on the game PC.")),
                Control(_("Quit Session"), _("In Moonlight's list of apps of that computer: closes the game on the game PC")),
            ),
        ),
    )


def apple_sections() -> tuple[Section, ...]:
    return (
        _touch_section(_("In touchpad mode. In touchscreen mode a tap clicks where you touch, a long press is a right click, and two fingers zoom and move the picture.")),
        _pointer_section(),
        Section(
            _("Keyboard"),
            _("The keyboard opened with three fingers has a bar of extra keys."),
            (
                Control(_("Tap with three fingers"), _("Open the keyboard with the extra keys bar")),
                Control(
                    "Win · Esc · Tab · Shift · Ctrl · Alt · Delete",
                    _("The extra keys. Win, Shift, Ctrl and Alt stay pressed until tapped again, so Ctrl, then Alt, then Delete sends Ctrl+Alt+Delete."),
                ),
                Control(_("Bluetooth keyboard"), _("Every key and shortcut goes to the game PC")),
            ),
        ),
        Section(
            _("Leaving"),
            "",
            (
                Control(_("Swipe from the left edge of the screen"), _("Leave the game. It keeps running on the game PC.")),
                Control(_("Quit Session"), _("In Moonlight's list of apps of that computer: closes the game on the game PC")),
            ),
        ),
    )


def controller_sections() -> tuple[Section, ...]:
    return (
        Section(
            _("Any Moonlight"),
            _("A controller connected to the device with Moonlight works as a controller on the game PC."),
            (Control(_("Controller buttons and sticks"), _("Sent to the game as they are")),),
        ),
        Section(
            _("Moonlight for Android"),
            "",
            (
                Control(_("Start + Select"), _("The controller's guide (home) button")),
                Control(_("L1 + Start"), _("Select, on controllers without that button")),
                Control(_("Hold Start"), _("Use the controller as a mouse: a stick moves the pointer, A clicks, B right-clicks")),
                Control(_("On-screen controls"), _("Moonlight's setting “Show on-screen controls” draws a controller on the touchscreen")),
            ),
        ),
    )


def _page(sections: tuple[Section, ...], heading: str, description: str, icon: str) -> Gtk.Widget:
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20, margin_top=16, margin_bottom=16, margin_start=16, margin_end=16)
    box.append(intro(heading, description, icon))
    for section in sections:
        group = Adw.PreferencesGroup(title=section.title)
        if section.description:
            group.set_description(section.description)
        for control in section.controls:
            row = Adw.ActionRow(title=control.how, subtitle=control.what, use_markup=False)
            row.set_title_lines(0)
            row.set_subtitle_lines(0)
            group.add(row)
        box.append(group)
    clamp = Adw.Clamp(maximum_size=640, child=box)
    scroll = Gtk.ScrolledWindow(vexpand=True, child=clamp)
    scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    return scroll


def _phone_guide() -> Gtk.Widget:
    from .network_common import qr_code

    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_top=16, margin_bottom=16, margin_start=16, margin_end=16)
    box.append(intro(_("Read it on your phone"), _("Point the phone's camera at the code to open this guide there, while you play."), "brp-phone-symbolic"))
    box.append(qr_code(GUIDE_URL, size=200, description=_("QR code that opens the guide to Moonlight's controls")))
    link = Gtk.LinkButton(uri=GUIDE_URL, label=_("Open the guide in the browser"), halign=Gtk.Align.CENTER)
    box.append(link)
    clamp = Adw.Clamp(maximum_size=640, child=box)
    scroll = Gtk.ScrolledWindow(vexpand=True, child=clamp)
    scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    return scroll


def controls_help_dialog() -> Adw.Dialog:
    dialog = Adw.Dialog(title=_("Shortcuts & Instructions"))
    dialog.add_css_class("brp-dialog")
    dialog.set_content_width(620)
    dialog.set_content_height(680)
    dialog.set_size_request(320, 300)
    stack = Adw.ViewStack()
    pages = (
        ("computer", _("Computer"), "brp-input-keyboard-symbolic", _page(computer_sections(), _("Moonlight on a computer"), _("Linux, Windows and macOS."), "brp-input-keyboard-symbolic")),
        ("android", "Android", "brp-phone-symbolic", _page(android_sections(), _("Moonlight for Android"), _("Phones and tablets."), "brp-phone-symbolic")),
        (
            "apple",
            _("iPhone and iPad"),
            "brp-phone-symbolic",
            _page(apple_sections(), _("Moonlight for iPhone and iPad"), _("Also Apple TV, whose remote moves the pointer and clicks."), "brp-phone-symbolic"),
        ),
        ("controller", _("Controllers"), "brp-input-gaming-symbolic", _page(controller_sections(), _("Controllers"), _("Gamepads on any device."), "brp-input-gaming-symbolic")),
        ("phone_guide", _("On the Phone"), "brp-help-browser-symbolic", _phone_guide()),
    )
    for name, title, icon, widget in pages:
        stack.add_titled_with_icon(widget, name, title, icon)
    switcher = Adw.ViewSwitcher(stack=stack, policy=Adw.ViewSwitcherPolicy.WIDE)
    header = Adw.HeaderBar(title_widget=switcher)
    bottom = Adw.ViewSwitcherBar(stack=stack)
    toolbar = Adw.ToolbarView(content=stack)
    toolbar.add_top_bar(header)
    toolbar.add_bottom_bar(bottom)
    # Narrow windows move the tabs to the bottom bar.
    breakpoint = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 560sp"))
    breakpoint.add_setter(header, "title-widget", Adw.WindowTitle(title=_("Shortcuts & Instructions")))
    breakpoint.add_setter(bottom, "reveal", True)
    dialog.add_breakpoint(breakpoint)
    dialog.set_child(toolbar)
    return dialog
