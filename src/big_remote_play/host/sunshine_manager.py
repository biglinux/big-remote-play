import logging
import subprocess, signal, os
import base64
import hashlib
import http.client
import json
import re
import socket
import ssl
import time
import urllib.parse
from pathlib import Path
from collections.abc import Callable
from typing import NamedTuple
from big_remote_play import paths
from big_remote_play.integration_contracts import sunshine_web_ui_port
from big_remote_play.utils.i18n import _
from big_remote_play.utils.secure_io import secure_write_text

_log = logging.getLogger("big-remoteplay")


class PendingPairing(NamedTuple):
    """A Moonlight client waiting for its PIN (Sunshine ``GET /api/pin``)."""

    pairing_id: str
    name: str
    address: str


class PinResult(NamedTuple):
    """Outcome of send_pin(). ``status`` is the HTTP status (0 = transport/cert
    failure); callers branch on it instead of parsing the localized ``message``.

    ``PIN_CHOOSE`` means several clients are waiting: ``pending`` lists them
    and the caller asks which one to approve. ``PIN_NONE_WAITING`` means no
    client has started pairing yet.
    """

    ok: bool
    status: int
    message: str
    pending: tuple[PendingPairing, ...] = ()


# Sunshine config web server. 127.0.0.1 avoids IPv6 (::1) quirks when Sunshine
# binds 0.0.0.0. TLS uses a self-signed cert verified via trust-on-first-use
# fingerprint pinning (see _api_request), not a CA chain.
API_HOST = "127.0.0.1"
API_PORT = 47990


def sunshine_executable() -> str | None:
    """The Sunshine executable, also outside a desktop session's PATH."""
    from big_remote_play.utils.dependencies import COMPONENTS, find_executable

    return find_executable(COMPONENTS["sunshine"].executables)


def _cert_fingerprint(cert_der: bytes) -> str:
    """SHA-256 hex of a DER-encoded certificate."""
    return hashlib.sha256(cert_der).hexdigest()


def _process_name(pid: int) -> str:
    """The kernel's short command name for ``pid`` (``""`` if unknown)."""
    try:
        return Path(f"/proc/{int(pid)}/comm").read_text(encoding="utf-8", errors="replace").strip()
    except (OSError, ValueError):
        return ""


def _is_zombie(pid: int) -> bool:
    """True for an exited process its parent has not collected yet."""
    try:
        stat = Path(f"/proc/{int(pid)}/stat").read_text(encoding="utf-8", errors="replace")
    except (OSError, ValueError):
        return False
    # The state letter follows the parenthesised command name, which may itself contain spaces.
    return stat.rpartition(")")[2].split()[:1] == ["Z"]


def _is_live_sunshine(pid: int) -> bool:
    return _process_name(pid) == "sunshine" and not _is_zombie(pid)


# Sunshine restores the default output it switched to its own virtual sink only
# when it exits normally, so it gets time to finish before being killed.
STOP_GRACE_SECONDS = 10.0
# Sunshine validates capture and encoders before opening its local API.  Wait
# long enough to catch delayed probe crashes, but do not block indefinitely on
# a portal screen picker that still needs the user.
STARTUP_PROBE_SECONDS = 10.0
STARTUP_PROBE_INTERVAL = 0.1


PIN_CHOOSE = 300
# Seconds Sunshine may take to answer a PIN (it waits for the device's check).
PIN_ANSWER_TIMEOUT = 20.0
PIN_NONE_WAITING = 409
_PAIRING_ID_RE = re.compile(r"^[0-9a-fA-F]{32}$")
_CURRENT_GAME_RE = re.compile(r"<currentgame>\s*(\d{1,10})\s*</currentgame>")


