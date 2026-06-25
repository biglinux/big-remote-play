"""Reusable UI builders shared across the Big Remote Play screens.

Pure widget factories (no domain logic), so welcome, VPN selector, guest
discover, host and the VPN connect wizard share the same explanatory components
seen in the mockups: "how it works" step strips, side helper cards, a wizard
stepper, difficulty pills and the VPN comparison table.

Every interactive/iconographic element gets an accessible label so AT-SPI
exposes it (icon-only widgets have no inferable name).
"""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # type: ignore  # noqa: E402

from big_remote_play.utils.icons import create_icon_widget  # noqa: E402
from big_remote_play.utils.i18n import _  # noqa: E402

# Difficulty level -> (translated label, CSS modifier). Keys are stable English.
_DIFFICULTY_LEVELS: dict[str, tuple[str, str]] = {
    "beginner": (_("Beginner"), "easy"),
    "intermediate": (_("Intermediate"), "medium"),
    "advanced": (_("Advanced"), "hard"),
}


def create_page_header(title: str, subtitle: str | None = None, icon_name: str | None = None) -> Gtk.Widget:
    """Compact page heading used by the main content pages."""
    header = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    header.add_css_class("page-header")

    title_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    title_row.set_halign(Gtk.Align.START)

    title_label = Gtk.Label(label=title)
    title_label.add_css_class("page-title")
    title_label.set_halign(Gtk.Align.START)
    title_label.set_wrap(True)
    title_row.append(title_label)

    if icon_name:
        icon = create_icon_widget(icon_name, size=24)
        icon.add_css_class("page-title-icon")
        icon.set_valign(Gtk.Align.CENTER)
        title_row.append(icon)

    header.append(title_row)
    if subtitle:
        subtitle_label = Gtk.Label(label=subtitle)
        subtitle_label.add_css_class("page-subtitle")
        subtitle_label.set_halign(Gtk.Align.START)
        subtitle_label.set_wrap(True)
        subtitle_label.set_max_width_chars(80)
        header.append(subtitle_label)
    return header


def create_stack_tab_strip(stack: Adw.ViewStack, accessible_label: str) -> Gtk.Widget:
    """Framed view switcher that reads visually as tabs and stays AT-SPI actionable."""
    switcher = Adw.InlineViewSwitcher()
    switcher.set_stack(stack)
    switcher.set_display_mode(Adw.InlineViewSwitcherDisplayMode.BOTH)
    switcher.add_css_class("round")
    switcher.update_property([Gtk.AccessibleProperty.LABEL], [accessible_label])

    strip = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
    strip.add_css_class("tab-strip")
    strip.set_halign(Gtk.Align.CENTER)
    strip.update_property([Gtk.AccessibleProperty.LABEL], [accessible_label])
    strip.append(switcher)
    return strip


def create_steps_strip(steps: list[tuple[str, str, str]]) -> Gtk.Widget:
    """ "How it works" strip: numbered steps with icon + title + description.

    `steps` is an ordered list of (icon_name, title, description). Steps are laid
    out horizontally with a chevron between them; the whole strip sits in a card.
    """
    card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
    card.add_css_class("steps-strip")

    header = Gtk.Label(label=_("How it works"))
    header.add_css_class("title-4")
    header.set_halign(Gtk.Align.START)
    card.append(header)

    row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    row.set_homogeneous(False)

    for index, (icon_name, title, description) in enumerate(steps):
        if index > 0:
            chevron = create_icon_widget("go-next-symbolic", size=16)
            chevron.add_css_class("dim-label")
            chevron.set_valign(Gtk.Align.CENTER)
            row.append(chevron)

        step = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        step.set_hexpand(True)
        step.set_valign(Gtk.Align.CENTER)

        number = Gtk.Label(label=str(index + 1))
        number.add_css_class("step-number")
        number.set_valign(Gtk.Align.CENTER)
        step.append(number)

        icon = create_icon_widget(icon_name, size=22)
        icon.add_css_class("accent")
        icon.set_valign(Gtk.Align.CENTER)
        step.append(icon)

        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        text.set_valign(Gtk.Align.CENTER)
        title_lbl = Gtk.Label(label=title)
        title_lbl.add_css_class("caption-heading")
        title_lbl.set_halign(Gtk.Align.START)
        title_lbl.set_wrap(True)
        text.append(title_lbl)
        desc_lbl = Gtk.Label(label=description)
        desc_lbl.add_css_class("caption")
        desc_lbl.add_css_class("dim-label")
        desc_lbl.set_halign(Gtk.Align.START)
        desc_lbl.set_wrap(True)
        desc_lbl.set_max_width_chars(28)
        text.append(desc_lbl)
        step.append(text)

        row.append(step)

    card.append(row)
    return card


