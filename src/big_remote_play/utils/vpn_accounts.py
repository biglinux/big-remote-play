"""Manage the VPN accounts and network memberships available on this PC.

The VPN clients remain the source of truth:

* Tailscale/Headscale profiles come from ``tailscale switch --list --json``.
  Older clients fall back to the current ``tailscale status --json`` profile.
* ZeroTier memberships come from ``zerotier-cli -j listnetworks``.
* Big Remote Play stores only optional friendly names and provider metadata.

No reusable credential is written here. Authentication keys continue to use the
system keyring through :mod:`big_remote_play.utils.secret_store`.
"""

from __future__ import annotations

from dataclasses import dataclass
import getpass
import json
import os
from pathlib import Path
import re
import subprocess
from typing import Any, Callable, Sequence
import urllib.parse

from big_remote_play import paths
from big_remote_play.utils.secure_io import secure_write_text
from big_remote_play.utils.system_check import SystemCheck

_PROFILE_ID_RE = re.compile(r"^[A-Za-z0-9._:@+-]{1,256}$")
_ZEROTIER_NETWORK_ID_RE = re.compile(r"^[0-9a-fA-F]{16}$")
_AUTH_URL_RE = re.compile(r"https://[^\s\"'<>]+")
_PERMISSION_MARKERS = (
    "permission denied",
    "access denied",
    "authtoken.secret",
    "not authorized",
    "401",
)

_METADATA_FILE = paths.CONFIG_DIR / "private_network" / "accounts.json"


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


@dataclass(frozen=True)
class TailscaleProfile:
    profile_id: str
    account: str
    tailnet: str
    nickname: str = ""
    selected: bool = False
    provider: str = "tailscale"
    login_server: str = ""
    friendly_name: str = ""
    removable: bool = True

    @property
    def display_name(self) -> str:
        return self.friendly_name or self.nickname or self.tailnet or self.account or self.profile_id


@dataclass(frozen=True)
class TailscaleProfiles:
    profiles: tuple[TailscaleProfile, ...]
    switching_supported: bool
    error: str = ""

    @property
    def selected(self) -> TailscaleProfile | None:
        return next((profile for profile in self.profiles if profile.selected), None)


@dataclass(frozen=True)
class ZeroTierNetwork:
    network_id: str
    name: str
    status: str
    assigned_addresses: tuple[str, ...] = ()
    device: str = ""
    network_type: str = ""
    friendly_name: str = ""

    @property
    def display_name(self) -> str:
        return self.friendly_name or self.name or self.network_id

    @property
    def ready(self) -> bool:
        return self.status.upper() == "OK"

    @property
    def awaiting_authorization(self) -> bool:
        return self.status.upper() in {"ACCESS_DENIED", "REQUESTING_CONFIGURATION", "NOT_FOUND"}


@dataclass(frozen=True)
class ZeroTierNetworks:
    networks: tuple[ZeroTierNetwork, ...]
    needs_privilege: bool = False
    error: str = ""


@dataclass(frozen=True)
class TailscaleConnection:
    """Outcome of a connection attempt, judged by the daemon's own state."""

    connected: bool
    backend_state: str = ""
    auth_url: str = ""
    detail: str = ""

    @property
    def awaiting_authentication(self) -> bool:
        """An auth URL was shown but the sign-in was never completed."""
        return not self.connected and bool(self.auth_url)


def _default_runner(argv: Sequence[str], *, timeout: float = 15.0) -> CommandResult:
    result = subprocess.run(list(argv), capture_output=True, text=True, timeout=timeout, check=False)
    return CommandResult(result.returncode, result.stdout or "", result.stderr or "")


def extract_auth_url(line: str) -> str:
    """Return the sign-in URL printed by ``tailscale up``, if the line has one."""
    match = _AUTH_URL_RE.search(line)
    return match.group(0).rstrip(".,;)") if match else ""


def normalize_login_server(value: str) -> str:
    """Validate a Headscale/Tailscale control server before it reaches argv.

    Accepts a bare host or a full URL and always returns an https origin.
    Anything else raises: a malformed server silently falling back to
    tailscale.com would join the wrong tailnet.
    """
    text = value.strip()
    if not text:
        return ""
    candidate = text if "://" in text else f"https://{text}"
    parsed = urllib.parse.urlsplit(candidate)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or any(character.isspace() for character in candidate):
        raise ValueError(f"invalid login server: {value}")
    return urllib.parse.urlunsplit(("https", parsed.netloc, parsed.path.rstrip("/"), "", ""))


