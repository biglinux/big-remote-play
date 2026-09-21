"""Small native building blocks for the task-first UI. No service side effects."""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GObject, Gtk, Pango  # type: ignore

from big_remote_play.utils.i18n import _
from big_remote_play.utils.icons import create_icon_widget, set_icon


def name_icon_button(button: Gtk.Button, label: str, description: str = "") -> Gtk.Button:
    """Give an icon-only button one concise visible and accessible name."""
    button.set_tooltip_text(label)
    properties = [Gtk.AccessibleProperty.LABEL]
    values: list[str] = [label]
    if description:
        properties.append(Gtk.AccessibleProperty.DESCRIPTION)
        values.append(description)
    button.update_property(properties, values)
    return button


def icon_tile(name: str, *, large: bool = False, tone: str = "accent") -> Gtk.Widget:
    tile = Gtk.Box(halign=Gtk.Align.START, valign=Gtk.Align.CENTER)
    tile.add_css_class("brp-icon-tile")
    tile.add_css_class(tone)
    image = create_icon_widget(name, size=32 if large else 16)
    for edge in ("top", "bottom", "start", "end"):
        getattr(image, f"set_margin_{edge}")(12 if large else 10)
    tile.append(image)
    return tile


def intro(title: str, description: str, icon: str, *, tone: str = "accent") -> Gtk.Box:
    """A compact task heading, never a wall of instructions before the controls."""
    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
    box.add_css_class("brp-intro")
    box.append(icon_tile(icon, large=True, tone=tone))
    text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, hexpand=True)
    heading = Gtk.Label(label=title, xalign=0, wrap=True)
    heading.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
    heading.add_css_class("title-2")
    text.append(heading)
    subtitle = Gtk.Label(label=description, xalign=0, wrap=True)
    subtitle.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
    subtitle.add_css_class("dim-label")
    text.append(subtitle)
    box.append(text)
    return box


def action_row(
    title: str,
    subtitle: str,
    icon: str,
    callback: Callable[[], None],
    *,
    icon_style: Literal["plain", "tile"] = "plain",
    tone: str = "accent",
) -> Adw.ActionRow:
    """Native action row with an explicit icon hierarchy.

    Routine list actions use a monochrome, theme-coloured prefix. ``tile`` is
    reserved for a small number of high-level choices (for example selecting a
    VPN provider), so blue is meaningful instead of accidental decoration.
    """
    row = Adw.ActionRow(title=title, subtitle=subtitle, activatable=True, use_markup=False)
    row.set_title_lines(0)
    row.set_subtitle_lines(0)
    if icon_style == "tile":
        row.add_prefix(icon_tile(icon, tone=tone))
    else:
        image = create_icon_widget(icon, size=18, css_class="brp-row-icon")
        image.set_valign(Gtk.Align.CENTER)
        row.add_prefix(image)
    row.add_suffix(create_icon_widget("go-next-symbolic", size=16))
    row.connect("activated", lambda _row: callback())
    return row


def boxed_rows(*rows: Gtk.Widget) -> Gtk.ListBox:
    box = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
    box.add_css_class("boxed-list")
    box.add_css_class("brp-boxed")
    for row in rows:
        box.append(row)
    return box


def note(text: str, icon: str = "brp-dialog-information-symbolic") -> Gtk.Box:
    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
    box.add_css_class("brp-note")
    image = create_icon_widget(icon, size=16)
    image.set_valign(Gtk.Align.START)
    image.set_margin_top(2)
    box.append(image)
    label = Gtk.Label(label=text, xalign=0, wrap=True, hexpand=True)
    box.append(label)
    return box


# A sheet shorter than this reads as a broken strip rather than a window: the
# header, its description and a couple of rows already need this much.
MIN_SHEET_HEIGHT = 360


def preferences_dialog(
    title: str,
    groups: list[Adw.PreferencesGroup],
    *,
    description: str = "",
    height: int = 520,
) -> Adw.PreferencesDialog:
    dialog = Adw.PreferencesDialog(title=title)
    dialog.set_content_width(680)
    # A negative height asks for the natural size, which libadwaita resolves to
    # its own 150px floor for a sparse page. Keep the request above that.
    dialog.set_content_height(max(height, MIN_SHEET_HEIGHT))
    dialog.add_css_class("brp-dialog")
    page = Adw.PreferencesPage(title=title)
    if description:
        heading = Adw.PreferencesGroup(description=description)
        page.add(heading)
    for group in groups:
        page.add(group)
    dialog.add(page)
    return dialog


