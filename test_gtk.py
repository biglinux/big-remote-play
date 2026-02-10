import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk
try:
    print(f"Gtk.INVALID_LIST_POSITION: {Gtk.INVALID_LIST_POSITION}")
except AttributeError:
    print("Gtk.INVALID_LIST_POSITION not found!")