def tailscale_connect_argv(tailscale_cmd: Sequence[str], *, login_server: str = "", auth_key_path: str = "", timeout: float = 300.0, add_account: bool = False) -> list[str]:
    """Argv that joins a tailnet: ``up`` normally, ``login`` for a new account.

    ``up`` is what connects — ``login`` alone authenticates and can leave the
    node stopped, which is how a PC ended up signed in yet unreachable. But
    ``up`` on an already-connected node does not offer a second sign-in, so
    adding another account uses ``login`` (fast user switching) and is brought
    up afterwards. ``--timeout`` bounds the wait for the browser sign-in inside
    the CLI, so no watchdog is needed.
    """
    argv = [*tailscale_cmd, "login" if add_account else "up", f"--timeout={int(timeout)}s"]
    if login_server:
        argv.append(f"--login-server={login_server}")
    if auth_key_path:
        # The key travels as a file reference: argv is world-readable in /proc.
        argv.append(f"--auth-key=file:{auth_key_path}")
    return argv


def _load_metadata(path: Path = _METADATA_FILE) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {"version": 1, "tailscale_profiles": {}, "zerotier_networks": {}}
    if not isinstance(payload, dict):
        return {"version": 1, "tailscale_profiles": {}, "zerotier_networks": {}}
    payload.setdefault("version", 1)
    for key in ("tailscale_profiles", "zerotier_networks"):
        if not isinstance(payload.get(key), dict):
            payload[key] = {}
    return payload


