"""
Application preferences window
"""

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Gtk, Adw, GLib, Gio
import subprocess, os, tempfile, threading, shutil, sys
from pathlib import Path

from utils.icons import create_icon_widget
import utils.logger as logger
from utils.i18n import _

class PreferencesWindow(Adw.PreferencesWindow):
    """Preferences window"""
    
    def __init__(self, **kwargs):
        self.config = kwargs.pop('config', None)
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
        restore_row.set_title(_('Restaurar Padrões'))
        restore_row.set_subtitle(_('Redefinir todas as configurações para o padrão'))
        
        restore_btn = Gtk.Button(label=_('Restaurar'))
        restore_btn.set_valign(Gtk.Align.CENTER)
        restore_btn.connect('clicked', self.on_restore_defaults_clicked)
        restore_row.add_suffix(restore_btn)
        restore_group.add(restore_row)
        
        # Clear All
        clear_all_row = Adw.ActionRow()
        clear_all_row.set_title(_('Limpar Tudo'))
        clear_all_row.set_subtitle(_('Remover logs, configurações, servidores e dados salvos'))
        
        clear_all_btn = Gtk.Button(label=_('Limpar Tudo'))
        clear_all_btn.add_css_class('destructive-action')
        clear_all_btn.set_valign(Gtk.Align.CENTER)
        clear_all_btn.connect('clicked', self.on_clear_all_clicked)
        clear_all_row.add_suffix(clear_all_btn)
        restore_group.add(clear_all_row)
        
        general_page.add(restore_group)
        

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
        
        # Maintenance group
        maintenance_group = Adw.PreferencesGroup()
        maintenance_group.set_title(_('Manutenção do Sistema'))
        maintenance_group.set_description(_('Ferramentas para resolver problemas comuns'))
        
        fix_libs_row = Adw.ActionRow()
        fix_libs_row.set_title(_('Corrigir Bibliotecas (ICU)'))
        fix_libs_row.set_subtitle(_('Resolve o erro libicuuc.so.7* ausente'))
        
        fix_btn = Gtk.Button(label=_('Fix Automático'))
        fix_btn.add_css_class('suggested-action')
        fix_btn.set_valign(Gtk.Align.CENTER)
        fix_libs_row.add_suffix(fix_btn)
        
        maintenance_group.add(fix_libs_row)
        
        # Connect signals
        verbose_row.set_active(self.config.get('verbose_logging', False))
        verbose_row.connect('notify::active', self.on_verbose_toggled)
        clear_btn.connect('clicked', self.on_clear_logs_clicked)
        fix_btn.connect('clicked', self.on_fix_libs_clicked)
        
        advanced_page.add(paths_group)
        advanced_page.add(logs_group)
        advanced_page.add(maintenance_group)
        
        # Add pages
        self.add(general_page)
        
        # Sunshine page
        from .sunshine_preferences import SunshinePreferencesPage
        self.sunshine_page = SunshinePreferencesPage(main_config=self.config)
        self.add(self.sunshine_page)
        
        # Moonlight page
        from .moonlight_preferences import MoonlightPreferencesPage
        self.moonlight_page = MoonlightPreferencesPage()
        self.add(self.moonlight_page)
        

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

    def on_fix_libs_clicked(self, button):
        from pathlib import Path
        script_path = Path(__file__).parent.parent / 'scripts' / 'fix_sunshine_libs.sh'
        if not script_path.exists():
            script_path = Path("/usr/share/big-remote-play/scripts/fix_sunshine_libs.sh")
            
        if script_path.exists():
            try:
                subprocess.Popen(['pkexec', str(script_path)])
                diag = Adw.MessageDialog(heading=_("Fix Automático Iniciado"), body=_("O script de correção de bibliotecas foi iniciado. Forneça a senha se solicitado."))
                diag.add_response("ok", _("OK"))
                diag.set_transient_for(self)
                diag.present()
            except Exception as e:
                diag = Adw.MessageDialog(heading=_("Erro"), body=str(e))
                diag.add_response("ok", _("OK"))
                diag.set_transient_for(self)
                diag.present()
        else:
            diag = Adw.MessageDialog(heading=_("Erro"), body=_("Script de correção não encontrado."))
            diag.add_response("ok", _("OK"))
            diag.set_transient_for(self)
            diag.present()

    def copy_config_path(self, btn):
        path = os.path.expanduser('~/.config/big-remoteplay')
        self.get_clipboard().set(path)
        self.add_toast(Adw.Toast.new(_("Caminho copiado!")))
        

    def on_restore_defaults_clicked(self, button):
        dialog = Adw.MessageDialog(heading=_("Restaurar Padrões?"), body=_("Isso redefinirá todas as configurações do aplicativo para os valores originais. Dados de pareamento não serão perdidos."))
        dialog.add_response("cancel", _("Cancelar"))
        dialog.add_response("restore", _("Restaurar"))
        dialog.set_response_appearance("restore", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_transient_for(self)
        
        def on_response(d, r):
            if r == "restore":
                # Reset main config
                default_conf = self.config.default_config()
                for k, v in default_conf.items():
                    self.config.set(k, v)
                
                # Reset Moonlight Config (delete file to force regeneration)
                try:
                    # Reset Sunshine Config (Clear file)
                    if hasattr(self, 'sunshine_page') and hasattr(self.sunshine_page, 'config'):
                        self.sunshine_page.config.config = {}
                        self.sunshine_page.config.save()
                        print("Sunshine config reset")

                    from utils.moonlight_config import MoonlightConfigManager
                    mc = MoonlightConfigManager()
                    if mc.config_file and mc.config_file.exists():
                        os.remove(mc.config_file)
                    mc.reload() # Recreates Default
                    mc.save()
                except Exception as e:
                    print(f"Error resetting configs: {e}")
                
                self.add_toast(Adw.Toast.new(_("Configurações Restauradas!")))
                self.close()
        
        dialog.connect("response", on_response)
        dialog.present()

    def on_clear_all_clicked(self, button):
        # Double check implementation
        dialog1 = Adw.MessageDialog(heading=_("Limpar TUDO?"), body=_("ATENÇÃO: Isso apagará TODOS os dados, logs, configurações e servidores salvos. O aplicativo será fechado."))
        dialog1.add_response("cancel", _("Cancelar"))
        dialog1.add_response("clear", _("Apagar Tudo"))
        dialog1.set_response_appearance("clear", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog1.set_transient_for(self)
        
        def on_response1(d, r):
            if r == "clear":
                # Second confirmation (Double Check)
                dialog2 = Adw.MessageDialog(heading=_("Tem Certeza Absoluta?"), body=_("Esta ação é IRREVERSÍVEL. Você perderá todos os dados configurados."))
                dialog2.add_response("cancel", _("Cancelar"))
                dialog2.add_response("destroy", _("Sim, Apagar Tudo"))
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
            err_dlg = Adw.MessageDialog(heading=_("Erro ao Limpar"), body=str(e))
            err_dlg.add_response("ok", _("OK"))
            err_dlg.set_transient_for(self)
            err_dlg.present()
