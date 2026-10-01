"""Prepare the captured screen for one stream, then put it back.

Sunshine's KMS capture reads the pixels the GPU sends to the monitor. When
KDE Plasma drives that monitor in HDR, those pixels are PQ-encoded BT.2020;
Sunshine does not tone map them for a client that asked for SDR, so the
other computer sees a grey, washed-out picture (measured: white arrived as
140/255, pure red as (129, 74, 48)). Switching that one output to SDR for the
session makes the capture ordinary sRGB/BT.709 again (measured: every patch
within 2/255).

A stream at another resolution is also scaled: a 3440x1440 screen sent to a
1920x1080 client loses single-pixel detail (small text). Optionally the output
can use the client's exact mode for the session instead.

Sunshine runs this module as a ``global_prep_cmd`` before capture starts
(``prepare``) and after the session ends (``restore``), with the client's
request in ``SUNSHINE_CLIENT_HDR`` / ``_WIDTH`` / ``_HEIGHT`` / ``_FPS``.
Only ``kscreen-doctor`` (KDE Plasma) is used; elsewhere nothing changes. The
previous state is kept in a private runtime file so a crash can be undone.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
import json
import logging
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

_log = logging.getLogger("big-remoteplay")
_OUTPUT_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_MODE_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,32}$")
MARKER = "stream_display.py"


@dataclass(frozen=True)
class Mode:
    mode_id: str
    width: int
    height: int
    refresh: float


@dataclass(frozen=True)
class Output:
    name: str
    enabled: bool
    hdr: bool | None
    wcg: bool | None
    mode_id: str
    modes: tuple[Mode, ...] = ()

    @property
    def mode(self) -> Mode | None:
        return next((mode for mode in self.modes if mode.mode_id == self.mode_id), None)


@dataclass(frozen=True)
class Request:
    """What the other computer asked Sunshine for."""

    hdr: bool = False
    width: int = 0
    height: int = 0
    fps: int = 0

    @classmethod
    def from_environment(cls, env: dict[str, str]) -> "Request":
        def number(key: str) -> int:
            try:
                return max(0, int(env.get(key, "0")))
            except ValueError:
                return 0

        return cls(env.get("SUNSHINE_CLIENT_HDR", "").strip().lower() == "true", number("SUNSHINE_CLIENT_WIDTH"), number("SUNSHINE_CLIENT_HEIGHT"), number("SUNSHINE_CLIENT_FPS"))


@dataclass
class Plan:
    """kscreen-doctor arguments to apply, and those that undo them."""

    apply: list[str] = field(default_factory=list)
    undo: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def parse_outputs(payload: object) -> list[Output]:
    """``kscreen-doctor -j`` → outputs; malformed entries are ignored."""
    outputs = payload.get("outputs") if isinstance(payload, dict) else None
    found: list[Output] = []
    for item in outputs if isinstance(outputs, list) else []:
        if not isinstance(item, dict) or not _OUTPUT_RE.fullmatch(str(item.get("name") or "")):
            continue
        modes = []
        for mode in item.get("modes") or []:
            size = mode.get("size") if isinstance(mode, dict) else None
            if not isinstance(size, dict) or not _MODE_ID_RE.fullmatch(str(mode.get("id") or "")):
                continue
            try:
                modes.append(Mode(str(mode["id"]), int(size["width"]), int(size["height"]), float(mode.get("refreshRate") or 0)))
            except (KeyError, TypeError, ValueError):
                continue
        hdr, wcg = item.get("hdr"), item.get("wcg")
        found.append(
            Output(
                name=str(item["name"]),
                enabled=bool(item.get("enabled")),
                hdr=hdr if isinstance(hdr, bool) else None,
                wcg=wcg if isinstance(wcg, bool) else None,
                mode_id=str(item.get("currentModeId") or ""),
                modes=tuple(modes),
            )
        )
    return found


def best_mode(output: Output, width: int, height: int, fps: int) -> Mode | None:
    """The output's mode with exactly the client's size, closest in refresh.

    Only an exact size avoids scaling; any other size would still be scaled
    by Sunshine, so it is not worth changing the person's screen for.
    """
    same = [mode for mode in output.modes if mode.width == width and mode.height == height]
    if not same:
        return None
    wanted = fps or 60
    # Prefer a refresh at least as high as requested, then the closest one.
    return min(same, key=lambda mode: (mode.refresh + 0.5 < wanted, abs(mode.refresh - wanted)))


_SIZE_RE = re.compile(r"^(\d{3,5})x(\d{3,5})$")
CLIENT_RESOLUTION = "client"


def parse_resolution(value: str) -> tuple[int, int] | None:
    match = _SIZE_RE.fullmatch(value or "")
    return (int(match.group(1)), int(match.group(2))) if match else None


def plan_session(outputs: Sequence[Output], request: Request, *, target: str | None, sdr_for_sdr_clients: bool = True, resolution: str = "") -> Plan:
    """What to change for this session, as a pure decision.

    ``target`` is Sunshine's ``output_name``; without one, every enabled HDR
    output is switched, because the automatic choice is Sunshine's.

    ``resolution`` is the shared screen's size for the session: ``""`` keeps
    it, ``"client"`` uses the size the device asked for, ``"1920x1080"`` a
    fixed size (the best choice when TVs and phones connect at once: one
    screen mode serves every device, and 16:9 devices get it 1:1).
    """
    plan = Plan()
    chosen = [output for output in outputs if output.enabled and (target is None or output.name == target)]
    for output in chosen:
        # Always SDR, even when this first device asked for HDR: Sunshine runs
        # this once per app launch, and every device that joins later shares
        # the same capture. From an SDR screen Sunshine serves SDR devices
        # correctly and HDR requests as SDR in a BT.2020 container; from an
        # HDR screen only HDR devices look right (seen for real: an HDR TV
        # launched the session and three SDR devices got washed-out colours).
        if sdr_for_sdr_clients and output.hdr:
            plan.apply += [f"output.{output.name}.hdr.disable"]
            plan.undo += [f"output.{output.name}.hdr.enable"]
            if output.wcg:
                plan.apply += [f"output.{output.name}.wcg.disable"]
                plan.undo += [f"output.{output.name}.wcg.enable"]
            asked = "HDR" if request.hdr else "SDR"
            plan.notes.append(f"{output.name}: HDR on, first device asked for {asked} -> SDR for this session")
    size = (request.width, request.height) if resolution == CLIENT_RESOLUTION else parse_resolution(resolution)
    if size is not None and target is not None and all(size):
        output = next((item for item in chosen if item.name == target), None)
        current = output.mode if output is not None else None
        mode = best_mode(output, size[0], size[1], request.fps) if output is not None else None
        if output is not None and mode is not None and current is not None and mode.mode_id != current.mode_id:
            plan.apply.append(f"output.{output.name}.mode.{mode.mode_id}")
            plan.undo.insert(0, f"output.{output.name}.mode.{current.mode_id}")
            plan.notes.append(f"{output.name}: {current.width}x{current.height} -> {mode.width}x{mode.height}@{mode.refresh:.0f} for this session")
        elif output is not None and mode is None:
            plan.notes.append(f"{output.name}: no {size[0]}x{size[1]} mode; Sunshine scales the picture")
    return plan


def _runtime_dir() -> Path:
    runtime = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    return Path(runtime) / "big-remote-play"


def state_file() -> Path:
    return _runtime_dir() / "stream-display.json"


def client_file() -> Path:
    return _runtime_dir() / "stream-client.json"


@dataclass(frozen=True)
class ClientReport:
    """What the latest client asked for and how this computer's screen was.

    Written by ``prepare`` from Sunshine's environment, so it is the real
    request of the device that just connected (not a Big Remote Play setting).
    """

    width: int = 0
    height: int = 0
    fps: int = 0
    hdr: bool = False
    screen: str = ""
    screen_width: int = 0
    screen_height: int = 0
    screen_hdr: bool | None = None  # during the session, after any change
    started_at: float = 0.0

    @property
    def scaled(self) -> bool:
        return bool(self.width and self.screen_width) and (self.width, self.height) != (self.screen_width, self.screen_height)

    @property
    def hdr_sent_as_sdr(self) -> bool:
        return bool(self.screen_hdr) and not self.hdr

    def summary(self) -> str:
        """Stable technical words (not translated), for details and logs."""
        if not self.width:
            return ""
        text = f"requested {self.width}x{self.height}@{self.fps or '?'} {'HDR' if self.hdr else 'SDR'}"
        if self.screen_width:
            text += f" · screen {self.screen or '?'} {self.screen_width}x{self.screen_height}{' HDR' if self.screen_hdr else ''}"
        return text


def read_client_report(path: Path | None = None) -> ClientReport | None:
    try:
        payload = json.loads((path or client_file()).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    fields = ClientReport.__dataclass_fields__
    clean = {key: payload[key] for key in fields if key in payload and isinstance(payload[key], (int, float, str, bool, type(None)))}
    try:
        return ClientReport(**clean)
    except TypeError:
        return None


Runner = Callable[[list[str]], "subprocess.CompletedProcess[str]"]


def _run(argv: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, capture_output=True, text=True, timeout=20, check=False)


class StreamDisplay:
    def __init__(self, *, runner: Runner = _run, state: Path | None = None, tool: str | None = None) -> None:
        self._runner = runner
        self._state = state or state_file()
        self._tool = tool if tool is not None else (shutil.which("kscreen-doctor") or "")

    @property
    def available(self) -> bool:
        return bool(self._tool)

    def outputs(self) -> list[Output]:
        if not self.available:
            return []
        try:
            result = self._runner([self._tool, "-j"])
            return parse_outputs(json.loads(result.stdout)) if result.returncode == 0 else []
        except (OSError, subprocess.SubprocessError, ValueError):
            return []

    def prepare(self, request: Request, *, target: str | None, sdr_for_sdr_clients: bool, resolution: str = "", report: Path | None = None) -> Plan:
        # A session that ended without "restore" (Sunshine killed) is undone first.
        self.restore()
        outputs = self.outputs()
        plan = plan_session(outputs, request, target=target, sdr_for_sdr_clients=sdr_for_sdr_clients, resolution=resolution)
        for note in plan.notes:
            _log.info("Stream display: %s", note)
        applied = True
        if plan.apply:
            self._write_state(plan.undo)
            result = self._runner([self._tool, *plan.apply])
            applied = result.returncode == 0
            if not applied:
                _log.warning("Stream display: kscreen-doctor failed: %s", (result.stderr or result.stdout).strip()[:300])
        self._write_report(request, outputs, target, plan if applied else Plan(), report)
        return plan

    def _write_report(self, request: Request, outputs: Sequence[Output], target: str | None, plan: Plan, path: Path | None) -> None:
        """The client's request and the captured screen as it is during the session."""
        import time

        from big_remote_play.utils.secure_io import secure_write_text

        screen = next((output for output in outputs if output.name == target), None) if target else next((output for output in outputs if output.enabled), None)
        width = height = 0
        hdr = None
        if screen is not None:
            mode_id = next((arg.rsplit(".", 1)[1] for arg in plan.apply if arg.startswith(f"output.{screen.name}.mode.")), screen.mode_id)
            mode = next((item for item in screen.modes if item.mode_id == mode_id), None)
            width, height = (mode.width, mode.height) if mode else (0, 0)
            hdr = False if f"output.{screen.name}.hdr.disable" in plan.apply else screen.hdr
        report = ClientReport(request.width, request.height, request.fps, request.hdr, screen.name if screen else "", width, height, hdr, time.time())
        target_path = path or client_file()
        target_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        secure_write_text(str(target_path), json.dumps(report.__dict__))

    def restore(self) -> bool:
        """Put back what ``prepare`` changed; ``True`` when something was restored."""
        try:
            payload = json.loads(self._state.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return False
        except (OSError, ValueError):
            self._state.unlink(missing_ok=True)
            return False
        undo = [str(item) for item in payload.get("undo", []) if isinstance(item, str) and _valid_argument(item)] if isinstance(payload, dict) else []
        if undo and self.available:
            result = self._runner([self._tool, *undo])
            if result.returncode != 0:
                _log.warning("Stream display: restoring failed: %s", (result.stderr or result.stdout).strip()[:300])
                return False
        self._state.unlink(missing_ok=True)
        return bool(undo)

    def _write_state(self, undo: list[str]) -> None:
        from big_remote_play.utils.secure_io import secure_write_text

        self._state.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        secure_write_text(str(self._state), json.dumps({"version": 1, "undo": undo}))


def _valid_argument(value: str) -> bool:
    parts = value.split(".")
    return len(parts) in (3, 4) and parts[0] == "output" and _OUTPUT_RE.fullmatch(parts[1]) is not None and all(_MODE_ID_RE.fullmatch(part) for part in parts[2:])


def prep_command(*, target: str | None, sdr_for_sdr_clients: bool, resolution: str = "") -> dict[str, object] | None:
    """The ``global_prep_cmd`` entry for Sunshine (``None`` inside Flatpak).

    It is added even with both options off: it then changes nothing and only
    records the client's request for the connection details. The command runs
    this file with the same Python, so it works from the native package and
    from a source checkout alike.
    """
    if Path("/.flatpak-info").exists():
        # Sunshine runs on the host, where this sandbox's Python and files do
        # not exist; a failing "do" command would make Sunshine refuse the stream.
        return None
    script = Path(__file__).resolve()
    flags = []
    if target:
        if _OUTPUT_RE.fullmatch(target) is None:
            return None
        flags += ["--output", target]
    if sdr_for_sdr_clients:
        flags.append("--sdr")
    if resolution and target and (resolution == CLIENT_RESOLUTION or parse_resolution(resolution)):
        flags += ["--resolution", resolution]
    base = f'"{sys.executable}" -I "{script}"'
    return {"do": f"{base} prepare {' '.join(flags)}", "undo": f"{base} restore", "elevated": False}


def merge_prep_commands(existing: str | None, entry: dict[str, object] | None) -> str | None:
    """Sunshine's ``global_prep_cmd`` with our entry replaced, others kept.

    ``None`` means the option can be removed (nothing left). An unreadable
    value is kept untouched: it is the person's configuration.
    """
    commands: list = []
    if existing:
        try:
            commands = json.loads(existing)
        except ValueError:
            return existing
        if not isinstance(commands, list):
            return existing
    commands = [item for item in commands if not (isinstance(item, dict) and MARKER in str(item.get("do", "")))]
    if entry is not None:
        commands.append(entry)
    return json.dumps(commands, separators=(",", ":")) if commands else None


def main(argv: Sequence[str] | None = None) -> int:
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(prog="stream_display")
    parser.add_argument("action", choices=("prepare", "restore"))
    parser.add_argument("--output")
    parser.add_argument("--sdr", action="store_true")
    parser.add_argument("--resolution", default="")
    args = parser.parse_args(argv)
    # Sunshine refuses to start a stream whose "do" command fails, so this
    # never fails: a screen that could not be adjusted still streams.
    try:
        display = StreamDisplay()
        if args.action == "restore":
            display.restore()
        else:
            target = args.output if args.output and _OUTPUT_RE.fullmatch(args.output) else None
            resolution = args.resolution if args.resolution == CLIENT_RESOLUTION or parse_resolution(args.resolution) else ""
            display.prepare(Request.from_environment(dict(os.environ)), target=target, sdr_for_sdr_clients=args.sdr, resolution=resolution)
    except Exception as error:
        _log.warning("Stream display: %s", error)
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    sys.exit(main())
