"""Box art Moonlight shows for each entry: real game covers and drawn entry art.

Reported on 2026-11-17 (phone screenshot): Desktop Low and the three screen
entries all showed Sunshine's generic "DESKTOP" picture, so nobody could tell
them apart, and games showed only their names.

- Games: the cover the launcher already keeps on this computer, converted to
  PNG (Sunshine accepts nothing else). Steam's library cache
  (``librarycache/<appid>/library_600x900.jpg`` or, since 2024,
  ``librarycache/<appid>/<hash>/library_capsule.jpg``), Heroic's image cache
  (``images-cache/<sha256 of the art URL>``) and Lutris' cover art. Nothing
  is downloaded.
- Ready-made entries: drawn here with the desktop's logo (``LOGO=`` in
  ``/etc/os-release``, e.g. Big WindowMaker), the entry's name, and what tells
  it apart: the screen's number, name and connector, or the bitrate ceiling.

Files live in ``~/.config/big-remote-play/sunshine/covers`` and are named after
their content, so a changed picture gets a new path: Sunshine derives the app
id from name and image path, and Moonlight then fetches the new picture
instead of keeping a cached one. Runs on worker threads (no GTK widgets).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
import hashlib
import json
import logging
import math
from pathlib import Path
import re

from big_remote_play import paths

_log = logging.getLogger("big-remoteplay")

WIDTH, HEIGHT = 600, 830  # Moonlight's tiles are about 0.72 wide per height
PREFIX = "brp-"
_ART_VERSION = "1"

# Big Remote Play's Gamer palette (docs/visual-design.md).
_WINDOW = (0x15 / 255, 0x1A / 255, 0x35 / 255)
_VIEW = (0x10 / 255, 0x14 / 255, 0x2B / 255)
_VIOLET = (0x83 / 255, 0x5D / 255, 0xFF / 255)
_CYAN = (0x22 / 255, 0xD3 / 255, 0xEE / 255)
_MAGENTA = (0xD9 / 255, 0x46 / 255, 0xEF / 255)
_TEXT = (0xEC / 255, 0xEE / 255, 0xFF / 255)
_SUBTLE = (0xB8 / 255, 0xBD / 255, 0xDA / 255)
_AMBER = (0xFB / 255, 0xBF / 255, 0x24 / 255)


def covers_dir() -> Path:
    return paths.SUNSHINE_CONFIG_DIR / "covers"


# ── the desktop's identity ────────────────────────────────────────────────


def os_release(path: Path = Path("/etc/os-release")) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return values
    for line in text.splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def distro_name(release: dict[str, str] | None = None) -> str:
    release = os_release() if release is None else release
    return release.get("NAME") or release.get("PRETTY_NAME") or "Linux"


def distro_logo(release: dict[str, str] | None = None, roots: Iterable[Path] = (Path("/usr/share/icons/hicolor"), Path("/usr/share/pixmaps"))) -> Path | None:
    """The largest picture of the logo os-release names (``LOGO=big-wm``)."""
    release = os_release() if release is None else release
    name = release.get("LOGO", "")
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", name):
        return None
    candidates: list[tuple[int, Path]] = []
    for root in roots:
        if not root.is_dir():
            continue
        for found in root.glob(f"**/{name}.*"):
            if found.suffix.lower() not in (".png", ".svg"):
                continue
            size = 4096 if found.suffix.lower() == ".svg" or "scalable" in found.parts else _size_from_path(found)
            candidates.append((size, found))
    return max(candidates, default=(0, None))[1]


def _size_from_path(path: Path) -> int:
    for part in path.parts:
        match = re.fullmatch(r"(\d+)x\d+(@\d+)?", part)
        if match:
            return int(match.group(1))
    return 64


# ── game covers already on this computer ──────────────────────────────────


def steam_cover(app_id: str, home: Path) -> Path | None:
    if not app_id.isdigit():
        return None
    for root in (home / ".local/share/Steam", home / ".steam/steam", home / ".var/app/com.valvesoftware.Steam/.local/share/Steam"):
        cache = root / "appcache/librarycache" / app_id
        if not cache.is_dir():
            continue
        for pattern in ("library_600x900.jpg", "*/library_capsule.jpg", "library_capsule.jpg", "*/library_600x900.jpg"):
            found = sorted(cache.glob(pattern))
            if found:
                return found[0]
    return None


def heroic_cover(app_name: str, home: Path) -> Path | None:
    for root in (home / ".config/heroic", home / ".var/app/com.heroicgameslauncher.hgl/config/heroic"):
        for library in ("store_cache/legendary_library.json", "store_cache/gog_library.json", "store_cache/nile_library.json", "sideload_apps/library.json"):
            try:
                data = json.loads((root / library).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            items = data.get("library") or data.get("games") if isinstance(data, dict) else data
            for item in items if isinstance(items, list) else []:
                if not isinstance(item, dict) or str(item.get("app_name") or item.get("appName") or "") != app_name:
                    continue
                for key in ("art_square", "art_cover"):
                    url = item.get(key)
                    if isinstance(url, str) and url:
                        cached = root / "images-cache" / hashlib.sha256(url.encode()).hexdigest()
                        if cached.is_file():
                            return cached
    return None


def lutris_cover(slug: str, home: Path) -> Path | None:
    for root in (home / ".local/share/lutris/coverart", home / ".cache/lutris/coverart"):
        for suffix in (".jpg", ".png"):
            candidate = root / f"{slug}{suffix}"
            if candidate.is_file():
                return candidate
    return None


def cover_source(game_key: str, home: Path | None = None) -> Path | None:
    """The launcher's own picture of a game (``steam:985890``, ``heroic:legendary:…``)."""
    home = home or Path.home()
    parts = game_key.split(":")
    if parts[0] == "steam" and len(parts) == 2:
        return steam_cover(parts[1], home)
    if parts[0] == "heroic" and len(parts) >= 2:
        return heroic_cover(parts[-1], home)
    if parts[0] == "lutris" and len(parts) == 2:
        return lutris_cover(parts[1], home)
    return None


