"""
Application preferences window
"""

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Gtk, Adw, GLib, Gio
import subprocess, os, tempfile, threading

from utils.icons import create_icon_widget
import utils.logger as logger
from utils.i18n import _

class PreferencesWindow(Adw.PreferencesWindow):
    """Preferences window"""
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        
        self.set_title(_('Preferências'))
        self.set_default_size(600, 500)
        self.set_modal(True)
        
        self.logger = logger.Logger()
        self.config = kwargs.get('config', None)
        if not self.config:
            from utils.config import Config
            self.config = Config()
            
        self.setup_ui()
        
    def setup_ui(self):
        """Configures interface"""
        # General page
        general_page = Adw.PreferencesPage()
        general_page.set_title(_('Geral'))
        general_page.set_icon_name('preferences-system-symbolic')
        
        # Appearance group
        appearance_group = Adw.PreferencesGroup()
        appearance_group.set_title(_('Aparência'))
        
        # Theme
        theme_row = Adw.ComboRow()
        theme_row.set_title(_('Tema'))
        theme_row.set_subtitle(_('Escolha o esquema de cores'))
        
        theme_model = Gtk.StringList()
        theme_model.append(_('Automático'))
        theme_model.append(_('Claro'))
        theme_model.append(_('Escuro'))
        
        theme_row.set_model(theme_model)
        theme_row.set_selected(0)
        
        appearance_group.add(theme_row)
        general_page.add(appearance_group)
        

        # Advanced page
        advanced_page = Adw.PreferencesPage()
        advanced_page.set_title(_('Avançado'))
        advanced_page.set_icon_name('preferences-other-symbolic')
        
        # Paths group
        paths_group = Adw.PreferencesGroup()
        paths_group.set_title(_('Caminhos'))
        
        config_row = Adw.ActionRow()
        config_row.set_title(_('Diretório de Configuração'))
        config_row.set_subtitle('~/.config/big-remoteplay')
        
        copy_config_btn = Gtk.Button()
        copy_config_btn.set_child(create_icon_widget('edit-copy-symbolic'))
        copy_config_btn.add_css_class('flat')
        copy_config_btn.set_valign(Gtk.Align.CENTER)
        copy_config_btn.set_tooltip_text(_("Copiar Caminho"))
        copy_config_btn.connect('clicked', self.copy_config_path)
        config_row.add_suffix(copy_config_btn)
        config_row.set_activatable_widget(copy_config_btn)
        
        paths_group.add(config_row)
        
        # Logs group
        logs_group = Adw.PreferencesGroup()
        logs_group.set_title(_('Logs e Depuração'))
        
        verbose_row = Adw.SwitchRow()
        verbose_row.set_title(_('Logs Detalhados'))
        verbose_row.set_subtitle(_('Ativar logging verbose para depuração'))
        verbose_row.set_active(False)
        logs_group.add(verbose_row)
        
        clear_logs_row = Adw.ActionRow()
        clear_logs_row.set_title(_('Limpar Logs'))
        clear_logs_row.set_subtitle(_('Remover arquivos de log antigos'))
        
        clear_btn = Gtk.Button(label=_('Limpar'))
        clear_btn.add_css_class('destructive-action')
        clear_btn.set_valign(Gtk.Align.CENTER)
        clear_logs_row.add_suffix(clear_btn)
        
        logs_group.add(clear_logs_row)
        
        # Connect signals
        verbose_row.set_active(self.config.get('verbose_logging', False))
        verbose_row.connect('notify::active', self.on_verbose_toggled)
        clear_btn.connect('clicked', self.on_clear_logs_clicked)
        
        advanced_page.add(paths_group)
        advanced_page.add(logs_group)
        
        # Add pages
        self.add(general_page)
        
        # Sunshine page
        from .sunshine_preferences import SunshinePreferencesPage
        self.sunshine_page = SunshinePreferencesPage()
        self.add(self.sunshine_page)
        
        # Moonlight page
        from .moonlight_preferences import MoonlightPreferencesPage
        self.moonlight_page = MoonlightPreferencesPage()
        self.add(self.moonlight_page)
        

        self.add(advanced_page)

    def on_verbose_toggled(self, row, param):
        enabled = row.get_active()
        self.config.set('verbose_logging', enabled)
        self.logger = logger.Logger(force_new=True)
        self.logger.set_verbose(enabled)
        self.logger.info(f"Verbose logging {'enabled' if enabled else 'disabled'}")
        
    def on_clear_logs_clicked(self, button):
        self.logger.clear_old_logs()
        diag = Adw.MessageDialog(heading=_("Logs Limpos"), body=_("Arquivos de log antigos foram removidos."))
        diag.add_response("ok", _("OK"))
        diag.set_transient_for(self)
        diag.present()

    def copy_config_path(self, _):
        path = os.path.expanduser('~/.config/big-remoteplay')
        self.get_clipboard().set(path)
        self.add_toast(Adw.Toast.new(_("Caminho copiado!")))
        

