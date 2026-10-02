from __future__ import annotations

from collections.abc import Callable
from typing import Any, TextIO
import shutil
import subprocess
import secrets
import threading
import time

from big_remote_play.guest.moonlight_audio import MoonlightAudioReport
from big_remote_play.utils.moonlight_config import paired_host_certificate


# Logged by moonlight-common-c (never translated) once the video stream is
# set up after the RTSP handshake; an error window never prints them.
STREAM_STARTED_MARKERS = ("Starting video stream", "IDR frame request sent")


class MoonlightClient:
    def __init__(self, logger: Any | None = None) -> None:
        self.process: subprocess.Popen[str] | None = None
        self.connected_host: str | None = None
        self._pair_process: subprocess.Popen[str] | None = None
        self._pair_cancel = threading.Event()
        self._pair_lock = threading.Lock()
        self.logger = logger
        self.moonlight_cmd = next((c for c in ["moonlight-qt", "moonlight"] if shutil.which(c)), None)
        self.stream_confirmed = threading.Event()
        # Moonlight's sound messages for the current connection.
        self.audio = MoonlightAudioReport()

    def _prepare_ip(self, ip):
        """Prepares IP for Moonlight CLI."""
        if not ip:
            return ""
        clean_ip = ip.strip()

        # Remove brackets if present to facilitate processing
        was_bracketed = clean_ip.startswith("[") and clean_ip.endswith("]")
        if was_bracketed:
            clean_ip = clean_ip[1:-1]

        if ":" in clean_ip and "%" not in clean_ip and clean_ip.startswith("fe80"):
            try:
                # Get interface with default route
                route = subprocess.check_output(["ip", "-6", "route", "show", "default"], text=True, timeout=5).split()
                if "dev" in route:
                    dev_idx = route.index("dev") + 1
                    if dev_idx < len(route):
                        iface = route[dev_idx]
                        clean_ip = f"{clean_ip}%{iface}"
                else:
                    # Dumb fallback: get first UP interface that is not lo
                    try:
                        import json

                        out = subprocess.check_output(["ip", "-j", "addr"], text=True, timeout=5)
                        for i in json.loads(out):
                            if i["ifname"] != "lo" and "UP" in i["flags"]:
                                clean_ip = f"{clean_ip}%{i['ifname']}"
                                break
                    except Exception:
                        pass
            except Exception:
                pass

        return clean_ip

    def target_address(self, address: str, port: int = 47989) -> str:
        """Moonlight host argument; bracket IPv6 when appending a custom port."""
        clean = self._prepare_ip(address)
        if not clean or clean.startswith("-") or any(c.isspace() for c in clean):
            raise ValueError("Invalid host address")
        port = int(port)
        if not 1 <= port <= 65535:
            raise ValueError("Invalid host port")
        if port == 47989:
            return clean
        return f"[{clean}]:{port}" if ":" in clean else f"{clean}:{port}"

    def connect(self, ip: str, **kw: Any) -> bool:
        if not self.moonlight_cmd or self.is_connected():
            return False
        moonlight_cmd = self.moonlight_cmd

        try:
            target_ip = self.target_address(ip, kw.get("port", 47989))

            cmd = [moonlight_cmd, "stream", target_ip, "Desktop"]
            if kw.get("width") and kw.get("height") and kw.get("width") != "custom":
                cmd.extend(["--resolution", f"{kw['width']}x{kw['height']}"])
            if kw.get("fps") and kw.get("fps") != "custom":
                cmd.extend(["--fps", str(kw["fps"])])
            if kw.get("bitrate"):
                cmd.extend(["--bitrate", str(kw["bitrate"])])
            cmd.extend(["--display-mode", kw.get("display_mode", "borderless")])
            # Explicitly override the host-audio request, including False.
            cmd.append("--audio-on-host" if kw.get("play_audio_on_host", False) else "--no-audio-on-host")
            # Do not force --quit-after: that closes the remote application.
            # Respect Moonlight's saved advanced preference instead.
            if kw.get("hw_decode", True):
                cmd.extend(["--video-decoder", "auto"])
            else:
                cmd.extend(["--video-decoder", "software"])

            if self.logger:
                self.logger.info(f"Connecting to {ip} (target: {target_ip}) with options: {kw}")
                self.logger.info(f"Command: {' '.join(cmd)}")

            cancel_event = kw.get("cancel_event")
            if cancel_event is not None and cancel_event.is_set():
                return False
            # Output is always read: it is how a real stream is told apart from
            # a Moonlight window that only shows an error.
            self.stream_confirmed = threading.Event()
            self.audio = MoonlightAudioReport()
            self.process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace")
            if cancel_event is not None and cancel_event.is_set():
                self.disconnect()
                return False
            self.connected_host = ip

            logger = self.logger
            confirmed = self.stream_confirmed
            audio = self.audio

            def read_output(pipe: TextIO) -> None:
                for line in iter(pipe.readline, ""):
                    # A real text pipe yields only strings. Stop cleanly if a
                    # substituted process boundary violates that contract;
                    # otherwise a background exception can outlive the task.
                    if not isinstance(line, str):
                        break
                    if not confirmed.is_set() and any(marker in line for marker in STREAM_STARTED_MARKERS):
                        confirmed.set()
                    kind, keep = audio.note(line)
                    if logger and line.strip() and keep:
                        logger.info(f"[Moonlight]{' [AUDIO]' if kind else ''} {line.strip()}")
                pipe.close()

            if self.process.stdout:
                threading.Thread(target=read_output, args=(self.process.stdout,), daemon=True).start()

            try:
                exit_code = self.process.wait(timeout=1.0)
                msg = f"Moonlight ended prematurely (Code {exit_code})"
                if self.logger:
                    self.logger.error(msg)
                return False
            except subprocess.TimeoutExpired:
                pass

            return True
        except Exception as e:
            if self.logger:
                self.logger.error(f"Error connecting: {e}")
            return False

    def wait_for_stream(self, timeout: float = 30.0) -> bool:
        """Block until Moonlight reports that video is flowing.

        Returns False on timeout, or as soon as Moonlight exits without having
        started a stream (for example after an error dialog is closed).
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.stream_confirmed.wait(0.5):
                return self.is_connected()
            if not self.is_connected():
                return False
        return False

    def is_connected(self) -> bool:
        return bool(self.process and self.process.poll() is None)

    def disconnect(self):
        self._pair_cancel.set()
        pairing = self._pair_process
        if pairing is not None and pairing.poll() is None:
            try:
                pairing.terminate()
            except OSError:
                pass
        if not self.is_connected():
            return pairing is not None
        try:
            if self.process:
                self.process.terminate()
                self.process.wait(timeout=5)
            self.process = None
            self.connected_host = None
            return True
        except Exception:
            if self.process:
                self.process.kill()
                self.process = None
                self.connected_host = None
            return False

    def probe_host(self, host_ip: str) -> bool:
        if not self.moonlight_cmd:
            return False
        try:
            target_ip = self._prepare_ip(host_ip)
            # Aggressive timeout for probe
            res = subprocess.run([self.moonlight_cmd, "list", target_ip], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=1.5)
            return res.returncode == 0
        except Exception as e:
            if self.logger:
                self.logger.error(f"Probe error: {e}")
            return False

    def pair(self, host_ip: str, on_pin_callback: Callable[[str], None] | None = None, *, port: int = 47989, cancel_event: threading.Event | None = None) -> bool:
        """Use the upstream --pin option, without parsing localized output.

        Pairing owns a separate process: it must never be reported as streaming
        or overwrite a running stream. Timeout/cancellation cannot become success.
        """
        if not self.moonlight_cmd or not self._pair_lock.acquire(blocking=False):
            return False
        process = None
        try:
            self._pair_cancel.clear()
            if cancel_event is not None and cancel_event.is_set():
                return False
            pin = f"{secrets.randbelow(10000):04d}"
            command = [self.moonlight_cmd, "pair", self.target_address(host_ip, port), "--pin", pin]
            # Current Moonlight can finish pairing yet leave the process
            # running. The certificate it stores for the host appears (or
            # changes) only after the PIN was accepted, so that is the signal.
            certificate_before = paired_host_certificate(host_ip)
            process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True)
            self._pair_process = process
            if cancel_event is not None and cancel_event.is_set():
                process.terminate()
                return False
            if on_pin_callback:
                on_pin_callback(pin)
            deadline = time.monotonic() + 90
            next_check = time.monotonic() + 1.0
            while not self._pair_cancel.is_set() and not (cancel_event is not None and cancel_event.is_set()):
                if time.monotonic() >= deadline:
                    return False
                try:
                    return process.wait(timeout=0.25) == 0 and not self._pair_cancel.is_set() and not (cancel_event is not None and cancel_event.is_set())
                except subprocess.TimeoutExpired:
                    pass
                if time.monotonic() >= next_check:
                    next_check = time.monotonic() + 1.0
                    certificate = paired_host_certificate(host_ip)
                    if certificate and certificate != certificate_before:
                        return not self._pair_cancel.is_set() and not (cancel_event is not None and cancel_event.is_set())
            return False
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            if self.logger:
                self.logger.error(f"Pairing failed: {exc}")
            return False
        finally:
            try:
                if process is not None and process.poll() is None:
                    # A graceful stop lets Moonlight release its profile before
                    # the next command (list/stream) uses it.
                    process.terminate()
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
            except (OSError, subprocess.SubprocessError):
                pass
            finally:
                self._pair_process = None
                self._pair_lock.release()

    def list_apps(self, host_ip, *, port: int = 47989):
        if not self.moonlight_cmd:
            return []

        try:
            target_ip = self.target_address(host_ip, port)
            # Uses start_new_session=True instead of external setsid for better compatibility
            r = subprocess.run([self.moonlight_cmd, "list", target_ip], capture_output=True, text=True, timeout=5, start_new_session=True)

            if self.logger:
                self.logger.debug(f"List apps {host_ip} (target: {target_ip}) stdout: {r.stdout}")
                if r.stderr:
                    self.logger.error(f"List apps {host_ip} stderr: {r.stderr}")

            if r.returncode == 0:
                return [l.strip() for l in r.stdout.splitlines() if l.strip()]

            # Check for explicit pairing error in stderr
            err = (r.stderr or "").lower()
            if "not paired" in err or "não foi pareado" in err or "unpaired" in err:
                return None

            return []  # Other error (timeout, connection refused, etc)
        except Exception as e:
            if self.logger:
                self.logger.error(f"List apps error: {e}")
            return []

    def get_status(self):
        return {"connected": self.is_connected(), "host": self.connected_host, "moonlight_cmd": self.moonlight_cmd}