# ── drawing ───────────────────────────────────────────────────────────────


def _target(kind: str, payload: object) -> Path:
    digest = hashlib.sha256(json.dumps([_ART_VERSION, kind, payload], sort_keys=True, default=str).encode()).hexdigest()[:12]
    slug = re.sub(r"[^a-z0-9]+", "-", kind.lower()).strip("-") or "art"
    return covers_dir() / f"{PREFIX}{slug}-{digest}.png"


def _save(surface, target: Path) -> Path:
    from big_remote_play.utils.secure_io import _secure_dir

    _secure_dir(str(target))
    temporary = target.with_suffix(".tmp")
    surface.write_to_png(str(temporary))
    temporary.replace(target)
    return target


def _pixbuf(path: Path, width: int, height: int):
    import gi

    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import GdkPixbuf  # type: ignore

    return GdkPixbuf.Pixbuf.new_from_file_at_scale(str(path), width, height, True)


def _paint_pixbuf(context, pixbuf, x: float, y: float) -> None:
    """Paint a pixbuf through PNG (Gdk.cairo_set_source_pixbuf is deprecated in GTK 4)."""
    import io

    import cairo

    ok, data = pixbuf.save_to_bufferv("png", [], [])
    if not ok:
        return
    surface = cairo.ImageSurface.create_from_png(io.BytesIO(data))
    context.set_source_surface(surface, x, y)
    context.paint()


def game_cover(game_key: str, home: Path | None = None) -> str:
    """A PNG of the game's cover, cropped to Moonlight's tile; ``""`` when none exists."""
    source = cover_source(game_key, home)
    if source is None:
        return ""
    try:
        stat = source.stat()
    except OSError:
        return ""
    target = _target("cover-" + game_key, [str(source), stat.st_mtime_ns, stat.st_size])
    if target.is_file():
        return str(target)
    try:
        import cairo

        import gi

        gi.require_version("GdkPixbuf", "2.0")
        from gi.repository import GdkPixbuf  # type: ignore

        original = GdkPixbuf.Pixbuf.new_from_file(str(source))
        scale = max(WIDTH / original.get_width(), HEIGHT / original.get_height())
        width, height = math.ceil(original.get_width() * scale), math.ceil(original.get_height() * scale)
        scaled = original.scale_simple(width, height, GdkPixbuf.InterpType.BILINEAR)
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, WIDTH, HEIGHT)
        context = cairo.Context(surface)
        context.set_source_rgb(*_VIEW)
        context.paint()
        _paint_pixbuf(context, scaled, (WIDTH - width) / 2, (HEIGHT - height) / 2)
        return str(_save(surface, target))
    except Exception as error:  # a cover is a nicety: the entry still works without one
        _log.info("No cover for %s: %s", game_key, error)
        return ""


