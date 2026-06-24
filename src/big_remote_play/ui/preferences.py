"""
Application preferences window
"""

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Gtk, Adw
import os, shutil, sys
from pathlib import Path

from big_remote_play.utils.icons import create_icon_widget
import big_remote_play.utils.logger as logger
from big_remote_play.utils.i18n import _

class PreferencesWindow(Adw.PreferencesWindow):
    """Preferences window"""
    
    def __init__(self, **kwargs):
        self.config = kwargs.pop('config', None)
        self.initial_tab = kwargs.pop('initial_tab', None)
        super().__init__(**kwargs)
        
        self.set_title(_('Preferences'))
        self.set_default_size(600, 500)
        self.set_modal(True)
        
        self.logger = logger.Logger()
        if not self.config:
            from big_remote_play.utils.config import Config
            self.config = Config()
            
        self.setup_ui()
        
        if self.initial_tab == 'sunshine' and hasattr(self, 'sunshine_page'):
             self.set_visible_page(self.sunshine_page)
        
    def setup_ui(self):
        """Configures interface"""
        # General page
        general_page = Adw.PreferencesPage()
        general_page.set_title(_('Geral'))
        general_page.set_icon_name('preferences-system-symbolic')
        
        # Appearance group
        appearance_group = Adw.PreferencesGroup()
        appearance_group.set_title(_('Appearance'))
        
        # Theme
        theme_row = Adw.ComboRow()
        theme_row.set_title(_('Tema'))
        theme_row.set_subtitle(_('Escolha o esquema de cores'))
        
        theme_model = Gtk.StringList()
        theme_model.append(_('Automatic'))
        theme_model.append(_('Claro'))
        theme_model.append(_('Escuro'))
        
        theme_row.set_model(theme_model)
        theme_row.set_model(theme_model)
        
        # Load current theme
        current_theme = self.config.get('theme', 'auto')
        idx = 0
        if current_theme == 'light': idx = 1
        elif current_theme == 'dark': idx = 2
        theme_row.set_selected(idx)
        
        theme_row.connect('notify::selected', self.on_theme_changed)
        
        appearance_group.add(theme_row)
        general_page.add(appearance_group)
        
        # Restore group
        restore_group = Adw.PreferencesGroup()
        restore_group.set_title(_('Restaurar'))
        
        # Restore Defaults
        restore_row = Adw.ActionRow()
        restore_row.set_title(_('Restore Defaults'))
        restore_row.set_subtitle(_('Reset all settings to default'))
        
        restore_btn = Gtk.Button(label=_('Restaurar'))
        restore_btn.set_valign(Gtk.Align.CENTER)
        restore_btn.connect('clicked', self.on_restore_defaults_clicked)
        restore_row.add_suffix(restore_btn)
        restore_group.add(restore_row)
        
        # Clear All
        clear_all_row = Adw.ActionRow()
        clear_all_row.set_title(_('Limpar Tudo'))
        clear_all_row.set_subtitle(_('Remove logs, settings, servers and saved data'))
        
        clear_all_btn = Gtk.Button(label=_('Limpar Tudo'))
        clear_all_btn.add_css_class('destructive-action')
        clear_all_btn.set_valign(Gtk.Align.CENTER)
        clear_all_btn.connect('clicked', self.on_clear_all_clicked)
        clear_all_row.add_suffix(clear_all_btn)
        restore_group.add(clear_all_row)
        
        general_page.add(restore_group)
        

        # Advanced page
        advanced_page = Adw.PreferencesPage()
        advanced_page.set_title(_('Advanced'))
        advanced_page.set_icon_name('preferences-other-symbolic')
        
        # Paths group
        paths_group = Adw.PreferencesGroup()
        paths_group.set_title(_('Paths'))
        
        config_row = Adw.ActionRow()
        config_row.set_title(_('Configuration Directory'))
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
        logs_group.set_title(_('Logs and Debugging'))
        
        verbose_row = Adw.SwitchRow()
        verbose_row.set_title(_('Logs Detalhados'))
        verbose_row.set_subtitle(_('Enable verbose logging for debugging'))
        verbose_row.set_active(False)
        logs_group.add(verbose_row)
        
        clear_logs_row = Adw.ActionRow()
        clear_logs_row.set_title(_('Limpar Logs'))
        clear_logs_row.set_subtitle(_('Remove old log files'))
        
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
        

        
        # Add pages. Sunshine/Moonlight tuning is NOT duplicated here — it lives
        # with its task (Server → Advanced server settings; Connect → Advanced
        # client settings), so there is one place per task.
        self.add(general_page)
        self.add(advanced_page)

    def on_theme_changed(self, row, param):
        idx = row.get_selected()
        theme = 'auto'
        if idx == 1: theme = 'light'
        elif idx == 2: theme = 'dark'
        
        self.config.set('theme', theme)
        
        sm = Adw.StyleManager.get_default()
        if theme == 'dark': sm.set_color_scheme(Adw.ColorScheme.FORCE_DARK)
        elif theme == 'light': sm.set_color_scheme(Adw.ColorScheme.FORCE_LIGHT)
        else: sm.set_color_scheme(Adw.ColorScheme.DEFAULT)

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



    def copy_config_path(self, btn):
        path = os.path.expanduser('~/.config/big-remoteplay')
        self.get_clipboard().set(path)
        self.add_toast(Adw.Toast.new(_("Path copied!")))
        

    def on_restore_defaults_clicked(self, button):
        # Updated text as requested
        msg = _("All your changes in Big Remote Play will be restored! This includes Sunshine and Moonlight settings.")
        
        dialog = Adw.MessageDialog(heading=_("Restore Defaults?"), body=msg)
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("restore", _("Restore"))
        dialog.set_response_appearance("restore", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_transient_for(self)
        
        def on_response(d, r):
            if r == "restore":
                try:
                    # 1. Reset Main Config (config.json)
                    # We load defaults and apply them. 
                    # Ideally we should clear unknown keys too, but setting defaults covers most.
                    default_conf = self.config.default_config()
                    for k, v in default_conf.items():
                        self.config.set(k, v)
                    self.config.save()
                    
                    # 2. Reset Sunshine Config (sunshine.conf)
                    # Delete the file so it regenerates cleanly or starts empty
                    sunshine_conf = Path.home() / '.config' / 'big-remoteplay' / 'sunshine' / 'sunshine.conf'
                    if sunshine_conf.exists():
                        os.remove(sunshine_conf)
                        print("Sunshine config deleted (reset)")
                        
                    # Reload UI config manager if active
                    if hasattr(self, 'sunshine_page') and hasattr(self.sunshine_page, 'config'):
                         self.sunshine_page.config.load()
                    
                    # 3. Reset Moonlight Config
                    from big_remote_play.utils.moonlight_config import MoonlightConfigManager
                    mc = MoonlightConfigManager()
                    if mc.config_file and mc.config_file.exists():
                        os.remove(mc.config_file)
                    mc.reload() # Recreates Default
                    mc.save()
                    
                    self.add_toast(Adw.Toast.new(_("Settings restored! Restart the application to apply all changes.")))
                    self.close()
                except Exception as e:
                    print(f"Error resetting configs: {e}")
                    self.add_toast(Adw.Toast.new(_("Error restoring: {}").format(e)))
        
        dialog.connect("response", on_response)
        dialog.present()

    def on_clear_all_clicked(self, button):
        # Double check implementation
        dialog1 = Adw.MessageDialog(heading=_("Clear EVERYTHING?"), body=_("WARNING: This will delete ALL data, logs, settings and saved servers. The application will close."))
        dialog1.add_response("cancel", _("Cancel"))
        dialog1.add_response("clear", _("Delete All"))
        dialog1.set_response_appearance("clear", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog1.set_transient_for(self)
        
        def on_response1(d, r):
            if r == "clear":
                # Second confirmation (Double Check)
                dialog2 = Adw.MessageDialog(heading=_("Are You Absolutely Sure?"), body=_("This action is IRREVERSIBLE. You will lose all configured data."))
                dialog2.add_response("cancel", _("Cancel"))
                dialog2.add_response("destroy", _("Yes, Delete All"))
                dialog2.set_response_appearance("destroy", Adw.ResponseAppearance.DESTRUCTIVE)
                dialog2.set_transient_for(self)
                
                def on_response2(d2, r2):
                    if r2 == "destroy":
                        self._perform_clear_all()
                
                dialog2.connect("response", on_response2)
                dialog2.present()
        
        dialog1.connect("response", on_response1)
        dialog1.present()

    def _perform_clear_all(self):
        try:
            # 1. Config Dir (~/.config/big-remoteplay)
            config_dir = Path.home() / '.config' / 'big-remoteplay'
            if config_dir.exists():
                shutil.rmtree(config_dir)
            
            # 2. Cache/Logs (~/.cache/big-remoteplay)
            cache_dir = Path.home() / '.cache' / 'big-remoteplay'
            if cache_dir.exists():
                shutil.rmtree(cache_dir)
            
            # 3. Moonlight Config (~/.config/Moonlight Game Streaming Project)
            moon_dir = Path.home() / '.config' / 'Moonlight Game Streaming Project'
            if moon_dir.exists():
                 shutil.rmtree(moon_dir)
            
            # 4. Moonlight Flatpak/Var Config (if any)
            # Not deleting global flatpak data to be safe, but can check specific paths if needed.
            
            print("All data cleared.")
            # Quit app
            app = self.get_application()
            if app:
                app.quit()
            else:
                sys.exit(0)
                
        except Exception as e:
            err_dlg = Adw.MessageDialog(heading=_("Error Clearing"), body=str(e))
            err_dlg.add_response("ok", _("OK"))
            err_dlg.set_transient_for(self)
            err_dlg.present()