def create_helper_card(title: str, icon_name: str, items: list[tuple[str, str, str]]) -> Gtk.Widget:
    """Side "what you'll need / if you can't find it" explanatory card.

    `items` is a list of (icon_name, title, description) rows. Used as the right
    column next to a form or list.
    """
    card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
    card.add_css_class("helper-card")

    head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
    head_icon = create_icon_widget(icon_name, size=20)
    head_icon.add_css_class("accent")
    head_icon.set_valign(Gtk.Align.CENTER)
    head.append(head_icon)
    head_lbl = Gtk.Label(label=title)
    head_lbl.add_css_class("title-4")
    head_lbl.set_halign(Gtk.Align.START)
    head_lbl.set_wrap(True)
    head.append(head_lbl)
    card.append(head)

    for row_icon, row_title, row_desc in items:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        row.add_css_class("helper-row")

        ricon = create_icon_widget(row_icon, size=18)
        ricon.add_css_class("dim-label")
        ricon.set_valign(Gtk.Align.START)
        ricon.set_margin_top(2)
        row.append(ricon)

        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        text.set_hexpand(True)
        t = Gtk.Label(label=row_title)
        t.add_css_class("caption-heading")
        t.set_halign(Gtk.Align.START)
        t.set_wrap(True)
        text.append(t)
        d = Gtk.Label(label=row_desc)
        d.add_css_class("caption")
        d.add_css_class("dim-label")
        d.set_halign(Gtk.Align.START)
        d.set_wrap(True)
        d.set_max_width_chars(32)
        text.append(d)
        row.append(text)

        card.append(row)

    return card


def create_wizard_stepper(steps: list[str], active_index: int) -> Gtk.Widget:
    """Top 1-2-3 wizard stepper. `steps` are the labels; `active_index` is 0-based.

    Steps before the active one render as completed, the active one is highlighted.
    """
    strip = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    strip.add_css_class("wizard-stepper")
    strip.set_halign(Gtk.Align.CENTER)

    for index, label_text in enumerate(steps):
        if index > 0:
            sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
            sep.add_css_class("step-connector")
            sep.set_valign(Gtk.Align.CENTER)
            sep.set_hexpand(True)
            sep.set_size_request(40, -1)
            strip.append(sep)

        step = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        step.set_valign(Gtk.Align.CENTER)

        number = Gtk.Label(label=str(index + 1))
        number.add_css_class("step-number")
        if index < active_index:
            number.add_css_class("completed")
        elif index == active_index:
            number.add_css_class("active")
        number.set_valign(Gtk.Align.CENTER)
        step.append(number)

        lbl = Gtk.Label(label=label_text)
        lbl.add_css_class("caption-heading" if index == active_index else "caption")
        if index != active_index:
            lbl.add_css_class("dim-label")
        step.append(lbl)

        strip.append(step)

    return strip


def create_difficulty_pill(level: str) -> Gtk.Widget:
    """Colored difficulty pill. `level` in {beginner, intermediate, advanced}."""
    label_text, modifier = _DIFFICULTY_LEVELS.get(level.lower(), (level, "medium"))
    pill = Gtk.Label(label=label_text)
    pill.add_css_class("difficulty-pill")
    pill.add_css_class(modifier)
    pill.set_halign(Gtk.Align.CENTER)
    return pill