def _layout(context, text: str, size: float, *, bold: bool = False, width: int = WIDTH - 80):
    import gi

    gi.require_version("Pango", "1.0")
    gi.require_version("PangoCairo", "1.0")
    from gi.repository import Pango, PangoCairo  # type: ignore

    layout = PangoCairo.create_layout(context)
    font = Pango.FontDescription.from_string(f"Sans {'Bold' if bold else ''} {size}")
    layout.set_font_description(font)
    layout.set_width(width * Pango.SCALE)
    layout.set_alignment(Pango.Alignment.CENTER)
    layout.set_wrap(Pango.WrapMode.WORD_CHAR)
    layout.set_ellipsize(Pango.EllipsizeMode.END)
    layout.set_height(-3)  # at most three lines
    layout.set_text(text, -1)
    return layout


def _text(context, text: str, y: float, size: float, color, *, bold: bool = False) -> float:
    import gi

    gi.require_version("PangoCairo", "1.0")
    from gi.repository import PangoCairo  # type: ignore

    layout = _layout(context, text, size, bold=bold)
    context.set_source_rgb(*color)
    context.move_to(40, y)
    PangoCairo.show_layout(context, layout)
    return y + layout.get_pixel_size()[1]


def _rounded(context, x: float, y: float, width: float, height: float, radius: float) -> None:
    context.new_sub_path()
    context.arc(x + width - radius, y + radius, radius, -math.pi / 2, 0)
    context.arc(x + width - radius, y + height - radius, radius, 0, math.pi / 2)
    context.arc(x + radius, y + height - radius, radius, math.pi / 2, math.pi)
    context.arc(x + radius, y + radius, radius, math.pi, 3 * math.pi / 2)
    context.close_path()


def _background(context) -> None:
    import cairo

    gradient = cairo.LinearGradient(0, 0, WIDTH, HEIGHT)
    gradient.add_color_stop_rgb(0, *_WINDOW)
    gradient.add_color_stop_rgb(1, *_VIEW)
    context.set_source(gradient)
    context.paint()
    glow = cairo.RadialGradient(WIDTH * 0.8, HEIGHT * 0.1, 10, WIDTH * 0.8, HEIGHT * 0.1, WIDTH * 0.9)
    glow.add_color_stop_rgba(0, *_VIOLET, 0.45)
    glow.add_color_stop_rgba(1, *_VIOLET, 0)
    context.set_source(glow)
    context.paint()
    accent = cairo.LinearGradient(0, 0, WIDTH, 0)
    accent.add_color_stop_rgb(0, *_VIOLET)
    accent.add_color_stop_rgb(0.5, *_MAGENTA)
    accent.add_color_stop_rgb(1, *_CYAN)
    context.set_source(accent)
    context.rectangle(0, HEIGHT - 12, WIDTH, 12)
    context.fill()


def _logo(context, logo: Path | None, size: int, x: float, y: float) -> bool:
    if logo is None:
        return False
    try:
        pixbuf = _pixbuf(logo, size, size)
    except Exception:
        return False
    _paint_pixbuf(context, pixbuf, x + (size - pixbuf.get_width()) / 2, y + (size - pixbuf.get_height()) / 2)
    return True


def _monitor(context, number: int, x: float, y: float, width: float, height: float) -> None:
    import cairo

    context.set_line_width(14)
    gradient = cairo.LinearGradient(x, y, x + width, y + height)
    gradient.add_color_stop_rgb(0, *_VIOLET)
    gradient.add_color_stop_rgb(1, *_CYAN)
    context.set_source(gradient)
    _rounded(context, x, y, width, height, 22)
    context.stroke()
    context.rectangle(x + width / 2 - 18, y + height + 7, 36, 34)
    context.fill()
    _rounded(context, x + width / 2 - 90, y + height + 38, 180, 16, 8)
    context.fill()
    import gi

    gi.require_version("PangoCairo", "1.0")
    from gi.repository import PangoCairo  # type: ignore

    layout = _layout(context, f"{number:02d}", 92, bold=True, width=int(width))
    tw, th = layout.get_pixel_size()
    context.set_source_rgb(*_TEXT)
    context.move_to(x, y + (height - th) / 2)
    PangoCairo.show_layout(context, layout)


def _badge(context, text: str, y: float) -> None:
    import gi

    gi.require_version("PangoCairo", "1.0")
    from gi.repository import PangoCairo  # type: ignore

    layout = _layout(context, text, 24, bold=True, width=WIDTH - 120)
    tw, th = layout.get_pixel_size()
    width = min(WIDTH - 80, tw + 56)
    x = (WIDTH - width) / 2
    _rounded(context, x, y, width, th + 26, (th + 26) / 2)
    context.set_source_rgb(*_AMBER)
    context.fill()
    context.set_source_rgb(*_VIEW)
    context.move_to(60, y + 13)
    PangoCairo.show_layout(context, layout)