class ChoiceGroup(Adw.PreferencesGroup):
    """A one-of-many choice as visible radio rows instead of a dropdown.

    Mirrors the small part of the ``Adw.ComboRow`` API the views use
    (``get_selected``/``set_selected``/``get_selected_item`` plus the
    ``selected`` and ``selected-item`` notifications) so a combo can be
    replaced without rewriting its readers.
    """

    __gtype_name__ = "BrpChoiceGroup"

    selected = GObject.Property(type=int, default=0)
    selected_item = GObject.Property(type=GObject.Object)

    def __init__(self, labels: list[str], *, subtitles: list[str] | None = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self._model = Gtk.StringList.new(list(labels))
        self._buttons: list[Gtk.CheckButton] = []
        # Exposed so a caller can react to re-activating the option that is
        # already selected (picking "Custom" again must reopen its sheet).
        self.rows: list[Adw.ActionRow] = []
        for index, label in enumerate(labels):
            row = Adw.ActionRow(title=label, activatable=True, use_markup=False)
            row.set_title_lines(0)
            if subtitles and index < len(subtitles) and subtitles[index]:
                row.set_subtitle(subtitles[index])
                row.set_subtitle_lines(0)
            check = Gtk.CheckButton(valign=Gtk.Align.CENTER)
            check.update_property([Gtk.AccessibleProperty.LABEL], [label])
            if subtitles and index < len(subtitles) and subtitles[index]:
                check.update_property([Gtk.AccessibleProperty.DESCRIPTION], [subtitles[index]])
            if self._buttons:
                check.set_group(self._buttons[0])
            check.connect("toggled", self._on_toggled, index)
            row.add_prefix(check)
            row.set_activatable_widget(check)
            self._buttons.append(check)
            self.rows.append(row)
            self.add(row)
        if self._buttons:
            self._buttons[0].set_active(True)
            self.props.selected_item = self._model.get_item(0)

    def _on_toggled(self, button: Gtk.CheckButton, index: int) -> None:
        if button.get_active():
            self.set_selected(index)

    def get_selected(self) -> int:
        return self.props.selected

    def set_selected(self, index: int) -> None:
        """Select an option, or pass -1 to leave every option unselected."""
        if index < 0:
            for button in self._buttons:
                button.set_active(False)
            if self.props.selected != -1:
                self.props.selected = -1
                self.props.selected_item = None
            return
        if index >= len(self._buttons):
            return
        self._buttons[index].set_active(True)
        if self.props.selected != index:
            self.props.selected = index
            self.props.selected_item = self._model.get_item(index)

    def get_selected_item(self) -> Gtk.StringObject | None:
        return self.props.selected_item


def sidebar_dialog(title: str, pages: list[tuple[str, str, Gtk.Widget]], *, width: int = 900, height: int = 660) -> Adw.Dialog:
    """Dialog with a category sidebar, for settings too long for one page."""
    dialog = Adw.Dialog(title=title)
    dialog.set_size_request(320, 240)
    dialog.add_css_class("brp-dialog")
    dialog.set_content_width(width)
    dialog.set_content_height(height)

    stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
    categories = Gtk.ListBox()
    categories.add_css_class("navigation-sidebar")

    content_title = Adw.WindowTitle()
    split = Adw.NavigationSplitView(min_sidebar_width=210, max_sidebar_width=280)

    def on_selected(_list: Gtk.ListBox, row: Gtk.ListBoxRow | None) -> None:
        if row is None:
            return
        stack.set_visible_child_name(row.brp_category)
        content_title.set_title(row.brp_category)
        if split.get_collapsed():
            split.set_show_content(True)

    categories.connect("row-selected", on_selected)

    for name, icon, child in pages:
        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True, child=child)
        stack.add_named(scroll, name)
        row = Gtk.ListBoxRow()
        row.brp_category = name
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12, margin_top=6, margin_bottom=6, margin_start=8, margin_end=8)
        box.append(create_icon_widget(icon, size=16))
        box.append(Gtk.Label(label=name, xalign=0, hexpand=True, wrap=True))
        row.set_child(box)
        row.update_property([Gtk.AccessibleProperty.LABEL], [name])
        categories.append(row)

    sidebar_view = Adw.ToolbarView()
    sidebar_view.add_top_bar(Adw.HeaderBar())
    sidebar_view.set_content(Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True, child=categories))

    header = Adw.HeaderBar()
    header.set_title_widget(content_title)
    content_view = Adw.ToolbarView()
    content_view.add_top_bar(header)
    content_view.set_content(stack)

    split.set_sidebar(Adw.NavigationPage.new(sidebar_view, _("Preferences")))
    split.set_content(Adw.NavigationPage.new(content_view, title))
    dialog.set_child(split)

    narrow = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 620px"))
    narrow.add_setter(split, "collapsed", True)
    dialog.add_breakpoint(narrow)

    categories.select_row(categories.get_row_at_index(0))
    return dialog


def add_preferences_intro(page: Adw.PreferencesPage, title: str, description: str, icon: str) -> None:
    group = Adw.PreferencesGroup()
    group.add(intro(title, description, icon))
    page.add(group)


def set_row_icon(row: Adw.ActionRow | Adw.ExpanderRow, name: str) -> None:
    """Monochrome native row prefix, recoloured by the current Adwaita theme."""
    image = getattr(row, "_brp_prefix_icon", None)
    if image is None:
        image = create_icon_widget(name, size=18, css_class="brp-row-icon")
        image.set_valign(Gtk.Align.CENTER)
        row.add_prefix(image)
        row._brp_prefix_icon = image
    else:
        set_icon(image, name)


def content_dialog(title: str, content: Gtk.Widget, *, description: str = "", width: int = 680, height: int = 600) -> Adw.Dialog:
    """Scrollable adaptive utility sheet with a native close control."""
    dialog = Adw.Dialog(title=title)
    dialog.set_size_request(320, 240)
    dialog.add_css_class("brp-dialog")
    dialog.set_content_width(width)
    dialog.set_content_height(height)
    toolbar = Adw.ToolbarView()
    toolbar.add_top_bar(Adw.HeaderBar())
    body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
    for edge in ("top", "bottom", "start", "end"):
        getattr(body, f"set_margin_{edge}")(16)
    if description:
        label = Gtk.Label(label=description, xalign=0, wrap=True)
        label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        label.add_css_class("dim-label")
        body.append(label)
    body.append(content)
    scroll = Gtk.ScrolledWindow(vexpand=True)
    scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    scroll.set_child(body)
    toolbar.set_content(scroll)
    dialog.set_child(toolbar)
    return dialog