def _save_metadata(payload: dict[str, Any], path: Path = _METADATA_FILE) -> None:
    secure_write_text(str(path), json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


class VPNAccountManager:
    """Thin, testable wrapper around the installed Tailscale and ZeroTier CLIs."""

    def __init__(
        self,
        system_check: SystemCheck | None = None,
        *,
        runner: Callable[..., CommandResult] | None = None,
        metadata_file: Path | None = None,
        popen: Callable[..., Any] | None = None,
    ) -> None:
        self.system_check = system_check or SystemCheck()
        self._runner = runner or _default_runner
        self.metadata_file = metadata_file or _METADATA_FILE
        # ``tailscale up`` prints the sign-in URL long before it exits, so it is
        # streamed instead of captured like the other short-lived commands.
        self._popen = popen or subprocess.Popen

    def _run(self, argv: Sequence[str], *, timeout: float = 15.0) -> CommandResult:
        try:
            result = self._runner(list(argv), timeout=timeout)
        except (OSError, subprocess.SubprocessError) as error:
            return CommandResult(127, "", str(error))
        if isinstance(result, CommandResult):
            return result
        return CommandResult(int(getattr(result, "returncode", 1)), str(getattr(result, "stdout", "") or ""), str(getattr(result, "stderr", "") or ""))

    def _metadata(self) -> dict[str, Any]:
        return _load_metadata(self.metadata_file)

    def _save_metadata(self, payload: dict[str, Any]) -> None:
        _save_metadata(payload, self.metadata_file)

    # ── Local labels/metadata ──────────────────────────────────────────────

    def set_tailscale_metadata(
        self,
        profile_id: str,
        *,
        friendly_name: str | None = None,
        provider: str | None = None,
        login_server: str | None = None,
    ) -> None:
        if not profile_id:
            return
        payload = self._metadata()
        entries = payload["tailscale_profiles"]
        current = dict(entries.get(profile_id, {}) or {})
        if friendly_name is not None:
            value = friendly_name.strip()
            if value:
                current["friendly_name"] = value
            else:
                current.pop("friendly_name", None)
        if provider in {"tailscale", "headscale"}:
            current["provider"] = provider
        if login_server is not None:
            value = login_server.strip()
            if value:
                current["login_server"] = value
            else:
                current.pop("login_server", None)
        if current:
            entries[profile_id] = current
        else:
            entries.pop(profile_id, None)
        self._save_metadata(payload)

    def remove_tailscale_metadata(self, profile_id: str) -> None:
        payload = self._metadata()
        payload["tailscale_profiles"].pop(profile_id, None)
        self._save_metadata(payload)

    def set_zerotier_name(self, network_id: str, friendly_name: str) -> None:
        if _ZEROTIER_NETWORK_ID_RE.fullmatch(network_id) is None:
            return
        payload = self._metadata()
        entries = payload["zerotier_networks"]
        value = friendly_name.strip()
        if value:
            entries[network_id.lower()] = {"friendly_name": value}
        else:
            entries.pop(network_id.lower(), None)
        self._save_metadata(payload)

    def remove_zerotier_metadata(self, network_id: str) -> None:
        payload = self._metadata()
        payload["zerotier_networks"].pop(network_id.lower(), None)
        self._save_metadata(payload)

    # ── Tailscale / Headscale ──────────────────────────────────────────────

    def list_tailscale_profiles(self) -> TailscaleProfiles:
        metadata = self._metadata()["tailscale_profiles"]
        command = [*self.system_check.tailscale_cmd(), "switch", "--list", "--json"]
        result = self._run(command, timeout=15)
        if result.returncode == 0:
            try:
                payload = json.loads(result.stdout)
            except (TypeError, ValueError):
                payload = None
            if isinstance(payload, list):
                profiles: list[TailscaleProfile] = []
                for item in payload:
                    if not isinstance(item, dict):
                        continue
                    profile_id = str(item.get("id") or "").strip()
                    if not profile_id:
                        continue
                    local = metadata.get(profile_id, {}) if isinstance(metadata.get(profile_id), dict) else {}
                    profiles.append(
                        TailscaleProfile(
                            profile_id=profile_id,
                            account=str(item.get("account") or ""),
                            tailnet=str(item.get("tailnet") or ""),
                            nickname=str(item.get("nickname") or ""),
                            selected=bool(item.get("selected")),
                            provider=str(local.get("provider") or "tailscale"),
                            login_server=str(local.get("login_server") or ""),
                            friendly_name=str(local.get("friendly_name") or ""),
                            removable=not bool(item.get("selected")),
                        )
                    )
                return TailscaleProfiles(tuple(profiles), switching_supported=True)

        # ``tailscale switch`` is still marked alpha upstream. Older clients can
        # expose only the current profile through status JSON; keep that usable.
        fallback = self._run([*self.system_check.tailscale_cmd(), "status", "--json"], timeout=15)
        if fallback.returncode != 0:
            detail = (result.stderr or result.stdout or fallback.stderr or fallback.stdout).strip()
            return TailscaleProfiles((), switching_supported=False, error=detail)
        try:
            status = json.loads(fallback.stdout)
        except (TypeError, ValueError):
            return TailscaleProfiles((), switching_supported=False, error="invalid status JSON")
        if not isinstance(status, dict) or status.get("BackendState") not in {"Running", "Starting", "Stopped"}:
            return TailscaleProfiles((), switching_supported=False)

        raw_tailnet = status.get("CurrentTailnet")
        tailnet_data = raw_tailnet if isinstance(raw_tailnet, dict) else {}
        tailnet = str(tailnet_data.get("Name") or status.get("MagicDNSSuffix") or "")
        raw_self = status.get("Self")
        self_node = raw_self if isinstance(raw_self, dict) else {}
        user_id = str(self_node.get("UserID") or "")
        raw_users = status.get("User")
        user_map = raw_users if isinstance(raw_users, dict) else {}
        numeric_user_id: object = int(user_id) if user_id.isdigit() else user_id
        user = user_map.get(user_id) or user_map.get(numeric_user_id)
        user = user if isinstance(user, dict) else {}
        account = str(user.get("LoginName") or user.get("DisplayName") or "")
        profile_id = "current"
        local = metadata.get(profile_id, {}) if isinstance(metadata.get(profile_id), dict) else {}
        profile = TailscaleProfile(
            profile_id=profile_id,
            account=account,
            tailnet=tailnet,
            nickname=str(self_node.get("HostName") or ""),
            selected=status.get("BackendState") in {"Running", "Starting"},
            provider=str(local.get("provider") or "tailscale"),
            login_server=str(local.get("login_server") or ""),
            friendly_name=str(local.get("friendly_name") or ""),
            removable=False,
        )
        return TailscaleProfiles((profile,), switching_supported=False)

    def switch_tailscale_profile(self, profile_id: str) -> CommandResult:
        if profile_id.startswith("-") or _PROFILE_ID_RE.fullmatch(profile_id) is None:
            return CommandResult(2, "", "invalid profile id")
        return self._run([*self.system_check.tailscale_cmd(), "switch", profile_id], timeout=45)

    def remove_tailscale_profile(self, profile_id: str) -> CommandResult:
        if profile_id.startswith("-") or _PROFILE_ID_RE.fullmatch(profile_id) is None or profile_id == "current":
            return CommandResult(2, "", "invalid profile id")
        result = self._run([*self.system_check.tailscale_cmd(), "switch", "remove", profile_id], timeout=45)
        if result.returncode == 0:
            self.remove_tailscale_metadata(profile_id)
        return result

    def pause_tailscale(self) -> CommandResult:
        """Temporarily disconnect while keeping every saved account."""
        result = self._run([*self.system_check.tailscale_cmd(), "down"], timeout=30)
        if result.returncode != 0 and self._permission_error(result):
            # Only PCs where the operator was never set still need a password.
            return self._run(["pkexec", "/usr/bin/tailscale", "down"], timeout=60)
        return result

    def logout_tailscale(self) -> CommandResult:
        """Expire the current node key; a later connection requires login."""
        return self._run([*self.system_check.tailscale_cmd(), "logout"], timeout=45)

    def tailscale_backend_state(self) -> str:
        """The daemon's own verdict: ``Running`` only when actually connected."""
        result = self._run([*self.system_check.tailscale_cmd(), "status", "--json"], timeout=15)
        if result.returncode != 0:
            return ""
        try:
            status = json.loads(result.stdout)
        except (TypeError, ValueError):
            return ""
        return str(status.get("BackendState") or "") if isinstance(status, dict) else ""

    def connect_tailscale(
        self,
        *,
        login_server: str = "",
        auth_key: str = "",
        on_auth_url: Callable[[str], None] | None = None,
        on_output: Callable[[str], None] | None = None,
        timeout: float = 300.0,
        add_account: bool = False,
    ) -> TailscaleConnection:
        """Join the tailnet and report whether the daemon really connected.

        The sign-in URL is handed to ``on_auth_url`` so the caller opens it in
        the user's session: the CLI may run privileged, and a browser started
        from a root process never reaches the desktop. With ``add_account`` the
        sign-in is for an additional profile on a PC that is already connected.
        """
        try:
            server = normalize_login_server(login_server)
        except ValueError as error:
            return TailscaleConnection(False, detail=str(error))

        self._ensure_tailscaled(on_output)
        key_path = self._write_auth_key(auth_key)
        try:
            argv = tailscale_connect_argv(self.system_check.tailscale_cmd(), login_server=server, auth_key_path=key_path, timeout=timeout, add_account=add_account)
            output, auth_url = self._stream(argv, on_auth_url=on_auth_url, on_output=on_output)
            if self._permission_error(CommandResult(1, output)):
                # Documented remedy: make this user the tailscaled operator once,
                # instead of running every future command through pkexec.
                operator = self._run(["pkexec", "/usr/bin/tailscale", "set", f"--operator={getpass.getuser()}"], timeout=120)
                if operator.returncode == 0:
                    retry_output, retry_url = self._stream(argv, on_auth_url=on_auth_url, on_output=on_output)
                    output, auth_url = retry_output, auth_url or retry_url
                elif on_output is not None:
                    on_output((operator.stderr or operator.stdout).strip())
        finally:
            if key_path:
                try:
                    os.unlink(key_path)
                except OSError:
                    pass

        state = self.tailscale_backend_state()
        if add_account and state not in {"Running", "NeedsLogin"}:
            # `login` authenticates the new profile; `up` is still what connects.
            output += "\n" + self._stream(tailscale_connect_argv(self.system_check.tailscale_cmd(), timeout=timeout), on_auth_url=on_auth_url, on_output=on_output)[0]
            state = self.tailscale_backend_state()
        return TailscaleConnection(state == "Running", backend_state=state, auth_url=auth_url, detail=output.strip()[-500:])

    def _ensure_tailscaled(self, on_output: Callable[[str], None] | None) -> None:
        """Start the daemon only when it is not already running."""
        if self._run(["systemctl", "is-active", "--quiet", "tailscaled"], timeout=15).returncode == 0:
            return
        result = self._run(["pkexec", "/usr/bin/systemctl", "enable", "--now", "tailscaled"], timeout=120)
        if result.returncode != 0 and on_output is not None:
            on_output((result.stderr or result.stdout).strip())

    def _write_auth_key(self, auth_key: str) -> str:
        key = auth_key.strip()
        if not key:
            return ""
        runtime_dir = os.environ.get("XDG_RUNTIME_DIR") or str(paths.CONFIG_DIR)
        path = os.path.join(runtime_dir, "big-remote-play", "tailscale-auth-key")
        secure_write_text(path, key)
        return path

    def _stream(self, argv: Sequence[str], *, on_auth_url: Callable[[str], None] | None, on_output: Callable[[str], None] | None) -> tuple[str, str]:
        """Run a command, forwarding each line and the first sign-in URL."""
        try:
            process = self._popen(list(argv), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        except (OSError, subprocess.SubprocessError) as error:
            return str(error), ""
        collected: list[str] = []
        auth_url = ""
        stream = getattr(process, "stdout", None)
        if stream is not None:
            for raw in stream:
                line = raw.rstrip("\n")
                collected.append(line)
                if on_output is not None and line.strip():
                    on_output(line.strip())
                if not auth_url:
                    auth_url = extract_auth_url(line)
                    if auth_url and on_auth_url is not None:
                        on_auth_url(auth_url)
        process.wait()
        return "\n".join(collected), auth_url

    # ── ZeroTier ───────────────────────────────────────────────────────────

    def _zerotier_cmd(self) -> list[str]:
        command = getattr(self.system_check, "zerotier_cmd", None)
        if callable(command):
            raw_command = command()
            if isinstance(raw_command, (list, tuple)):
                return [str(part) for part in raw_command]
        return ["zerotier-cli"]

    @staticmethod
    def _permission_error(result: CommandResult) -> bool:
        detail = f"{result.stdout}\n{result.stderr}".lower()
        return any(marker in detail for marker in _PERMISSION_MARKERS)

    def _run_zerotier(self, args: Sequence[str], *, timeout: float = 20, allow_privileged: bool = False) -> tuple[CommandResult, bool]:
        command = [*self._zerotier_cmd(), *args]
        result = self._run(command, timeout=timeout)
        if result.returncode == 0 or not self._permission_error(result):
            return result, False
        if not allow_privileged or command[:2] == ["flatpak", "run"]:
            return result, True
        privileged = self._run(["pkexec", *command], timeout=max(timeout, 45))
        return privileged, self._permission_error(privileged)

    def list_zerotier_networks(self, *, allow_privileged: bool = False) -> ZeroTierNetworks:
        result, needs_privilege = self._run_zerotier(["-j", "listnetworks"], timeout=20, allow_privileged=allow_privileged)
        if result.returncode != 0:
            return ZeroTierNetworks((), needs_privilege=needs_privilege, error=(result.stderr or result.stdout).strip())
        try:
            payload = json.loads(result.stdout)
        except (TypeError, ValueError):
            return ZeroTierNetworks((), error="invalid listnetworks JSON")
        if not isinstance(payload, list):
            return ZeroTierNetworks((), error="invalid listnetworks JSON")

        metadata = self._metadata()["zerotier_networks"]
        networks: list[ZeroTierNetwork] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            network_id = str(item.get("nwid") or item.get("id") or "").lower()
            if _ZEROTIER_NETWORK_ID_RE.fullmatch(network_id) is None:
                continue
            raw_addresses = item.get("assignedAddresses")
            addresses = raw_addresses if isinstance(raw_addresses, list) else []
            local = metadata.get(network_id, {}) if isinstance(metadata.get(network_id), dict) else {}
            networks.append(
                ZeroTierNetwork(
                    network_id=network_id,
                    name=str(item.get("name") or ""),
                    status=str(item.get("status") or "UNKNOWN"),
                    assigned_addresses=tuple(str(value) for value in addresses),
                    device=str(item.get("portDeviceName") or ""),
                    network_type=str(item.get("type") or ""),
                    friendly_name=str(local.get("friendly_name") or ""),
                )
            )
        return ZeroTierNetworks(tuple(networks))

    def join_zerotier_network(self, network_id: str, *, allow_privileged: bool = True) -> CommandResult:
        value = network_id.strip().lower()
        if _ZEROTIER_NETWORK_ID_RE.fullmatch(value) is None:
            return CommandResult(2, "", "invalid network id")
        result, _needs_privilege = self._run_zerotier(["join", value], timeout=45, allow_privileged=allow_privileged)
        return result

    def leave_zerotier_network(self, network_id: str, *, allow_privileged: bool = True) -> CommandResult:
        value = network_id.strip().lower()
        if _ZEROTIER_NETWORK_ID_RE.fullmatch(value) is None:
            return CommandResult(2, "", "invalid network id")
        result, _needs_privilege = self._run_zerotier(["leave", value], timeout=45, allow_privileged=allow_privileged)
        if result.returncode == 0:
            self.remove_zerotier_metadata(value)
        return result


def metadata_snapshot(path: Path = _METADATA_FILE) -> dict[str, Any]:
    """Read-only helper used by release diagnostics and tests."""
    payload = _load_metadata(path)
    return json.loads(json.dumps(payload))
