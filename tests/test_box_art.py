"""Box art in Moonlight: real game covers and pictures that tell entries apart.

Reported on 2026-11-17 (phone screenshot): Desktop Low and the three screen
entries all showed Sunshine's generic "DESKTOP" picture, and games only their
names.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from big_remote_play.host import box_art

cairo = pytest.importorskip("cairo")


def _jpeg(path: Path, width: int = 60, height: int = 90) -> Path:
    import gi

    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import GdkPixbuf

    path.parent.mkdir(parents=True, exist_ok=True)
    pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, width, height)
    pixbuf.fill(0xAA3366FF)
    pixbuf.savev(str(path), "jpeg", [], [])
    return path


def _png_size(path: str) -> tuple[int, int]:
    surface = cairo.ImageSurface.create_from_png(path)
    return surface.get_width(), surface.get_height()


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    return tmp_path / "home"


def test_steam_covers_in_both_cache_layouts(home):
    old = _jpeg(home / ".local/share/Steam/appcache/librarycache/985890/library_600x900.jpg")
    new = _jpeg(home / ".local/share/Steam/appcache/librarycache/1967260/e9535ce9/library_capsule.jpg")
    assert box_art.cover_source("steam:985890", home) == old
    assert box_art.cover_source("steam:1967260", home) == new
    assert box_art.cover_source("steam:1", home) is None and box_art.cover_source("steam:../x", home) is None


def test_heroic_cover_is_the_cached_art_url(home):
    url = "https://cdn.example/art.png"
    root = home / ".config/heroic"
    (root / "store_cache").mkdir(parents=True)
    (root / "store_cache/legendary_library.json").write_text(json.dumps({"library": [{"app_name": "abc123", "art_square": url}]}))
    cached = _jpeg(root / "images-cache" / hashlib.sha256(url.encode()).hexdigest())
    assert box_art.cover_source("heroic:legendary:abc123", home) == cached


def test_a_cover_becomes_a_png_of_moonlights_tile_size_and_is_reused(home):
    _jpeg(home / ".local/share/Steam/appcache/librarycache/10/library_600x900.jpg")
    first = box_art.game_cover("steam:10", home)
    assert first.endswith(".png") and _png_size(first) == (box_art.WIDTH, box_art.HEIGHT)
    assert box_art.game_cover("steam:10", home) == first
    assert box_art.game_cover("steam:11", home) == ""


def test_the_distro_logo_comes_from_os_release(tmp_path):
    release = tmp_path / "os-release"
    release.write_text('NAME="Big WindowMaker"\nLOGO="big-wm"\n')
    icons = tmp_path / "icons"
    for size in ("32x32", "256x256"):
        (icons / size / "apps").mkdir(parents=True)
        (icons / size / "apps/big-wm.png").write_bytes(b"x")
    values = box_art.os_release(release)
    assert box_art.distro_name(values) == "Big WindowMaker"
    assert box_art.distro_logo(values, roots=[icons]) == icons / "256x256/apps/big-wm.png"
    assert box_art.distro_logo({"LOGO": "../evil"}, roots=[icons]) is None


def test_each_entry_gets_its_own_picture_and_a_persons_picture_is_kept(home):
    release = {"NAME": "Big WindowMaker"}
    low = {"name": "Desktop Low", "max-bitrate": 3000, "brp-entry": "desktop-low", "image-path": "desktop.png"}
    one = {"name": "01 - Monitor", "brp-entry": "monitor:DP-1", "output-name": "DP-1"}
    two = {"name": "02 - Monitor", "brp-entry": "monitor:DP-2", "output-name": "DP-2"}
    desktop = {"name": "Desktop", "cmd": ""}
    mine = {"name": "My Emulator", "cmd": "retroarch", "image-path": "/home/me/emu.png"}
    steam = {"name": "Steam Big Picture", "brp-entry": "steam-big-picture", "image-path": "steam.png"}
    apps = [low, one, two, desktop, mine, steam]
    assert box_art.decorate(apps, screens={"DP-1": "LG ULTRAWIDE", "DP-2": "LG HDR WFHD"}, low_label=lambda kbps: f"{kbps}", release=release)
    pictures = [app.get("image-path") for app in apps]
    assert len(set(pictures[:4])) == 4 and all(Path(p).is_file() for p in pictures[:4])
    assert mine["image-path"] == "/home/me/emu.png" and steam["image-path"] == "steam.png"
    # Same content, same file: nothing changes the next time.
    assert not box_art.decorate(apps, screens={"DP-1": "LG ULTRAWIDE", "DP-2": "LG HDR WFHD"}, low_label=lambda kbps: f"{kbps}", release=release)


def test_pictures_nobody_uses_are_removed(home):
    kept = box_art.entry_art("desktop", "Desktop")
    old = box_art.entry_art("desktop", "Old")
    assert box_art.forget_unused([kept]) == 1
    assert Path(kept).is_file() and not Path(old).exists()