class MetricTile(Gtk.Box):
    """Compact metric tile: icon + label, a big current value and a mini sparkline.

    Used by the Server status card (mockup 05) to show Latency / FPS / Bandwidth.
    Feed it with `update(values_norm, value_text)` where `values_norm` is a list of
    points already normalized to 0..1; `rgba` is the sparkline color (0..1 tuple).
    """

    def __init__(self, icon_name: str, label: str, color_class: str, rgba: tuple) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.add_css_class("metric-tile")
        self._values: list[float] = []
        self._rgba = rgba

        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        icon = create_icon_widget(icon_name, size=16)
        icon.add_css_class(color_class)
        head.append(icon)
        lbl = Gtk.Label(label=label)
        lbl.add_css_class("caption")
        lbl.add_css_class("dim-label")
        head.append(lbl)
        self.append(head)

        self._value_label = Gtk.Label(label="--")
        self._value_label.add_css_class("title-3")
        self._value_label.set_halign(Gtk.Align.START)
        self.append(self._value_label)

        self._spark = Gtk.DrawingArea()
        self._spark.set_content_height(28)
        self._spark.set_hexpand(True)
        self._spark.set_draw_func(self._draw_sparkline)
        self.append(self._spark)

    def update(self, values_norm: list[float], value_text: str) -> None:
        self._values = values_norm
        self._value_label.set_label(value_text)
        self._spark.queue_draw()

    def _draw_sparkline(self, _area, cr, width: int, height: int) -> None:
        vals = self._values
        r, g, b, a = self._rgba
        if len(vals) < 2:
            return
        n = len(vals)
        step = width / max(n - 1, 1)
        pad = 3.0
        usable = max(height - 2 * pad, 1)

        def y(v: float) -> float:
            return pad + (1.0 - max(0.0, min(1.0, v))) * usable

        # Soft area fill under the line.
        cr.move_to(0, height)
        for i, v in enumerate(vals):
            cr.line_to(i * step, y(v))
        cr.line_to((n - 1) * step, height)
        cr.close_path()
        cr.set_source_rgba(r, g, b, 0.12)
        cr.fill()

        # The line itself.
        cr.set_line_width(2.0)
        cr.set_source_rgba(r, g, b, a)
        cr.move_to(0, y(vals[0]))
        for i, v in enumerate(vals[1:], start=1):
            cr.line_to(i * step, y(v))
        cr.stroke()


def create_comparison_table(headers: list[str], rows: list[tuple[str, list[str]]]) -> Gtk.Widget:
    """Comparison grid: first column = row label, remaining = one cell per header.

    `headers` are the provider column titles (the top-left corner stays blank).
    `rows` is a list of (row_label, [cell, cell, ...]) with one cell per header.
    """
    grid = Gtk.Grid()
    grid.add_css_class("comparison-table")
    grid.set_column_homogeneous(True)
    grid.set_row_spacing(0)
    grid.set_column_spacing(0)

    # Header row (corner stays empty so the provider names align over the cells).
    for col, header_text in enumerate(headers):
        cell = Gtk.Label(label=header_text)
        cell.add_css_class("comparison-header")
        cell.set_halign(Gtk.Align.CENTER)
        cell.set_hexpand(True)
        grid.attach(cell, col + 1, 0, 1, 1)

    for r, (row_label, cells) in enumerate(rows):
        label = Gtk.Label(label=row_label)
        label.add_css_class("comparison-cell")
        label.add_css_class("comparison-rowlabel")
        label.set_halign(Gtk.Align.START)
        label.set_hexpand(True)
        grid.attach(label, 0, r + 1, 1, 1)

        for col, value in enumerate(cells):
            cell = Gtk.Label(label=value)
            cell.add_css_class("comparison-cell")
            cell.set_halign(Gtk.Align.CENTER)
            cell.set_wrap(True)
            cell.set_hexpand(True)
            grid.attach(cell, col + 1, r + 1, 1, 1)

    return grid
