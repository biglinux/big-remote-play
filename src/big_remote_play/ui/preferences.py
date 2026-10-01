"""Backup, restore and reset of everything this application stores."""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gtk, Adw, GLib  # type: ignore
import logging

_log = logging.getLogger("big-remoteplay")

import os, shutil, threading
from datetime import datetime
from pathlib import Path

from big_remote_play import __version__
import big_remote_play.utils.logger as logger
from big_remote_play.utils.backup_restore import BackupManager, BackupValidationError
from big_remote_play.utils.i18n import _
from big_remote_play import paths
from .components import action_row


class PreferencesWindow(Adw.Window):
    """Preferences window"""

    def __init__(self, **kwargs):
        self.config = kwargs.pop("config", None)
        self.initial_tab = kwargs.pop("initial_tab", None)
        super().__init__(**kwargs)

        self.set_title(_("Backup and restore"))
        self.set_default_size(640, 520)
        self.add_css_class("brp-dialog")
        self.set_modal(True)

        self.logger = logger.Logger()
        if not self.config:
            from big_remote_play.utils.config import Config

            self.config = Config()

        self.setup_ui()

    def setup_ui(self):
        """Backup, restore and reset — the three actions that are about the
        stored data itself. Everything that changes how the app behaves lives
        on the page it affects, not in a separate window."""
        page = Adw.PreferencesPage(title=_("Backup"), name="backup", icon_name="brp-document-properties-symbolic")
        # The window header already carries the title; repeating it as a page
        # heading printed the same words twice.
        backup_group = Adw.PreferencesGroup(
            title=_("Backup"),
            description=f"{_('Your settings, paired networks and preferences, in a single file.')} {_('Backups can contain private certificates. Keep the file safe.')}",
        )
        backup_group.add(action_row(_("Create a backup"), _("Save your settings to a file you choose"), "brp-document-properties-symbolic", self.on_create_backup_clicked))
        backup_group.add(action_row(_("Restore a backup"), _("Replace the current settings with a saved file"), "brp-edit-undo-symbolic", self.on_restore_backup_clicked))
        page.add(backup_group)

        reset_group = Adw.PreferencesGroup(title=_("Reset"), description=_("Review carefully: these actions change or remove saved data."))
        restore_row = Adw.ActionRow(title=_("Restore Defaults"), subtitle=_("Reset all settings to default"))
        restore_button = Gtk.Button(label=_("Restore"), valign=Gtk.Align.CENTER)
        restore_button.connect("clicked", self.on_restore_defaults_clicked)
        restore_row.add_suffix(restore_button)
        restore_row.set_activatable_widget(restore_button)
        reset_group.add(restore_row)

        clear_row = Adw.ActionRow(title=_("Clear Everything"), subtitle=_("Remove logs, settings, servers and saved data"))
        clear_button = Gtk.Button(label=_("Clear Everything"), valign=Gtk.Align.CENTER)
        clear_button.add_css_class("destructive-action")
        clear_button.connect("clicked", self.on_clear_all_clicked)
        clear_row.add_suffix(clear_button)
        clear_row.set_activatable_widget(clear_button)
        reset_group.add(clear_row)
        page.add(reset_group)

        self.toast_overlay = Adw.ToastOverlay()
        toolbar = Adw.ToolbarView()
        toolbar.set_top_bar_style(Adw.ToolbarStyle.FLAT)
        toolbar.add_top_bar(Adw.HeaderBar())
        toolbar.set_content(page)
        self.toast_overlay.set_child(toolbar)
        self.set_content(self.toast_overlay)
        self._preferences_page = page

    def add_toast(self, toast: Adw.Toast) -> None:
        self.toast_overlay.add_toast(toast)

    # ── Backup ────────────────────────────────────────────────────────────

    def _backup_manager(self) -> BackupManager:
        from big_remote_play.utils.moonlight_config import MoonlightConfigManager

        return BackupManager(paths.CONFIG_DIR, paths.SUNSHINE_CONFIG_DIR, MoonlightConfigManager().config_file, app_version=__version__)

    def _run_storage_task(self, operation, *, progress: str, success: str, error_heading: str, on_success=None) -> None:
        """Run filesystem work away from GTK and marshal the result back."""
        self._preferences_page.set_sensitive(False)
        self.add_toast(Adw.Toast.new(progress))

        def finish(error):
            self._preferences_page.set_sensitive(True)
            if error is not None:
                _log.error("Storage operation failed: %s", error)
                body = str(error)
                if isinstance(error, BackupValidationError):
                    body = _("Choose a file created by “Create a backup”.")
                self._show_error(error_heading, body)
            else:
                self.add_toast(Adw.Toast.new(success))
                if on_success is not None:
                    on_success()
            return False

        def work():
            error = None
            try:
                operation()
            except Exception as caught:
                error = caught
            GLib.idle_add(finish, error)

        threading.Thread(target=work, daemon=True).start()

    def on_create_backup_clicked(self) -> None:
        dialog = Gtk.FileDialog(title=_("Create a backup"), initial_name=f"big-remote-play-{datetime.now():%Y-%m-%d}.tar.gz")

        def on_chosen(file_dialog, result):
            try:
                target = file_dialog.save_finish(result)
            except GLib.Error:
                return  # cancelled
            if target is None or target.get_path() is None:
                return
            destination = Path(target.get_path())
            self._run_storage_task(
                lambda: self._write_backup(destination),
                progress=_("Create a backup"),
                success=_("Backup saved."),
                error_heading=_("Could not create the backup"),
            )

        dialog.save(self, None, on_chosen)

    def _write_backup(self, destination: Path) -> None:
        self._backup_manager().create(destination)

    def on_restore_backup_clicked(self) -> None:
        dialog = Gtk.FileDialog(title=_("Restore a backup"))

        def on_chosen(file_dialog, result):
            try:
                chosen = file_dialog.open_finish(result)
            except GLib.Error:
                return  # cancelled
            if chosen is None or chosen.get_path() is None:
                return
            self._confirm_restore(Path(chosen.get_path()))

        dialog.open(self, None, on_chosen)

    def _confirm_restore(self, archive_path: Path) -> None:
        confirm = Adw.AlertDialog(
            heading=_("Replace the current settings?"),
            body=_("Replace the current settings with a saved file"),
        )
        confirm.add_response("cancel", _("Cancel"))
        confirm.add_response("restore", _("Restore"))
        confirm.set_response_appearance("restore", Adw.ResponseAppearance.DESTRUCTIVE)
        confirm.set_default_response("cancel")
        confirm.set_close_response("cancel")
        confirm.connect("response", lambda _dialog, response: self._start_restore(archive_path) if response == "restore" else None)
        confirm.present(self)

    def _restore_backup(self, archive_path: Path) -> None:
        self._backup_manager().restore(archive_path)

    def _start_restore(self, archive_path: Path) -> None:
        def close_after_restore() -> None:
            application = self.get_application()
            if application is not None:
                application.quit()
            else:
                self.close()

        self._run_storage_task(
            lambda: self._restore_backup(archive_path),
            progress=_("Restore a backup"),
            success=_("Settings restored! Restart the application to apply all changes."),
            error_heading=_("Could not restore the backup"),
            on_success=close_after_restore,
        )

    def _show_error(self, heading: str, body: str) -> None:
        dialog = Adw.AlertDialog(heading=heading, body=body)
        dialog.add_response("ok", _("OK"))
        dialog.present(self)

    def on_verbose_toggled(self, row, _param):
        enabled = row.get_active()
        self.config.set("verbose_logging", enabled)
        self.logger = logger.Logger(force_new=True)
        self.logger.set_verbose(enabled)
        self.logger.info(f"Verbose logging {'enabled' if enabled else 'disabled'}")

    def on_clear_logs_clicked(self, button):
        self.logger.clear_old_logs()
        diag = Adw.AlertDialog(heading=_("Logs Cleared"), body=_("Old log files have been removed."))
        diag.add_response("ok", _("OK"))
        diag.present(self)

    def copy_config_path(self, btn):
        self.get_clipboard().set(str(paths.CONFIG_DIR))
        self.add_toast(Adw.Toast.new(_("Path copied!")))

    def on_restore_defaults_clicked(self, button):
        # Updated text as requested
        msg = _("All your changes in Big Remote Play will be restored! This includes Sunshine and Moonlight settings.")

        dialog = Adw.AlertDialog(heading=_("Restore Defaults?"), body=msg)
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("restore", _("Restore"))
        dialog.set_response_appearance("restore", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def on_response(d, r):
            if r == "restore":
                try:
                    # Replace the file, including removal of obsolete keys.
                    if not self.config.reset_defaults():
                        raise OSError("Could not write the default application settings")
                    (paths.CONFIG_DIR / "vpn_choice.json").unlink(missing_ok=True)

                    # 2. Reset Sunshine Config (sunshine.conf)
                    # Delete the file so it regenerates cleanly or starts empty
                    sunshine_conf = paths.SUNSHINE_CONF
                    if sunshine_conf.exists():
                        os.remove(sunshine_conf)

                    # 3. Reset Moonlight Config
                    from big_remote_play.utils.moonlight_config import MoonlightConfigManager

                    mc = MoonlightConfigManager()
                    if not mc.reset_streaming_settings():
                        raise OSError("Could not reset Moonlight preferences")

                    self.add_toast(Adw.Toast.new(_("Settings restored! Restart the application to apply all changes.")))
                    self.close()
                except Exception as e:
                    _log.error(f"Error resetting configs: {e}")
                    self.add_toast(Adw.Toast.new(_("Error restoring: {error}").format(error=e)))

        dialog.connect("response", on_response)
        dialog.present(self)

    def on_clear_all_clicked(self, button):
        # Double check implementation
        dialog1 = Adw.AlertDialog(
            heading=_("Clear EVERYTHING?"),
            body=_(
                "WARNING: This will delete ALL data, logs, settings and saved servers, and remove saved passwords (Sunshine, ZeroTier, network auth keys) from the system keyring. The application will close."
            ),
        )
        dialog1.add_response("cancel", _("Cancel"))
        dialog1.add_response("clear", _("Delete All"))
        dialog1.set_response_appearance("clear", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog1.set_default_response("cancel")
        dialog1.set_close_response("cancel")

        def on_response1(d, r):
            if r == "clear":
                # Second confirmation (Double Check)
                dialog2 = Adw.AlertDialog(heading=_("Are You Absolutely Sure?"), body=_("This action is IRREVERSIBLE. You will lose all configured data."))
                dialog2.add_response("cancel", _("Cancel"))
                dialog2.add_response("destroy", _("Yes, Delete All"))
                dialog2.set_response_appearance("destroy", Adw.ResponseAppearance.DESTRUCTIVE)
                dialog2.set_default_response("cancel")
                dialog2.set_close_response("cancel")

                def on_response2(_d2, r2):
                    if r2 == "destroy":
                        self._perform_clear_all()

                dialog2.connect("response", on_response2)
                dialog2.present(self)

        dialog1.connect("response", on_response1)
        dialog1.present(self)

    def _wipe_keyring_secrets(self):
        """Clear all app secrets from the Secret Service before files are deleted.

        Must run BEFORE the config dir is removed, because the history file holds
        the keyring references for per-network auth keys.
        """
        from big_remote_play.private_network.credentials import CredentialStore
        from big_remote_play.private_network.legacy import clear_migrated_secrets
        from big_remote_play.utils.secret_store import SecretStore, SecretStoreUnavailable
        from big_remote_play.utils.sunshine_credentials import SUNSHINE_PASSWORD_KEY
        from big_remote_play.ui.private_network_view import _load_history, _clear_history_secrets

        store = SecretStore()
        try:
            store.clear(SUNSHINE_PASSWORD_KEY)
            # Every API credential (ZeroTier, Tailscale, Headscale per server) and
            # the values moved out of older plain-text history files.
            CredentialStore(store).remove_all()
            clear_migrated_secrets(store)
        except SecretStoreUnavailable:
            pass
        try:
            for entry in _load_history():
                _clear_history_secrets(entry)
        except Exception as exc:
            _log.error(f"Error clearing history secrets: {exc}")

    def _perform_clear_all(self):
        def close_after_clear() -> None:
            app = self.get_application()
            if app:
                app.quit()
            else:
                self.close()

        self._run_storage_task(
            self._clear_all_data,
            progress=_("Clear Everything"),
            success=_("Clear Everything"),
            error_heading=_("Error Clearing"),
            on_success=close_after_clear,
        )

    def _clear_all_data(self) -> None:
        try:
            # 0. Keyring secrets (Sunshine password, ZeroTier token, per-network
            #    auth keys) — before the files that reference them are removed.
            self._wipe_keyring_secrets()

            # Moonlight owns its paired hosts and client identity. Clear only
            # the streaming preferences managed by Big Remote Play.
            from big_remote_play.utils.moonlight_config import MoonlightConfigManager

            if not MoonlightConfigManager().reset_streaming_settings():
                raise OSError("Could not reset Moonlight preferences")

            # 1. Config Dir (canonical + any leftover legacy dir)
            for config_dir in (paths.CONFIG_DIR, paths.legacy_config_dir()):
                if config_dir.exists():
                    shutil.rmtree(config_dir)

            # 2. Cache/Logs
            for cache_dir in (Path.home() / ".cache" / "big-remote-play", Path.home() / ".cache" / "big-remoteplay"):
                if cache_dir.exists():
                    shutil.rmtree(cache_dir)

            _log.info("All data cleared.")
        except Exception:
            _log.exception("Could not clear all Big Remote Play data")
            raise
