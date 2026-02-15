import os
from gi.repository import Gtk, Gio

# Base directory for icons: src/icons
# src/utils/icons.py -> src/icons
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ICONS_DIR = os.path.join(BASE_DIR, "icons")

def get_icon_file_path(icon_name):
    """Returns absolute path to icon file if it exists in icons dir."""
    # Check known extensions
    for ext in [".svg", ".png"]:
        path = os.path.join(ICONS_DIR, f"{icon_name}{ext}")
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

def set_icon(image_widget, icon_name):
    """Sets the content of an existing Gtk.Image to a local icon."""
    gicon = get_gicon(icon_name)
    if gicon:
        image_widget.set_from_gicon(gicon)
    else:
        image_widget.set_from_icon_name(icon_name)
