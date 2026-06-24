import os
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('GdkPixbuf', '2.0')
from gi.repository import Gtk, Gio, Gdk, GdkPixbuf

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
    """Returns a Gio.FileIcon for the local icon, or None if not found."""
    path = get_icon_file_path(icon_name)
    if path:
        gfile = Gio.File.new_for_path(path)
        return Gio.FileIcon.new(gfile)
    return None

def create_icon_widget(icon_name, size=None, css_class=None):
    """
    Creates a Gtk.Image using the local icon file.
    Falls back to theme icon_name if local file not found.
    """
    gicon = get_gicon(icon_name)
    
    if gicon:
        img = Gtk.Image.new_from_gicon(gicon)
    else:
        # Fallback to system theme if local not found (though user wants only local, 
        # this prevents empty space if something is missing)
        img = Gtk.Image.new_from_icon_name(icon_name)
        
    if size:
        img.set_pixel_size(size)
    
    if css_class:
        if isinstance(css_class, list):
            for c in css_class: img.add_css_class(c)
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
            img.set_from_paintable(Gdk.Texture.new_for_pixbuf(pixbuf))
        except Exception:
            gicon = get_gicon(icon_name)
            if gicon:
                img.set_from_gicon(gicon)
    else:
        img.set_from_icon_name(icon_name)

    img.set_pixel_size(size)
    if css_class:
        for c in ([css_class] if isinstance(css_class, str) else css_class):
            img.add_css_class(c)
    return img


def set_icon(image_widget, icon_name):
    """Sets the content of an existing Gtk.Image to a local icon."""
    gicon = get_gicon(icon_name)
    if gicon:
        image_widget.set_from_gicon(gicon)
    else:
        image_widget.set_from_icon_name(icon_name)
