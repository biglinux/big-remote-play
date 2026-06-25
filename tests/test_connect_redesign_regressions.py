"""Static source guards for the Connect page + Rede Privada novice redesign."""

from pathlib import Path

GUEST = Path("src/big_remote_play/ui/guest_view.py")
MAIN = Path("src/big_remote_play/ui/main_window.py")
PNV = Path("src/big_remote_play/ui/private_network_view.py")


def test_host_scroll_has_breathing_room_and_no_tall_min() -> None:
    src = GUEST.read_text()
    # The host list scroller must not force a tall empty box, and the list must
    # have vertical margins so the boxed-list card corners are not clipped.
    assert "host_scroll.set_min_content_height(120)" in src
    assert "self.hosts_list.set_margin_top(6)" in src
    assert "self.hosts_list.set_margin_bottom(6)" in src
