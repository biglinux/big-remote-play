import os
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gtk, Gio, Gdk, GdkPixbuf  # type: ignore

from big_remote_play import paths

# Oversample full-color SVG logos so the SVG is rasterized at (at least) HiDPI
# device resolution instead of GTK upscaling a small icon-pipeline bitmap.
_LOGO_OVERSAMPLE = 2

ICONS_DIR = str(paths.ICONS_DIR)
IMG_DIR = str(paths.IMG_DIR)


def get_icon_file_path(icon_name):
    """Returns absolute path to icon file if it exists in icons or img dir."""
    # Check icons (symbolic) first, then img (non-symbolic)
    for folder in [ICONS_DIR, IMG_DIR]:
        for ext in [".svg", ".png", ".jpg"]:
            path = os.path.join(folder, f"{icon_name}{ext}")
            if os.path.exists(path):
                return path
    return None


def get_gicon(icon_name):
    """Returns a Gio.FileIcon for a local non-symbolic asset, if available."""
    path = get_icon_file_path(icon_name)
    if path:
        gfile = Gio.File.new_for_path(path)
        return Gio.FileIcon.new(gfile)
    return None


def _ensure_icon_search_paths() -> Gtk.IconTheme | None:
    """Put bundled icons before system fallbacks in the active icon theme.

    Symbolic SVGs must be resolved by ``Gtk.IconTheme`` rather than loaded as a
    generic file texture. This is what lets GTK apply the current foreground,
    selected, dark-theme and high-contrast palettes.
    """
    display = Gdk.Display.get_default()
    if display is None:
        return None
    theme = Gtk.IconTheme.get_for_display(display)
    current = list(theme.get_search_path())
    bundled = [path for path in (ICONS_DIR, IMG_DIR) if os.path.isdir(path)]
    desired = bundled + [path for path in current if path not in bundled]
    if desired != current:
        theme.set_search_path(desired)
    return theme


def create_icon_widget(icon_name, size=None, css_class=None):
    """Create a theme-aware image.

    ``*-symbolic`` assets always go through ``Gtk.IconTheme`` so GTK can
    replace the canonical symbolic foreground marker for light, dark, selected
    and high-contrast states. Full-colour images keep the explicit local-file
    path.
    """
    if icon_name.endswith("-symbolic"):
        _ensure_icon_search_paths()
        img = Gtk.Image.new_from_icon_name(icon_name)
    else:
        gicon = get_gicon(icon_name)
        img = Gtk.Image.new_from_gicon(gicon) if gicon else Gtk.Image.new_from_icon_name(icon_name)

    if size:
        img.set_pixel_size(size)

    # Icons created here are visual reinforcement. The surrounding button,
    # row or label owns the accessible name, so exposing the image separately
    # would make screen readers announce the same control twice.
    img.set_accessible_role(Gtk.AccessibleRole.PRESENTATION)

    if css_class:
        if isinstance(css_class, list):
            for c in css_class:
                img.add_css_class(c)
        else:
            img.add_css_class(css_class)

    return img


def create_logo_widget(icon_name, size, css_class=None):
    """Crisp full-color logo from an SVG/PNG, rasterized at the target size.

    Use for app logos (NOT symbolic icons — those are recolored via CSS and must
    stay on the gicon path). GdkPixbuf rasterizes the SVG via librsvg at exactly
    the requested pixel size, so the result is sharp instead of an upscaled
    low-res bitmap. Oversampled so it stays crisp on HiDPI (scale 2) displays.
    """
    img = Gtk.Image()
    path = get_icon_file_path(icon_name)
    if path:
        try:
            target = max(1, int(size)) * _LOGO_OVERSAMPLE
            pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_size(path, target, target)
            if pixbuf is not None:
                img.set_from_paintable(Gdk.Texture.new_for_pixbuf(pixbuf))
            else:
                gicon = get_gicon(icon_name)
                if gicon:
                    img.set_from_gicon(gicon)
        except Exception:
            gicon = get_gicon(icon_name)
            if gicon:
                img.set_from_gicon(gicon)
    else:
        img.set_from_icon_name(icon_name)

    img.set_pixel_size(size)
    img.set_accessible_role(Gtk.AccessibleRole.PRESENTATION)
    if css_class:
        for c in [css_class] if isinstance(css_class, str) else css_class:
            img.add_css_class(c)
    return img


def set_icon(image_widget, icon_name):
    """Set an existing image while preserving symbolic recolouring."""
    if icon_name.endswith("-symbolic"):
        _ensure_icon_search_paths()
        image_widget.set_from_icon_name(icon_name)
        return
    gicon = get_gicon(icon_name)
    if gicon:
        image_widget.set_from_gicon(gicon)
    else:
        image_widget.set_from_icon_name(icon_name)