def entry_art(kind: str, title: str, *, subtitle: str = "", badge: str = "", number: int = 0, logo: Path | None = None, small_print: str = "") -> str:
    """Draw one ready-made entry's picture; ``""`` if drawing is not possible here."""
    payload = [title, subtitle, badge, number, str(logo or ""), small_print]
    target = _target(kind, payload)
    if target.is_file():
        return str(target)
    try:
        import cairo

        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, WIDTH, HEIGHT)
        context = cairo.Context(surface)
        _background(context)
        if number:
            _logo(context, logo, 72, WIDTH - 108, 36)
            _monitor(context, number, 90, 150, WIDTH - 180, 260)
            y = 520.0
        else:
            if not _logo(context, logo, 280, (WIDTH - 280) / 2, 120):
                _monitor(context, 0, 120, 160, WIDTH - 240, 220)
            y = 470.0
        y = _text(context, title, y, 46, _TEXT, bold=True) + 14
        if subtitle:
            y = _text(context, subtitle, y, 28, _SUBTLE) + 10
        if small_print:
            _text(context, small_print, y, 22, _SUBTLE)
        if badge:
            _badge(context, badge, HEIGHT - 120)
        return str(_save(surface, target))
    except Exception as error:
        _log.info("Entry art not drawn: %s", error)
        return ""


def forget_unused(keep: Iterable[str]) -> int:
    """Remove pictures drawn here that no entry uses any more."""
    kept = {Path(path).name for path in keep if path}
    removed = 0
    directory = covers_dir()
    if not directory.is_dir():
        return 0
    for picture in directory.glob(f"{PREFIX}*.png"):
        if picture.name not in kept:
            try:
                picture.unlink()
                removed += 1
            except OSError:
                pass
    return removed


ArtMaker = Callable[..., str]


# ── which picture each entry gets ─────────────────────────────────────────


def _ours(image_path: object) -> bool:
    """Empty, Sunshine's own generic pictures, or one drawn here: free to replace."""
    if not isinstance(image_path, str) or not image_path:
        return True
    if image_path in ("desktop.png", "box.png", "./assets/desktop.png"):
        return True
    return Path(image_path).parent == covers_dir() and Path(image_path).name.startswith(PREFIX)


def picture_for(app: dict, *, screens: dict[str, str] | None = None, low_label: Callable[[int], str] | None = None, release: dict[str, str] | None = None) -> str:
    """The picture this app should show, or ``""`` to leave it as it is.

    Only apps without a picture of their own are given one: a picture the
    person chose is never replaced.
    """
    from big_remote_play.host.moonlight_entries import DESKTOP_LOW, DESKTOP_NAME, MARKER, MONITOR_PREFIX
    from big_remote_play.utils.game_detector import app_game_key

    if not _ours(app.get("image-path")):
        return ""
    release = os_release() if release is None else release
    logo, name = distro_logo(release), distro_name(release)
    marker = str(app.get(MARKER) or "")
    if marker == DESKTOP_LOW:
        rate = int(app.get("max-bitrate") or 0)
        return entry_art("desktop-low", str(app.get("name")), subtitle=name, badge=low_label(rate) if low_label else "", logo=logo)
    if marker.startswith(MONITOR_PREFIX):
        connector = marker[len(MONITOR_PREFIX) :]
        match = re.match(r"(\d+)", str(app.get("name", "")))
        number = int(match.group(1)) if match else 1
        label = (screens or {}).get(connector) or connector
        return entry_art("monitor-" + connector, label, subtitle=connector, number=number, logo=logo, small_print=name)
    if marker:
        return ""  # Steam Big Picture keeps Sunshine's Steam picture
    if app.get("name") == DESKTOP_NAME and not app.get("cmd") and not app.get("detached"):
        return entry_art("desktop", DESKTOP_NAME, subtitle=name, logo=logo)
    key = app_game_key(app)
    return game_cover(key) if key else ""


def decorate(apps: list, **options) -> bool:
    """Give every app without its own picture the right one, in place; True when one changed."""
    changed = False
    for app in apps:
        if not isinstance(app, dict):
            continue
        picture = picture_for(app, **options)
        if picture and app.get("image-path") != picture:
            app["image-path"] = picture
            changed = True
    forget_unused(str(app.get("image-path", "")) for app in apps if isinstance(app, dict))
    return changed


__all__ = ["HEIGHT", "WIDTH", "cover_source", "covers_dir", "decorate", "distro_logo", "distro_name", "entry_art", "forget_unused", "game_cover", "os_release", "picture_for"]
