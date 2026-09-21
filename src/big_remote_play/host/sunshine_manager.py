import logging
import subprocess, signal, os, shutil
import base64
import hashlib
import http.client
import json
import ssl
import urllib.parse
from pathlib import Path
from typing import NamedTuple
from big_remote_play import paths
from big_remote_play.integration_contracts import sunshine_web_ui_port
from big_remote_play.utils.i18n import _
from big_remote_play.utils.secure_io import secure_write_text

_log = logging.getLogger("big-remoteplay")


class PinResult(NamedTuple):
    """Outcome of send_pin(). ``status`` is the HTTP status (0 = transport/cert
    failure); callers branch on it instead of parsing the localized ``message``."""

    ok: bool
    status: int
    message: str


# Sunshine config web server. 127.0.0.1 avoids IPv6 (::1) quirks when Sunshine
# binds 0.0.0.0. TLS uses a self-signed cert verified via trust-on-first-use
# fingerprint pinning (see _api_request), not a CA chain.
API_HOST = "127.0.0.1"
API_PORT = 47990


def _cert_fingerprint(cert_der: bytes) -> str:
    """SHA-256 hex of a DER-encoded certificate."""
    return hashlib.sha256(cert_der).hexdigest()


class SunshineHost:
    def __init__(self, cdir: Path | None = None):
        self.config_dir = cdir or paths.SUNSHINE_CONFIG_DIR
        self.config_dir.mkdir(parents=True, exist_ok=True)
        # TOFU store for Sunshine's self-signed API certificate fingerprint.
        self.cert_fp_file = self.config_dir / "sunshine_cert.sha256"
        self.process = None
        self.pid = None

    def _config_value(self, key: str, default: str) -> str:
        try:
            for line in (self.config_dir / "sunshine.conf").read_text().splitlines():
                if line.lstrip().startswith("#") or "=" not in line:
                    continue
                name, value = line.split("=", 1)
                if name.strip() == key:
                    return value.strip()
        except OSError:
            pass
        return default

    @property
    def api_port(self) -> int:
        try:
            return sunshine_web_ui_port(int(self._config_value("port", "47989")))
        except ValueError:
            return API_PORT

    @property
    def web_ui_url(self) -> str:
        return f"https://localhost:{self.api_port}"

    def ensure_desktop_app(self) -> bool:
        """Add Desktop without erasing the user's apps, environment or path."""
        app_file = Path(self._config_value("file_apps", "apps.json"))
        if not app_file.is_absolute():
            app_file = self.config_dir / app_file
        try:
            data = json.loads(app_file.read_text()) if app_file.exists() else {"env": {}, "apps": []}
            if not isinstance(data, dict) or not isinstance(data.get("apps"), list):
                return False
            if not any(isinstance(app, dict) and app.get("name") == "Desktop" for app in data["apps"]):
                data["apps"].append({"name": "Desktop", "cmd": ""})
                secure_write_text(str(app_file), json.dumps(data, indent=2))
            return True
        except (OSError, ValueError):
            return False

    def start(self, **kwargs):
        if self.is_running():
            return True, "Already running"

        sc = shutil.which("sunshine")
        if not sc:
            return False, "Sunshine executable not found"
        try:
            config_file = self.config_dir / "sunshine.conf"
            # Prepare environment
            env = os.environ.copy()
            if "DISPLAY" not in env:
                env["DISPLAY"] = ":0"

            if "XAUTHORITY" not in env:
                home = os.path.expanduser("~")
                xauth = os.path.join(home, ".Xauthority")
                if os.path.exists(xauth):
                    env["XAUTHORITY"] = xauth

            if "XDG_RUNTIME_DIR" not in env:
                uid = os.getuid()
                runtime_dir = f"/run/user/{uid}"
                if os.path.exists(runtime_dir):
                    env["XDG_RUNTIME_DIR"] = runtime_dir

            # Pass WAYLAND_DISPLAY if exists
            if "WAYLAND_DISPLAY" in os.environ:
                env["WAYLAND_DISPLAY"] = os.environ["WAYLAND_DISPLAY"]

            cmd = [sc, str(config_file)]

            # Start process redirecting logs to file
            log_path = self.config_dir / "sunshine.log"
            self.log_file = open(log_path, "a")

            self.process = subprocess.Popen(
                cmd,
                text=True,
                stdout=self.log_file,
                stderr=subprocess.STDOUT,
                env=env,
                cwd=str(self.config_dir),  # Force CWD for local configs
                start_new_session=True,  # Create new group ID
            )

            self.pid = self.process.pid

            # Check if process died immediately (e.g. library error)
            try:
                # Wait a bit to see if startup fails
                exit_code = self.process.wait(timeout=2.0)

                # If reached here, process ended (failed)
                self.log_file.flush()
                # Try to read the error from the log
                error_detail = ""
                try:
                    with open(log_path, "r") as f:
                        lines = f.readlines()
                        if lines:
                            # Look for shared library errors in the last 10 lines
                            for line in lines[-10:]:
                                if "error while loading shared libraries" in line or "symbol lookup error" in line:
                                    error_detail = line.strip()
                                    break
                except Exception:
                    pass

                self.log_file.write(_("Sunshine failed to start (Exit code {}).\n").format(exit_code))
                if error_detail:
                    _log.error(_("Sunshine failed to start: {}").format(error_detail))
                else:
                    _log.error(_("Sunshine failed to start (Exit code {}). Check logs.").format(exit_code))

                self.process = None
                self.pid = None
                return False, error_detail if error_detail else f"Exit code {exit_code}"

            except subprocess.TimeoutExpired:
                # Process continues running after timeout, success!
                pass

            # Save PID
            pid_file = self.config_dir / "sunshine.pid"
            with open(pid_file, "w") as f:
                f.write(str(self.pid))

            _log.info(_("Sunshine started (PID: {})").format(self.pid))
            return True, None

        except Exception as e:
            _log.error(_("Error starting Sunshine: {}").format(e))
            return False, str(e)  # Return tuple (success, error_message)

    def stop(self) -> bool:
        """Stops the Sunshine server this object started.

        Kills by tracked process group first, then the PID file. Falls back to a
        name-*exact* pkill ONLY when we hold no handle on the instance, so we
        never take down an unrelated `sunshine` process the app didn't spawn.
        """
        if not self.is_running():
            _log.info(_("Sunshine is not running"))
            return False

        killed_tracked = False
        try:
            if self.process:
                try:
                    pgid = os.getpgid(self.process.pid)
                    os.killpg(pgid, signal.SIGTERM)
                    try:
                        self.process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        os.killpg(pgid, signal.SIGKILL)
                    killed_tracked = True
                except Exception:
                    try:
                        self.process.terminate()
                        killed_tracked = True
                    except Exception:
                        pass
            else:
                pid_file = self.config_dir / "sunshine.pid"
                if pid_file.exists():
                    try:
                        with open(pid_file, "r") as f:
                            pid = int(f.read().strip())
                        os.kill(pid, signal.SIGTERM)
                        killed_tracked = True
                    except Exception:
                        pass

            # Name-scoped fallback only when we could not target our own instance.
            if not killed_tracked:
                subprocess.run(["pkill", "-x", "sunshine"], stderr=subprocess.DEVNULL, timeout=10)

            # Close log
            if hasattr(self, "log_file"):
                try:
                    self.log_file.close()
                except Exception:
                    pass
                del self.log_file

            pid_file = self.config_dir / "sunshine.pid"
            if pid_file.exists():
                pid_file.unlink()

            self.process = None
            self.pid = None
            return True
        except Exception:
            if not killed_tracked:
                subprocess.run(["pkill", "-9", "-x", "sunshine"], stderr=subprocess.DEVNULL, timeout=10)
            return False

    def restart(self) -> tuple[bool, str | None]:
        """Stop then start the server; returns start()'s (ok, error) result."""
        self.stop()
        return self.start()

    def is_running(self) -> bool:
        """Checks if Sunshine is running"""
        # Check process directly
        if self.process and self.process.poll() is None:
            return True

        # Check PID file
        pid_file = self.config_dir / "sunshine.pid"
        if pid_file.exists():
            try:
                with open(pid_file, "r") as f:
                    pid = int(f.read().strip())

                # Check if process exists
                os.kill(pid, 0)
                return True

            except (OSError, ValueError):
                # Process does not exist, clear PID file
                pid_file.unlink()
                return False

        # Check via pgrep
        try:
            result = subprocess.run(["pgrep", "-x", "sunshine"], capture_output=True, timeout=5)
            return result.returncode == 0
        except Exception:
            return False

    def get_status(self) -> dict:
        """Gets server status"""
        return {
            "running": self.is_running(),
            "pid": self.pid,
            "config_dir": str(self.config_dir),
        }

    def update_apps(self, apps_list: list) -> bool:
        """
        Updates application list (apps.json)

        Args:
            apps_list: List of dictionaries describing apps
                       Ex: [{'name': 'Steam', 'cmd': 'steam', ...}]
        """
        try:
            import json

            apps_file = self.config_dir / "apps.json"

            # Sunshine apps.json format
            data = {"env": {"PATH": "$(PATH):$(HOME)/.local/bin"}, "apps": apps_list}

            with open(apps_file, "w") as f:
                json.dump(data, f, indent=4)

            return True
        except Exception as e:
            _log.error(f"Error saving apps.json: {e}")
            return False

    def configure(self, settings: dict) -> bool:
        """
        Configures Sunshine

        Args:
            settings: Dictionary with settings
        """
        try:
            config_file = self.config_dir / "sunshine.conf"

            # Load existing config
            current_config = {}
            if config_file.exists():
                try:
                    with open(config_file, "r") as f:
                        for line in f:
                            line = line.strip()
                            if not line or line.startswith("#"):
                                continue
                            if "=" in line:
                                parts = line.split("=", 1)
                                if len(parts) == 2:
                                    current_config[parts[0].strip()] = parts[1].strip()
                except Exception as e:
                    _log.error(f"Error reading existing config: {e}")
                    return False

            # Update with new settings
            for k, v in settings.items():
                if v is None:
                    # Remove key if value is None
                    if k in current_config:
                        del current_config[k]
                else:
                    current_config[k] = str(v)

            # Sunshine's documented key is file_apps; leave an explicit path intact.
            current_config.setdefault("file_apps", current_config.pop("apps_file", "apps.json"))
            # Options emitted by older Big Remote Play versions, ignored by Sunshine.
            for key in ("platform", "wayland.display", "fps", "bitrate", "videocodec", "webserver", "enable_api_endpoints", "audio", "log_level", "min_bitrate"):
                current_config.pop(key, None)

            # Save merged config
            secure_write_text(str(config_file), "".join(f"{key} = {value}\n" for key, value in current_config.items()))

            return True

        except Exception as e:
            _log.error(f"Error configuring Sunshine: {e}")
            return False

    def _trust_fingerprint(self, fingerprint: str) -> bool:
        """Trust-on-first-use check for Sunshine's self-signed API cert.

        First contact pins the fingerprint (0600 file); later connections must
        match exactly. A mismatch means the cert changed (Sunshine reinstall) or
        a local process is impersonating the API: refuse rather than trust blindly.
        """
        try:
            if self.cert_fp_file.exists():
                return self.cert_fp_file.read_text().strip() == fingerprint
            secure_write_text(str(self.cert_fp_file), fingerprint)
            return True
        except Exception as exc:
            _log.error(_("Certificate trust error: {}").format(exc))
            return False

    def _api_request(self, method: str, path: str, payload: dict | None = None, auth: tuple[str, str] | None = None, timeout: float = 5.0) -> tuple[int, bytes]:
        """Calls the Sunshine config API over TLS with TOFU cert pinning.

        Returns (status_code, body_bytes). status 0 means the connection failed
        or the certificate fingerprint did not match the pinned value.
        """
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        # Self-signed cert: skip CA validation; we verify by pinned fingerprint.
        ctx.verify_mode = ssl.CERT_NONE
        try:
            # Some Sunshine builds negotiate legacy TLS renegotiation.
            ctx.options |= 0x4  # ssl.OP_LEGACY_SERVER_CONNECT
        except Exception:
            pass

        conn = http.client.HTTPSConnection(API_HOST, self.api_port, context=ctx, timeout=timeout)
        try:
            conn.connect()
            cert_der = conn.sock.getpeercert(binary_form=True)
            if not cert_der or not self._trust_fingerprint(_cert_fingerprint(cert_der)):
                return 0, b""

            headers = {"Content-Type": "application/json"}
            if auth:
                username, password = auth
                token = base64.b64encode(f"{username}:{password}".encode()).decode()
                headers["Authorization"] = f"Basic {token}"

            body = json.dumps(payload).encode("utf-8") if payload is not None else None
            conn.request(method, path, body=body, headers=headers)
            response = conn.getresponse()
            return response.status, response.read()
        except Exception as exc:
            _log.error(_("Sunshine API request failed: {}").format(exc))
            return 0, b""
        finally:
            conn.close()

    def send_pin(self, pin: str, name: str | None = None, auth: tuple[str, str] | None = None) -> PinResult:
        """Sends a pairing PIN to Sunshine (POST /api/pin).

        Returns a PinResult; ``status`` is the HTTP status so callers can branch
        (401 = bad credentials, 307 = no admin user yet) without reading prose.
        """
        payload = {"pin": pin}
        if name:
            payload["name"] = name

        status, data = self._api_request("POST", "/api/pin", payload, auth)
        if status == 0:
            return PinResult(False, 0, _("Connection error: Sunshine is unreachable or its certificate does not match"))
        if status == 200:
            try:
                reply = json.loads(data)
                accepted = isinstance(reply, dict) and reply.get("status") is True
            except (ValueError, TypeError):
                accepted = False
            if accepted:
                return PinResult(True, 200, _("PIN sent successfully"))
            return PinResult(False, 200, _("Sunshine rejected the PIN"))
        if status == 401:
            return PinResult(False, 401, _("Authentication Failed. Configure a user in Sunshine."))
        # 307 means no admin user exists yet; the caller offers to create one.
        return PinResult(False, status, _("API Error: {}").format(status))

    def set_credentials(self, new_username: str, new_password: str, current: tuple[str, str] | None = None) -> tuple[bool, str]:
        """Sets or changes the Sunshine admin credentials (POST /api/password).

        First-run create: leave current empty. Password change: pass the existing
        (username, password) as current; Sunshine requires it to authorize and
        authenticate the change.
        """
        current_user, current_password = current if current else ("", "")
        payload = {
            "currentUsername": current_user,
            "currentPassword": current_password,
            "newUsername": new_username,
            "newPassword": new_password,
            "confirmNewPassword": new_password,
        }
        # When current credentials exist, authenticate the request with them.
        auth = current if current else None
        status, data = self._api_request("POST", "/api/password", payload, auth)
        if status == 0:
            return False, _("Connection error: Sunshine is unreachable or its certificate does not match")
        if status == 401:
            return False, _("Authentication Failed. Check the current password.")
        if status == 200:
            try:
                if not json.loads(data).get("status", True):
                    return False, _("Sunshine rejected the credentials")
            except Exception:
                pass
            return True, _("Credentials updated successfully")
        return False, _("API Error: {}").format(status)

    def create_user(self, username: str, password: str) -> tuple[bool, str]:
        """Creates the Sunshine admin user (first-run, no existing credentials)."""
        return self.set_credentials(username, password)

    def reset_credentials(self, new_username: str, new_password: str) -> tuple[bool, str]:
        """Resets admin credentials WITHOUT the current password (`sunshine --creds`).

        For a lost/forgotten password: this writes Sunshine's credentials file
        directly via the CLI, bypassing the API auth. Restart Sunshine afterwards
        for a running instance to pick up the new credentials.
        """
        if not new_username or not new_password:
            return False, _("Username and password cannot be empty")
        sc = shutil.which("sunshine")
        if not sc:
            return False, _("Sunshine executable not found")
        env = os.environ.copy()
        try:
            # argv array, no shell; credentials are not logged.
            res = subprocess.run([sc, "--creds", new_username, new_password], capture_output=True, text=True, env=env, timeout=15)
            if res.returncode == 0:
                return True, _("Credentials reset successfully")
            detail = (res.stderr or res.stdout or "").strip()
            return False, _("Reset failed: {}").format(detail[:200]) if detail else _("Reset failed")
        except Exception as exc:
            return False, _("Reset error: {}").format(exc)

    def list_clients(self, auth: tuple[str, str] | None = None) -> list:
        """Lists paired devices (GET /api/clients/list).

        Each entry exposes name, uuid and enabled. Note: these are paired
        clients, not live stream sessions (Sunshine exposes no session API).
        """
        status, data = self._api_request("GET", "/api/clients/list", auth=auth)
        if status != 200 or not data:
            return []
        try:
            obj = json.loads(data)
        except Exception:
            return []
        return obj.get("named_certs", []) if isinstance(obj, dict) else []

    def unpair_client(self, uuid: str, auth: tuple[str, str] | None = None) -> bool:
        """Removes a single paired device (POST /api/clients/unpair)."""
        if not uuid:
            return False
        status, _data = self._api_request("POST", "/api/clients/unpair", {"uuid": uuid}, auth)
        return status == 200

    def unpair_all_clients(self, auth: tuple[str, str] | None = None) -> bool:
        """Removes all paired devices (POST /api/clients/unpair-all)."""
        status, _data = self._api_request("POST", "/api/clients/unpair-all", {}, auth)
        return status == 200

    def set_client_enabled(self, uuid: str, enabled: bool, auth: tuple[str, str] | None = None) -> bool:
        """Enables or disables a paired device (POST /api/clients/update)."""
        if not uuid:
            return False
        status, _data = self._api_request("POST", "/api/clients/update", {"uuid": uuid, "enabled": enabled}, auth)
        return status == 200

    def get_logs(self, auth: tuple[str, str] | None = None) -> str:
        """Fetches the Sunshine log (GET /api/logs, text/plain)."""
        status, data = self._api_request("GET", "/api/logs", auth=auth)
        if status != 200 or not data:
            return ""
        return data.decode("utf-8", errors="replace")

    def close_app(self, auth: tuple[str, str] | None = None) -> bool:
        """Closes the currently streaming app (POST /api/apps/close).

        Gentle stop: ends the app for the guest without root, unlike the
        socket-level kill in drop_guest.sh which evicts a network address.
        """
        status, _data = self._api_request("POST", "/api/apps/close", {}, auth)
        return status == 200

    def get_apps(self, auth: tuple[str, str] | None = None) -> list:
        """Lists configured Sunshine apps (GET /api/apps)."""
        status, data = self._api_request("GET", "/api/apps", auth=auth)
        if status != 200 or not data:
            return []
        try:
            obj = json.loads(data)
        except Exception:
            return []
        return obj.get("apps", []) if isinstance(obj, dict) else []

    def add_app(self, entry: dict, auth: tuple[str, str] | None = None) -> bool:
        """Adds or replaces a Sunshine app (POST /api/apps).

        entry must follow the Sunshine app schema (name, cmd, image-path,
        detached, prep-cmd, ...). index defaults to -1 (append); a real index
        replaces that slot. Apps are re-sorted by name server-side.
        """
        payload = dict(entry)
        payload.setdefault("index", -1)
        status, _data = self._api_request("POST", "/api/apps", payload, auth)
        return status == 200

    def delete_app(self, index: int, auth: tuple[str, str] | None = None) -> bool:
        """Removes a Sunshine app by index (DELETE /api/apps/{index})."""
        if index is None or index < 0:
            return False
        status, _data = self._api_request("DELETE", f"/api/apps/{index}", auth=auth)
        return status == 200

    def upload_cover(self, key: str, url: str | None = None, data_b64: str | None = None, auth: tuple[str, str] | None = None) -> str:
        """Uploads box art for an app (POST /api/covers/upload).

        Provide either a url (images.igdb.com only) or base64 image data.
        Returns the saved cover path, or "" on failure.
        """
        if not key or (not url and not data_b64):
            return ""
        payload = {"key": key}
        if url:
            payload["url"] = url
        if data_b64:
            payload["data"] = data_b64
        status, data = self._api_request("POST", "/api/covers/upload", payload, auth)
        if status != 200 or not data:
            return ""
        try:
            return json.loads(data).get("path", "")
        except Exception:
            return ""

    def get_config(self, auth: tuple[str, str] | None = None) -> dict:
        """Fetches the canonical Sunshine config incl. defaults (GET /api/config).

        The response also carries status/platform/version metadata keys.
        """
        status, data = self._api_request("GET", "/api/config", auth=auth)
        if status != 200 or not data:
            return {}
        try:
            obj = json.loads(data)
        except Exception:
            return {}
        return obj if isinstance(obj, dict) else {}

    def save_config(self, settings: dict, auth: tuple[str, str] | None = None) -> bool:
        """Updates Sunshine config while it runs (POST /api/config).

        Server skips null/empty values. Use only when Sunshine is up; the
        pre-start path stays in configure() (file write).
        """
        status, _data = self._api_request("POST", "/api/config", dict(settings), auth)
        return status == 200

    def restart_via_api(self, auth: tuple[str, str] | None = None) -> bool:
        """Restarts Sunshine through the API (POST /api/restart).

        The process restarts and the connection commonly drops mid-request, so
        a transport failure (status 0) is treated as a likely success here.
        """
        status, _data = self._api_request("POST", "/api/restart", {}, auth)
        return status in (200, 0)

    def browse(self, path: str, type_filter: str = "any", auth: tuple[str, str] | None = None) -> dict:
        """Browses the host filesystem (GET /api/browse).

        type_filter: directory | executable | file | any. Returns
        {path, parent, entries:[{name,type,path}]}, or {} on failure.
        """
        query = urllib.parse.urlencode({"path": path or "", "type": type_filter})
        status, data = self._api_request("GET", f"/api/browse?{query}", auth=auth)
        if status != 200 or not data:
            return {}
        try:
            obj = json.loads(data)
        except Exception:
            return {}
        return obj if isinstance(obj, dict) else {}