def game_window_screen() -> tuple[bool, str | None]:
    """Whether a Game Window share is live, and the private screen it uses."""
    from big_remote_play.host import window_capture

    state = window_capture.read_state()
    if not window_capture.helper_running(state):
        return False, None
    socket = str((state or {}).get("socket") or "")
    return True, socket or None


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

    def _api_is_reachable(self) -> bool:
        """Whether Sunshine has opened its local configuration endpoint."""
        try:
            with socket.create_connection((API_HOST, self.api_port), timeout=0.25):
                return True
        except OSError:
            return False

    def _wait_for_startup(self) -> int | None:
        """Return an early exit code, or None once ready/still starting."""
        deadline = time.monotonic() + STARTUP_PROBE_SECONDS
        while time.monotonic() < deadline:
            if self.process is None:
                return 1
            exit_code = self.process.poll()
            if exit_code is not None:
                return exit_code
            if self._api_is_reachable():
                # Close-to-ready crashes still win over the successful socket
                # probe when both happen in the same iteration.
                return self.process.poll()
            time.sleep(STARTUP_PROBE_INTERVAL)
        return self.process.poll() if self.process is not None else 1

    @staticmethod
    def sunshine_data_dir() -> Path:
        """Where Sunshine resolves relative paths from its configuration.

        Sunshine uses its own data directory (``$XDG_CONFIG_HOME/sunshine``)
        for relative ``file_apps``, ``file_state`` and similar paths, not the
        directory of the configuration file it was started with. Seen for
        real: with ``file_apps = apps.json`` the apps offered to devices came
        from ``~/.config/sunshine/apps.json``.
        """
        root = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
        return Path(root) / "sunshine"

    def apps_file(self) -> Path:
        """The app library Sunshine really serves."""
        app_file = Path(self._config_value("file_apps", "apps.json")).expanduser()
        return app_file if app_file.is_absolute() else self.sunshine_data_dir() / app_file

    def ensure_desktop_app(self) -> bool:
        """Add Desktop without erasing the user's apps, environment or path."""
        app_file = self.apps_file()
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

    def start(self, *, wayland_display: str | None = None, **kwargs):
        """Start Sunshine; ``wayland_display`` makes it see only that compositor.

        Game Window passes its private screen here: Sunshine then has no
        connection to the desktop's compositor at all.
        """
        if self.is_running():
            # A server already running sees the desktop: never report it as
            # the private Game Window server.
            if wayland_display:
                return False, _("Sunshine is already running.")
            return True, _("Sunshine is already running.")
        if not wayland_display:
            # A start without a screen during a Game Window share (a restart, a
            # password reset, the service dialog) would bring Sunshine up on
            # the desktop with KWin capture still configured: the private
            # desktop on the stream, labelled Game Window.
            live, private = game_window_screen()
            if live:
                if not private:
                    return False, _("Game Window is sharing, but its private screen was not found. Stop sharing, then start again.")
                wayland_display = private

        sc = sunshine_executable()
        if not sc:
            return False, _("Sunshine executable not found")
        try:
            config_file = self.config_dir / "sunshine.conf"
            # Prepare environment
            env = os.environ.copy()
            # vkBasalt is a game post-processing layer.  A session-wide
            # ENABLE_VKBASALT=1 also injects it into Sunshine's Vulkan encoder
            # probe and can crash the server.  This copy is server-only: games
            # launched by Big Remote Play retain the user's original setting.
            env["ENABLE_VKBASALT"] = "0"
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
            if wayland_display:
                env["WAYLAND_DISPLAY"] = wayland_display
                env.pop("WAYLAND_SOCKET", None)

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

            # Encoder/capture validation may fail a few seconds after exec.
            # Prefer API readiness, otherwise keep watching for a bounded time.
            exit_code = self._wait_for_startup()
            if exit_code is not None:
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

                self.log_file.write(_("Sunshine failed to start (exit code {code}).\n").format(code=exit_code))
                if error_detail:
                    _log.error("Sunshine failed to start: %s", error_detail)
                else:
                    _log.error("Sunshine failed to start (Exit code %s). Check logs.", exit_code)

                self.log_file.close()
                del self.log_file
                self.process = None
                self.pid = None
                return False, error_detail if error_detail else _("Exit code {code}").format(code=exit_code)

            # Save PID
            pid_file = self.config_dir / "sunshine.pid"
            with open(pid_file, "w") as f:
                f.write(str(self.pid))

            _log.info("Sunshine started (PID: %s)", self.pid)
            return True, None

        except Exception as e:
            _log.error("Error starting Sunshine: %s", e)
            return False, str(e)  # Return tuple (success, error_message)

    def stop(self) -> bool:
        """Stops the Sunshine server this object started.

        Kills by tracked process group first, then the PID file. Falls back to a
        name-*exact* pkill ONLY when we hold no handle on the instance, so we
        never take down an unrelated `sunshine` process the app didn't spawn.
        """
        if not self.is_running():
            _log.info("Sunshine is not running")
            return False

        killed_tracked = False
        try:
            if self.process:
                try:
                    pgid = os.getpgid(self.process.pid)
                    os.killpg(pgid, signal.SIGTERM)
                    try:
                        self.process.wait(timeout=STOP_GRACE_SECONDS)
                    except subprocess.TimeoutExpired:
                        os.killpg(pgid, signal.SIGKILL)
                        # Collect the exit status, or the process stays a zombie.
                        self.process.wait(timeout=5)
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

                # The PID must still be a Sunshine process: after Sunshine
                # exits, the kernel can hand the same PID to anything else.
                os.kill(pid, 0)
                if _is_live_sunshine(pid):
                    return True
                pid_file.unlink()

            except (OSError, ValueError):
                # Process does not exist, clear PID file
                pid_file.unlink()
                return False

        # Check via pgrep. It also lists exited processes nobody collected
        # (zombies), which must not block a new start.
        try:
            result = subprocess.run(["pgrep", "-x", "sunshine"], capture_output=True, text=True, timeout=5)
        except Exception:
            return False
        if result.returncode != 0:
            return False
        return any(_is_live_sunshine(int(pid)) for pid in (result.stdout or "").split() if pid.isdigit())

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
            previous_text = None
            if config_file.exists():
                try:
                    previous_text = config_file.read_text()
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

            settings = dict(settings)
            if "brp_stream_display" in settings:
                # Our prep command replaces only our own entry.
                from big_remote_play.host.stream_display import merge_prep_commands

                settings["global_prep_cmd"] = merge_prep_commands(current_config.get("global_prep_cmd"), settings.pop("brp_stream_display"))

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

            # Save merged config, keeping the previous version beside it.
            text = "".join(f"{key} = {value}\n" for key, value in current_config.items())
            if previous_text is not None and previous_text != text:
                secure_write_text(str(self.config_dir / "sunshine.conf.previous"), previous_text)
            secure_write_text(str(config_file), text)

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
            _log.error("Certificate trust error: %s", exc)
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
            _log.error("Sunshine API request failed: %s", exc)
            return 0, b""
        finally:
            conn.close()

    def pending_pairings(self, auth: tuple[str, str] | None = None) -> tuple[list[PendingPairing] | None, int]:
        """Clients waiting for a PIN, or ``None`` when Sunshine predates the list.

        Current Sunshine answers ``GET /api/pin`` with ``[{id, name, address}]``
        and requires the chosen ``id`` as ``pairing_id`` when the PIN is sent.
        """
        status, data = self._api_request("GET", "/api/pin", None, auth)
        if status != 200:
            return None, status
        try:
            payload = json.loads(data)
        except (ValueError, TypeError):
            return None, status
        items = payload.get("pairings", payload.get("pending")) if isinstance(payload, dict) else payload
        if not isinstance(items, list):
            return None, status
        pending = [
            PendingPairing(str(item.get("id")), str(item.get("name") or ""), str(item.get("address") or ""))
            for item in items
            if isinstance(item, dict) and _PAIRING_ID_RE.fullmatch(str(item.get("id") or ""))
        ]
        return pending, status

    def cancel_pairing(self, pairing_id: str, auth: tuple[str, str] | None = None) -> bool:
        """Drop one waiting pairing (DELETE /api/pin), as Sunshine's web panel does."""
        if not _PAIRING_ID_RE.fullmatch(pairing_id or ""):
            return False
        status, data = self._api_request("DELETE", "/api/pin", {"pairing_id": pairing_id}, auth)
        try:
            return status == 200 and json.loads(data or b"{}").get("status") is True
        except (ValueError, AttributeError):
            return False

    def discard_abandoned_pairings(self, auth: tuple[str, str] | None = None, *, ss: Callable[[list[str]], str] | None = None) -> int:
        """Cancel waiting pairings whose device is no longer waiting.

        Sunshine keeps a pairing request that the device abandoned (it was
        cancelled, the network dropped, a firewall cut it) until it restarts.
        Meanwhile the same Moonlight is refused with "A pairing session with
        this uniqueid already exists" and a PIN goes to the dead request. A
        device that is really waiting keeps its connection to the HTTP port
        open, so only requests without one are cancelled; if the connections
        cannot be read, nothing is.
        """
        from big_remote_play.host.sunshine_sessions import _ss, established_peers
        from big_remote_play.utils.connection_health import valid_address

        pending, status = self.pending_pairings(auth)
        if not pending or status != 200:
            return 0
        try:
            port = int(self._config_value("port", "47989"))
        except ValueError:
            port = 47989
        output = (ss or _ss)(["ss", "-tan"])
        if not output.strip():
            return 0
        waiting = established_peers(output, port)
        by_address: dict[str, list[PendingPairing]] = {}
        for item in pending:
            address = valid_address(item.address.split("%", 1)[0].removeprefix("::ffff:")) or item.address
            by_address.setdefault(address, []).append(item)
        cancelled = 0
        for address, items in by_address.items():
            # Sunshine lists requests oldest first; more requests than open
            # connections from one address means the oldest were abandoned.
            for item in items[: max(0, len(items) - waiting[address])]:
                if self.cancel_pairing(item.pairing_id, auth):
                    cancelled += 1
        if cancelled:
            _log.info("Cancelled %d abandoned pairing request(s).", cancelled)
        return cancelled

    def send_pin(self, pin: str, name: str | None = None, auth: tuple[str, str] | None = None, pairing_id: str | None = None) -> PinResult:
        """Sends a pairing PIN to Sunshine (POST /api/pin).

        Returns a PinResult; ``status`` is the HTTP status so callers can branch
        (401 = bad credentials, 307 = no admin user yet) without reading prose.
        With current Sunshine the PIN belongs to one pending request: it is
        matched automatically when only one client is waiting.
        """
        payload: dict[str, str] = {"pin": pin}
        if pairing_id is None:
            pending, status = self.pending_pairings(auth)
            if status in (0, 401, 307):
                return self._pin_failure(status)
            if pending is not None:
                if not pending:
                    return PinResult(False, PIN_NONE_WAITING, _("No computer is waiting to pair. Choose this computer under Connect on the other PC, then enter the code it shows."))
                if len(pending) > 1:
                    return PinResult(False, PIN_CHOOSE, _("Several computers are waiting to pair."), tuple(pending))
                pairing_id = pending[0].pairing_id
                name = name or pending[0].name
        if pairing_id is not None:
            if not _PAIRING_ID_RE.fullmatch(pairing_id):
                return PinResult(False, 400, _("Sunshine rejected the PIN"))
            payload["pairing_id"] = pairing_id
            # The current API requires a device name of 1–128 bytes.
            payload["name"] = (name or "Big Remote Play").encode("utf-8")[:128].decode("utf-8", "ignore") or "Big Remote Play"
        elif name:
            payload["name"] = name

        # Sunshine answers only after the device checked the PIN: measured with
        # Sunshine 2026.914, a wrong PIN is refused after about 10 seconds.
        status, data = self._api_request("POST", "/api/pin", payload, auth, timeout=PIN_ANSWER_TIMEOUT)
        if status == 200:
            try:
                reply = json.loads(data)
                accepted = isinstance(reply, dict) and reply.get("status") is True
            except (ValueError, TypeError):
                accepted = False
            if accepted:
                return PinResult(True, 200, _("PIN sent successfully"))
            return PinResult(False, 200, _("The PIN did not match. On the other computer, start pairing again and type the new PIN here."))
        return self._pin_failure(status)

    @staticmethod
    def _pin_failure(status: int) -> PinResult:
        if status == 0:
            return PinResult(False, 0, _("Connection error: Sunshine is unreachable or its certificate does not match"))
        if status == 401:
            return PinResult(False, 401, _("Authentication Failed. Configure a user in Sunshine."))
        # 307 means no admin user exists yet; the caller offers to create one.
        return PinResult(False, status, _("API error: {status}").format(status=status))

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
        return False, _("API error: {status}").format(status=status)

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
            return False, _("Username and password cannot be empty.")
        sc = sunshine_executable()
        if not sc:
            return False, _("Sunshine executable not found")
        env = os.environ.copy()
        try:
            # argv array, no shell; credentials are not logged.
            res = subprocess.run([sc, "--creds", new_username, new_password], capture_output=True, text=True, env=env, timeout=15)
            if res.returncode == 0:
                return True, _("Credentials reset successfully")
            detail = (res.stderr or res.stdout or "").strip()
            return False, _("Reset failed: {error}").format(error=detail[:200]) if detail else _("Reset failed")
        except Exception as exc:
            return False, _("Reset error: {error}").format(error=exc)

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

    def running_app_id(self, timeout: float = 2.0) -> int | None:
        """The app Sunshine streams now (0 = none), or ``None`` when unknown.

        Read from ``/serverinfo`` on the GameStream HTTP port, the value
        Moonlight checks: it refuses to pair while it is not 0 ("The computer
        is currently in a game"), even when nobody is connected any more.
        """
        try:
            port = int(self._config_value("port", "47989"))
        except ValueError:
            port = 47989
        conn = http.client.HTTPConnection(API_HOST, port, timeout=timeout)
        try:
            conn.request("GET", "/serverinfo", headers={"User-Agent": "BigRemotePlay"})
            response = conn.getresponse()
            body = response.read(64 * 1024).decode("utf-8", errors="replace")
        except (OSError, http.client.HTTPException):
            return None
        finally:
            conn.close()
        match = _CURRENT_GAME_RE.search(body)
        return int(match.group(1)) if response.status == 200 and match else None

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
