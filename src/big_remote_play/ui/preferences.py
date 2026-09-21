"""Backup, restore and reset of everything this application stores."""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gtk, Adw, GLib  # type: ignore
import logging

_log = logging.getLogger("big-remoteplay")

import os, shutil, sys, tarfile
from datetime import datetime
from pathlib import Path

import big_remote_play.utils.logger as logger
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
        backup_group = Adw.PreferencesGroup(title=_("Backup"), description=_("Your settings, paired networks and preferences, in a single file."))
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

    def add_toast(self, toast: Adw.Toast) -> None:
        self.toast_overlay.add_toast(toast)

    # ── Backup ────────────────────────────────────────────────────────────

    def _backup_sources(self) -> list[Path]:
        """Directories worth carrying to another machine or another install."""
        from big_remote_play.utils.moonlight_config import MoonlightConfigManager

        moonlight = MoonlightConfigManager().config_file
        sources = [paths.CONFIG_DIR, paths.SUNSHINE_CONFIG_DIR]
        if moonlight is not None:
            sources.append(Path(moonlight).parent)
        return [path for path in sources if path.exists()]

    def on_create_backup_clicked(self) -> None:
        dialog = Gtk.FileDialog(title=_("Create a backup"), initial_name=f"big-remote-play-{datetime.now():%Y-%m-%d}.tar.gz")

        def on_chosen(file_dialog, result):
            try:
                target = file_dialog.save_finish(result)
            except GLib.Error:
                return  # cancelled
            if target is None or target.get_path() is None:
                return
            try:
                self._write_backup(Path(target.get_path()))
                self.add_toast(Adw.Toast.new(_("Backup saved.")))
            except OSError as error:
                _log.error(f"Backup failed: {error}")
                self._show_error(_("Could not create the backup"), str(error))

        dialog.save(self, None, on_chosen)

    def _write_backup(self, destination: Path) -> None:
        """Write the archive beside the target, then replace it in one step, so
        an interrupted write never leaves a truncated backup behind."""
        import tempfile

        fd, filename = tempfile.mkstemp(prefix=".brp-backup-", dir=destination.parent)
        temporary = Path(filename)
        try:
            # Backups include native TLS keys; keep them owner-only even when
            # the caller's umask is permissive. Do not follow a predictable .part.
            with os.fdopen(fd, "wb") as output:
                with tarfile.open(fileobj=output, mode="w:gz") as archive:
                    for source in self._backup_sources():

                        def include(info, root=source):
                            relative = Path(info.name).relative_to(root.name)
                            original = root / relative
                            # Saving inside the config directory must not archive
                            # the output recursively or external files via links.
                            if info.issym() or info.islnk() or original.resolve() in (temporary.resolve(), destination.resolve()):
                                return None
                            return info if info.isfile() or info.isdir() else None

                        archive.add(source, arcname=source.name, filter=include)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)

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
        confirm.connect("response", lambda _dialog, response: self._restore_backup(archive_path) if response == "restore" else None)
        confirm.present(self)

    def _restore_backup(self, archive_path: Path) -> None:
        try:
            with tarfile.open(archive_path, "r:gz") as archive:
                members = archive.getmembers()
                from big_remote_play.utils.moonlight_config import MoonlightConfigManager

                moonlight_file = MoonlightConfigManager().config_file
                destinations = {source.name: source for source in (paths.CONFIG_DIR, paths.SUNSHINE_CONFIG_DIR)}
                if moonlight_file is not None:
                    destinations[Path(moonlight_file).parent.name] = Path(moonlight_file).parent
                if len(members) > 10000 or sum(member.size for member in members) > 128 * 1024 * 1024:
                    raise ValueError("Backup exceeds the supported size limit")
                names = {Path(member.name).parts[0] for member in members if member.name}
                if not names or not names.issubset(destinations):
                    self._show_error(_("This file is not a Big Remote Play backup"), _("Choose a file created by “Create a backup”."))
                    return
                for member in members:
                    # A crafted archive must not write outside the restore root.
                    if not (member.isfile() or member.isdir()) or os.path.isabs(member.name) or ".." in Path(member.name).parts:
                        self._show_error(_("This backup cannot be restored"), _("The file contains unexpected paths."))
                        return
                # Preflight against actual destinations, including Moonlight's
                # Flatpak or XDG path, not a guessed common parent directory.
                targets = []
                for member in members:
                    parts = Path(member.name).parts
                    target = destinations[parts[0]].joinpath(*parts[1:])
                    if target.is_symlink() or any(parent.is_symlink() for parent in target.parents):
                        raise ValueError("Restore path contains a symbolic link")
                    targets.append((member, target))
                import tempfile

                for member, target in targets:
                    if member.isdir():
                        target.mkdir(parents=True, exist_ok=True, mode=0o700)
                        continue
                    data = archive.extractfile(member)
                    if data is None:
                        raise ValueError("Backup file could not be read")
                    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    fd, temporary = tempfile.mkstemp(prefix=".brp-restore-", dir=target.parent)
                    try:
                        with os.fdopen(fd, "wb") as output, data:
                            shutil.copyfileobj(data, output)
                            output.flush()
                            os.fsync(output.fileno())
                        os.replace(temporary, target)
                    finally:
                        if os.path.exists(temporary):
                            os.unlink(temporary)

        except (OSError, ValueError, tarfile.TarError) as error:
            _log.error(f"Restore failed: {error}")
            self._show_error(_("Could not restore the backup"), str(error))
            return

        application = self.get_application()
        if application is not None:
            application.quit()
        else:
            sys.exit(0)

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
                    # 1. Reset Main Config (config.json)
                    # We load defaults and apply them.
                    # Ideally we should clear unknown keys too, but setting defaults covers most.
                    default_conf = self.config.default_config()
                    for k, v in default_conf.items():
                        self.config.set(k, v)
                    self.config.save()

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
                    self.add_toast(Adw.Toast.new(_("Error restoring: {}").format(e)))

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
        from big_remote_play.utils.secret_store import SecretStore, SecretStoreUnavailable
        from big_remote_play.utils.sunshine_credentials import SUNSHINE_PASSWORD_KEY
        from big_remote_play.ui.private_network_view import _ZEROTIER_TOKEN_KEY, _load_history, _clear_history_secrets

        store = SecretStore()
        for key in (SUNSHINE_PASSWORD_KEY, _ZEROTIER_TOKEN_KEY):
            try:
                store.clear(key)
            except SecretStoreUnavailable:
                pass
        try:
            for entry in _load_history():
                _clear_history_secrets(entry)
        except Exception as exc:
            _log.error(f"Error clearing history secrets: {exc}")

    def _perform_clear_all(self):
        try:
            # 0. Keyring secrets (Sunshine password, ZeroTier token, per-network
            #    auth keys) — before the files that reference them are removed.
            self._wipe_keyring_secrets()

            # 1. Config Dir (canonical + any leftover legacy dir)
            for config_dir in (paths.CONFIG_DIR, paths.legacy_config_dir()):
                if config_dir.exists():
                    shutil.rmtree(config_dir)

            # 2. Cache/Logs
            for cache_dir in (Path.home() / ".cache" / "big-remote-play", Path.home() / ".cache" / "big-remoteplay"):
                if cache_dir.exists():
                    shutil.rmtree(cache_dir)

            # 3. Moonlight Config (~/.config/Moonlight Game Streaming Project)
            moon_dir = Path.home() / ".config" / "Moonlight Game Streaming Project"
            if moon_dir.exists():
                shutil.rmtree(moon_dir)

            # 4. Moonlight Flatpak/Var Config (if any)
            # Not deleting global flatpak data to be safe, but can check specific paths if needed.

            _log.info("All data cleared.")
            # Quit app
            app = self.get_application()
            if app:
                app.quit()
            else:
                sys.exit(0)

        except Exception as e:
            err_dlg = Adw.AlertDialog(heading=_("Error Clearing"), body=str(e))
            err_dlg.add_response("ok", _("OK"))
            err_dlg.present(self)
