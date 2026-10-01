"""Connect: choose a computer, and nothing else on the first tab.

Real GTK, fake discovery. The contracts: every discovered computer gets a card
(no hidden third or fourth one), choosing a card connects, and the technical
ways to connect live on the Advanced options tab.
"""

from __future__ import annotations

import pytest
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk  # noqa: E402

from test_ui_task_flows import drain, ui as _ui_fixture  # noqa: E402

ui = _ui_fixture


def hosts(count: int) -> list[dict]:
    return [{"name": f"PC {index + 1}", "ip": f"192.168.50.{index + 10}", "port": 47989} for index in range(count)]


def ancestors_of_type(widget, kind):
    found = []
    parent = widget.get_parent()
    while parent is not None:
        if isinstance(parent, kind):
            found.append(parent)
        parent = parent.get_parent()
    return found


@pytest.mark.parametrize("count", [0, 1, 2, 4, 10])
def test_every_discovered_computer_gets_a_card(ui, count):
    guest = ui.guest_view
    guest.update_hosts_list(hosts(count))
    drain()
    assert [host["name"] for host in guest.listed_hosts()] == [f"PC {index + 1}" for index in range(count)]
    assert all(guest.host_card(index).get_visible() for index in range(count))
    if count == 0:
        assert guest._empty_container.get_visible() and not guest._host_scroll.get_visible()
    else:
        assert not guest._empty_container.get_visible()


def test_the_cards_are_not_inside_a_height_capped_scroller(ui):
    """Two visible computers out of four looked like a two-computer limit."""
    guest = ui.guest_view
    guest.update_hosts_list(hosts(4))
    scrollers = ancestors_of_type(guest.host_card(3), Gtk.ScrolledWindow)
    # Only the page itself scrolls; no inner box caps the list's height.
    assert all(scroller.get_max_content_height() in (-1, 0) for scroller in scrollers)
    assert guest.hosts_list.get_max_children_per_line() >= 2  # two per line when there is room


def test_choosing_a_computer_connects_and_says_so(ui, monkeypatch):
    guest = ui.guest_view
    chosen = []

    def connect(host):
        chosen.append(host["name"])
        guest.show_loading(True)

    monkeypatch.setattr(guest, "connect_to_host", connect)
    guest.update_hosts_list(hosts(3))
    assert not guest.main_connect_btn.get_visible()  # nothing to stop yet
    guest.host_card(2).emit("clicked")
    assert chosen == ["PC 3"]
    assert guest.connect_status.get_visible() and "PC 3" in guest.connect_status.get_label()
    assert guest.main_connect_btn.get_visible() and guest.connect_btn_label.get_label() == "Stop"
    # One computer at a time: the others wait.
    assert not guest.host_card(0).get_sensitive()
    guest.host_card(0).emit("clicked")
    assert chosen == ["PC 3"]
    guest.show_loading(False)
    assert not guest.main_connect_btn.get_visible() and guest.host_card(0).get_sensitive()


def test_technical_ways_to_connect_live_on_the_advanced_tab(ui):
    guest = ui.guest_view
    computers = guest.view_stack.get_child_by_name("computers")
    advanced = guest.view_stack.get_child_by_name("advanced")
    for widget in (guest.recent_group, guest.address_row, guest.search_code_row, guest.history_row, guest.image_row, guest.audio_settings_row, guest.input_settings_row):
        assert widget.is_ancestor(advanced), widget
        assert not widget.is_ancestor(computers), widget
    assert guest.connect_card.is_ancestor(computers)
    assert guest.view_stack.get_page(advanced).get_title() == "Advanced options"


def test_connect_does_not_repeat_the_internet_page(ui):
    guest = ui.guest_view
    titles = []
    stack = [guest]
    while stack:
        widget = stack.pop()
        get_title = getattr(widget, "get_title", None)
        if callable(get_title) and isinstance(widget, Gtk.Widget):
            titles.append(get_title())
        child = widget.get_first_child()
        while child is not None:
            stack.append(child)
            child = child.get_next_sibling()
    assert "Play over the internet" not in titles
