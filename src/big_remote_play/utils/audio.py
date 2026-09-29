"""Sunshine audio on PipeWire/PulseAudio: discover, verify and own only what we add.

Contract (see docs/audio-architecture.md):

- Sunshine records the *monitor* of an output — the sound this computer plays —
  never a microphone. A source counts as a monitor only when the server itself
  says so ("Monitor of Sink"), never because its name ends in ``.monitor``.
- Automatic mode writes nothing to the sound server: Sunshine records the
  monitor of whatever output is current when a client connects.
- The only thing Big Remote Play ever adds is a "bridge": direct PipeWire port
  links from a Sunshine virtual output's monitor into a device. Port links are
  not streams, so effect programs that capture every new stream (EasyEffects,
  JamesDSP) cannot pull them into a feedback loop. Application streams are
  never moved, so per-process capture by other programs (Steam Remote Play,
  OBS, recorders) keeps working.
- Object ids are never persisted except the ids of links this app created,
  always re-verified against their exact ports before removal.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field, replace
import array
import json
import logging
import math
import os
from pathlib import Path
import re
import secrets
import subprocess
import threading
import time

_log = logging.getLogger("big-remoteplay")

Runner = Callable[..., subprocess.CompletedProcess]

SUNSHINE_SINK_PREFIX = "sink-sunshine-"
SUNSHINE_STEREO_SINK = "sink-sunshine-stereo"
# Null sinks created by Big Remote Play 2.x and earlier. They are no longer
# created; leftovers from a crash of an old version are removed on recovery.
LEGACY_SINKS = frozenset({"SunshineGameSink", "SunshineStereo", "SunshineHybrid"})
OWNER_PROPERTY = "big-remote-play.owner"
_COMMAND_TIMEOUT = 5

# Output kinds. Only "hardware" and "bluetooth" can be chosen explicitly (see
# manual_outputs); virtual outputs are captured through Automatic.
HARDWARE, BLUETOOTH, VIRTUAL, SUNSHINE, STEAM, LEGACY, OWNED = "hardware", "bluetooth", "virtual", "sunshine", "steam", "legacy", "owned"
_REAL_KINDS = frozenset({HARDWARE, BLUETOOTH, VIRTUAL})


# --------------------------------------------------------------------------
# Parsing `pactl list` (LC_ALL=C). The text form is used instead of
# `pactl -f json` because pactl 17 drops non-ASCII descriptions from JSON.
# --------------------------------------------------------------------------

_HEADER = re.compile(r"^(Sink|Source|Sink Input|Source Output) #(\d+)\s*$")
_PROPERTY = re.compile(r'^([A-Za-z0-9_.\-]+) = "(.*)"$')


def parse_pactl_list(text: str) -> list[dict]:
    """``[{"kind", "index", "fields": {...}, "properties": {...}}]``."""
    items: list[dict] = []
    current: dict | None = None
    in_properties = False
    for raw in (text or "").splitlines():
        header = _HEADER.match(raw)
        if header:
            current = {"kind": header.group(1), "index": header.group(2), "fields": {}, "properties": {}}
            items.append(current)
            in_properties = False
            continue
        if current is None or not raw.strip():
            continue
        line = raw.strip()
        indent = len(raw) - len(raw.lstrip("\t "))
        if in_properties and indent >= 2:
            match = _PROPERTY.match(line)
            if match:
                current["properties"][match.group(1)] = match.group(2).replace('\\"', '"')
            continue
        in_properties = False
        if line == "Properties:":
            in_properties = True
            continue
        if ":" in line:
            key, value = line.split(":", 1)
            current["fields"].setdefault(key.strip(), value.strip())
    return items


@dataclass(frozen=True)
class AudioOutput:
    """A playback device or virtual output (a PulseAudio "sink")."""

    index: str
    name: str
    description: str
    monitor: str  # as announced by the sink; verified by AudioGraph.monitor_of
    state: str
    kind: str

    @property
    def is_real(self) -> bool:
        """An output the person chose or could choose, not one made by Sunshine, Steam or us."""
        return self.kind in _REAL_KINDS


@dataclass(frozen=True)
class AudioSource:
    index: str
    name: str
    description: str
    monitor_of: str  # sink name for a monitor, "" for a microphone or line-in

    @property
    def is_monitor(self) -> bool:
        return bool(self.monitor_of)


@dataclass(frozen=True)
class AudioStream:
    """An application stream: playback into an output or capture from a source."""

    index: str
    device: str  # output name (playback) or source name (capture)
    app: str
    binary: str
    pid: str
    media_name: str
    node_name: str
    properties: Mapping[str, str] = field(default_factory=dict, compare=False, repr=False)


@dataclass(frozen=True)
class AudioModule:
    index: str
    name: str
    args: str


def classify_output(name: str, description: str, properties: Mapping[str, str], flags: str = "") -> str:
    if name in LEGACY_SINKS:
        return LEGACY
    if name.startswith(SUNSHINE_SINK_PREFIX):
        return SUNSHINE
    if OWNER_PROPERTY in properties:
        return OWNED
    if "steam.autounload" in properties or "steam streaming" in f"{name} {description}".lower():
        return STEAM
    api = properties.get("device.api", "")
    if api == "bluez5" or properties.get("device.bus") == "bluetooth":
        return BLUETOOTH
    if api == "alsa" or "HARDWARE" in flags.split():
        return HARDWARE
    # EasyEffects, JamesDSP, combined and network outputs: legitimate, but
    # they usually forward to the default output themselves.
    return VIRTUAL


def _is_sunshine_process(stream: AudioStream) -> bool:
    return stream.binary == "sunshine" or (stream.app.lower() == "sunshine" and stream.media_name == "sunshine-record")


def _is_steam_process(stream: AudioStream) -> bool:
    return stream.binary in {"steam", "steamwebhelper"} or stream.app.lower().startswith("steam")


@dataclass(frozen=True)
class AudioGraph:
    """One consistent reading of the sound server."""

    outputs: tuple[AudioOutput, ...] = ()
    sources: tuple[AudioSource, ...] = ()
    playback: tuple[AudioStream, ...] = ()
    capture: tuple[AudioStream, ...] = ()
    modules: tuple[AudioModule, ...] = ()
    default_sink: str = ""
    default_source: str = ""

    def output(self, name: str) -> AudioOutput | None:
        return next((o for o in self.outputs if o.name == name), None)

    def source(self, name: str) -> AudioSource | None:
        return next((s for s in self.sources if s.name == name), None)

    def monitor_of(self, output_name: str) -> str | None:
        """The verified monitor of ``output_name``; ``None`` rather than a guess."""
        output = self.output(output_name)
        if output is None:
            return None
        candidates = [output.monitor] if output.monitor else []
        candidates += [s.name for s in self.sources if s.monitor_of == output_name]
        for name in candidates:
            source = self.source(name)
            if source is not None and source.monitor_of == output_name:
                return source.name
        return None

    def microphones(self) -> list[AudioSource]:
        return [s for s in self.sources if not s.is_monitor]

    def recorded_output(self, stream: AudioStream) -> str:
        """The output whose monitor ``stream`` records, ``""`` for a microphone."""
        source = self.source(stream.device)
        return source.monitor_of if source is not None else ""

    def sunshine_captures(self) -> list[AudioStream]:
        return [s for s in self.capture if _is_sunshine_process(s)]

    def steam_captures(self) -> list[AudioStream]:
        return [s for s in self.capture if _is_steam_process(s)]

    def legacy_modules(self) -> list[AudioModule]:
        patterns = [f"sink_name={name}" for name in LEGACY_SINKS] + ["source=SunshineGameSink.monitor", "SunshineLoopback"]
        return [m for m in self.modules if m.name in {"module-null-sink", "module-loopback"} and any(p in m.args for p in patterns)]


def build_graph(sinks: str, sources: str, sink_inputs: str, source_outputs: str, modules: str, default_sink: str, default_source: str) -> AudioGraph:
    outputs = []
    for item in parse_pactl_list(sinks):
        f, p = item["fields"], item["properties"]
        name = f.get("Name", "")
        if not name:
            continue
        description = f.get("Description", "") or p.get("device.description", "") or name
        outputs.append(AudioOutput(item["index"], name, description, f.get("Monitor Source", ""), f.get("State", ""), classify_output(name, description, p, f.get("Flags", ""))))
    source_list = []
    for item in parse_pactl_list(sources):
        f, p = item["fields"], item["properties"]
        name = f.get("Name", "")
        if not name:
            continue
        monitor_of = f.get("Monitor of Sink", "")
        source_list.append(AudioSource(item["index"], name, f.get("Description", "") or name, "" if monitor_of in ("", "n/a") else monitor_of))
    sink_names = {o.index: o.name for o in outputs}
    source_names = {s.index: s.name for s in source_list}

    def streams(text: str, key: str, names: Mapping[str, str]) -> tuple[AudioStream, ...]:
        found = []
        for item in parse_pactl_list(text):
            f, p = item["fields"], item["properties"]
            target = f.get(key, "")
            found.append(
                AudioStream(
                    item["index"],
                    names.get(target, target),
                    p.get("application.name", ""),
                    p.get("application.process.binary", ""),
                    p.get("application.process.id", ""),
                    p.get("media.name", ""),
                    p.get("node.name", ""),
                    dict(p),
                )
            )
        return tuple(found)

    module_list = []
    for line in (modules or "").splitlines():
        parts = line.split("\t")
        if len(parts) >= 2 and parts[0].strip().isdigit():
            module_list.append(AudioModule(parts[0].strip(), parts[1].strip(), parts[2].strip() if len(parts) > 2 else ""))
    return AudioGraph(
        tuple(outputs),
        tuple(source_list),
        streams(sink_inputs, "Sink", sink_names),
        streams(source_outputs, "Source", source_names),
        tuple(module_list),
        (default_sink or "").strip(),
        (default_source or "").strip(),
    )


# --------------------------------------------------------------------------
# Capture plan and status: what Sunshine should record and what it records.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class CapturePlan:
    output: AudioOutput | None
    monitor: str | None
    problem: str = ""  # "", "no-sound-server", "no-output", "missing-output", "no-monitor", "not-selectable"

    @property
    def available(self) -> bool:
        return self.monitor is not None


def capture_plan(graph: AudioGraph | None, manual_output: str = "") -> CapturePlan:
    """Which monitor Sunshine will record. Never a microphone, never a guess."""
    if graph is None:
        return CapturePlan(None, None, "no-sound-server")
    if manual_output:
        output = graph.output(manual_output)
        if output is None:
            return CapturePlan(None, None, "missing-output")
        if output.kind not in (HARDWARE, BLUETOOTH):
            return CapturePlan(output, None, "not-selectable")
    else:
        output = graph.output(graph.default_sink)
        if output is None:
            return CapturePlan(None, None, "no-output")
    monitor = graph.monitor_of(output.name)
    return CapturePlan(output, monitor, "" if monitor else "no-monitor")


def sunshine_audio_sink(manual_output: str, play_on_host: bool) -> str | None:
    """The ``audio_sink`` value for sunshine.conf (``None`` removes the key).

    Measured behavior of Sunshine on Linux: without ``audio_sink`` it records
    the monitor of the default output when a client connects, and switches the
    default to its own virtual output only when that client asks not to play
    sound on the host. Any other ``audio_sink`` also becomes the default output
    for the session. Automatic therefore leaves it unset; "only on the other
    computer" points it at Sunshine's own stereo output.
    """
    if manual_output:
        return manual_output
    return None if play_on_host else SUNSHINE_STEREO_SINK


def manual_outputs(graph: AudioGraph | None) -> list[AudioOutput]:
    """Outputs that may be chosen explicitly.

    Sunshine makes the chosen output the default for the session. A virtual
    output that forwards to the default output (EasyEffects, JamesDSP) would
    then feed itself, so virtual outputs are captured through Automatic only.
    """
    if graph is None:
        return []
    return [o for o in graph.outputs if o.kind in (HARDWARE, BLUETOOTH) and graph.monitor_of(o.name)]


@dataclass(frozen=True)
class Bridge:
    """Sound from one output's monitor linked into another output."""

    source_sink: str
    target_sink: str
    reason: str  # "client-muted-host" | "sunshine-switched-output"

    @property
    def source(self) -> str:
        return f"{self.source_sink}.monitor"


def bridge_port_pairs(bridge: Bridge, outputs: Iterable[str], inputs: Iterable[str]) -> list[tuple[str, str]]:
    """``(monitor port, playback port)`` pairs, channel by channel.

    Surround into stereo keeps the front pair; anything into a mono device
    goes to its single channel.
    """
    prefix_out, prefix_in = f"{bridge.source_sink}:monitor_", f"{bridge.target_sink}:playback_"
    out_channels = [port[len(prefix_out) :] for port in outputs if port.startswith(prefix_out)]
    in_channels = {port[len(prefix_in) :] for port in inputs if port.startswith(prefix_in)}
    if in_channels == {"MONO"}:
        return [(prefix_out + ch, prefix_in + "MONO") for ch in out_channels if ch in ("FL", "FR", "MONO")]
    return [(prefix_out + ch, prefix_in + ch) for ch in out_channels if ch in in_channels]


def parse_pw_links(text: str) -> dict[str, tuple[str, str]]:
    """``pw-link -l -I`` → ``{link id: (output port, input port)}``."""
    links: dict[str, tuple[str, str]] = {}
    port = ""
    for raw in (text or "").splitlines():
        match = re.match(r"^\s*(\d+)\s+\|(->|<-)\s+\d+\s+(\S.*)$", raw)
        if match and port:
            link_id, direction, other = match.group(1), match.group(2), match.group(3).strip()
            links[link_id] = (port, other) if direction == "->" else (other, port)
            continue
        match = re.match(r"^\s*\d+\s+(\S.*)$", raw)
        if match:
            port = match.group(1).strip()
    return links


@dataclass(frozen=True)
class AudioStatus:
    """Measured facts for the interface. Nothing here is estimated."""

    plan: CapturePlan
    default_source: str = ""
    default_source_description: str = ""
    sunshine_source: str = ""  # the source Sunshine records now, "" when no client
    sunshine_records_monitor: bool | None = None  # None when no client is connected
    microphone_sent: bool = False
    steam_sources: tuple[tuple[str, bool], ...] = ()  # (source, is_monitor)
    bridges: tuple[Bridge, ...] = ()
    host_muted_by_client: bool = False
    notes: tuple[str, ...] = ()


def audio_status(graph: AudioGraph | None, manual_output: str = "", bridges: Iterable[Bridge] = ()) -> AudioStatus:
    plan = capture_plan(graph, manual_output)
    if graph is None:
        return AudioStatus(plan)
    mic = graph.source(graph.default_source)
    captures = graph.sunshine_captures()
    source = captures[0].device if captures else ""
    records_monitor = bool(graph.recorded_output(captures[0])) if captures else None
    default = graph.output(graph.default_sink)
    return AudioStatus(
        plan,
        graph.default_source,
        mic.description if mic else "",
        source,
        records_monitor,
        any(not graph.recorded_output(c) for c in captures),
        tuple((s.device, bool(graph.recorded_output(s))) for s in graph.steam_captures()),
        tuple(bridges),
        bool(captures) and default is not None and default.kind == SUNSHINE,
    )


def desired_bridges(graph: AudioGraph, *, original_sink: str, play_on_host: bool) -> tuple[list[Bridge], list[str]]:
    """Loopbacks needed right now, plus notes about ones refused as unsafe.

    Two measured Sunshine behaviors need help:

    1. When a client asks not to play sound on the host, Sunshine makes its
       virtual output the default. If Sunshine records a *different* output
       (an explicit ``audio_sink``, or stereo while the client asked for
       surround), applications follow the default away from what is recorded
       and the other computer hears silence. Bridge the new default into the
       recorded output.
    2. With "Also play sound on this computer" on, the same client request
       mutes this computer. Bridge Sunshine's virtual output back into the
       output the person was using.

    Targets are limited to devices and Sunshine's own outputs: a virtual output
    usually forwards to the default output, which here is the bridge source,
    so a loopback into it would feed itself.
    """
    bridges: list[Bridge] = []
    notes: list[str] = []
    captures = graph.sunshine_captures()
    if not captures:
        return bridges, notes
    default = graph.output(graph.default_sink)
    for capture in captures:
        recorded = graph.recorded_output(capture)
        recorded_output = graph.output(recorded) if recorded else None
        if recorded_output is None:
            continue
        if default is not None and default.kind == SUNSHINE and default.name != recorded:
            if recorded_output.kind in (HARDWARE, BLUETOOTH, SUNSHINE):
                bridges.append(Bridge(default.name, recorded, "sunshine-switched-output"))
            else:
                notes.append("unsafe-target")
        elif play_on_host and recorded_output.kind == SUNSHINE and default is not None and default.name == recorded:
            target = graph.output(original_sink)
            if target is None:
                notes.append("original-output-missing")
            elif target.kind in (HARDWARE, BLUETOOTH):
                bridges.append(Bridge(recorded, target.name, "client-muted-host"))
            else:
                notes.append("unsafe-target")
    unique = list(dict.fromkeys(bridges))
    return [b for b in unique if not _creates_cycle(b, unique)], notes


def _creates_cycle(bridge: Bridge, bridges: list[Bridge]) -> bool:
    edges: dict[str, set[str]] = {}
    for b in bridges:
        edges.setdefault(b.source_sink, set()).add(b.target_sink)
    seen: set[str] = set()
    stack = [bridge.target_sink]
    while stack:
        node = stack.pop()
        if node == bridge.source_sink:
            return True
        if node in seen:
            continue
        seen.add(node)
        stack.extend(edges.get(node, ()))
    return False


# --------------------------------------------------------------------------
# Sound-server access
# --------------------------------------------------------------------------


class AudioManager:
    """Reads the sound server and performs the few writes Big Remote Play owns."""

    def __init__(self, runner: Runner = subprocess.run) -> None:
        self._run = runner

    def _pactl(self, *args: str, timeout: float = _COMMAND_TIMEOUT) -> subprocess.CompletedProcess | None:
        try:
            return self._run(["pactl", *args], capture_output=True, text=True, timeout=timeout, env={**os.environ, "LC_ALL": "C"}, stdin=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError) as exc:
            _log.warning("pactl %s failed: %s", args[:1], exc)
            return None

    def snapshot(self, *, streams: bool = True) -> AudioGraph | None:
        """The whole graph, or ``None`` when no PulseAudio-compatible server answers.

        ``streams=False`` reads only outputs, sources and defaults.
        """
        texts = []
        for args in (("list", "sinks"), ("list", "sources"), ("list", "sink-inputs"), ("list", "source-outputs"), ("list", "short", "modules"), ("get-default-sink",), ("get-default-source",)):
            if not streams and args[-1] in ("sink-inputs", "source-outputs", "modules"):
                texts.append("")
                continue
            result = self._pactl(*args)
            if result is None or result.returncode != 0:
                return None
            texts.append(result.stdout or "")
        return build_graph(*texts)

    def get_passive_sinks(self) -> list[dict[str, str]]:
        """Outputs that may be chosen explicitly, as ``{"id", "name", "description"}``."""
        return [{"id": o.index, "name": o.name, "description": o.description} for o in manual_outputs(self.snapshot(streams=False))]

    def get_default_sink(self) -> str | None:
        result = self._pactl("get-default-sink")
        return result.stdout.strip() if result is not None and result.returncode == 0 else None

    def set_default_sink(self, sink_name: str) -> bool:
        result = self._pactl("set-default-sink", sink_name)
        return result is not None and result.returncode == 0

    def _pw_link(self, *args: str) -> subprocess.CompletedProcess | None:
        try:
            return self._run(["pw-link", *args], capture_output=True, text=True, timeout=_COMMAND_TIMEOUT, env={**os.environ, "LC_ALL": "C"}, stdin=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError) as exc:
            _log.warning("pw-link failed: %s", exc)
            return None

    def links(self) -> dict[str, tuple[str, str]] | None:
        result = self._pw_link("-l", "-I")
        return parse_pw_links(result.stdout) if result is not None and result.returncode == 0 else None

    def link_bridge(self, bridge: Bridge, token: str) -> list[dict[str, str]]:
        """Link ``bridge`` port by port; returns ``[{"id", "out", "in"}]`` (empty on failure)."""
        outputs, inputs = self._pw_link("-o"), self._pw_link("-i")
        if outputs is None or inputs is None or outputs.returncode != 0 or inputs.returncode != 0:
            return []
        pairs = bridge_port_pairs(bridge, outputs.stdout.split("\n"), inputs.stdout.split("\n"))
        if not pairs:
            return []
        props = json.dumps({OWNER_PROPERTY: token, "object.linger": True})
        for out_port, in_port in pairs:
            self._pw_link("-p", props, out_port, in_port)
        current = self.links() or {}
        created = [{"id": link_id, "out": ports[0], "in": ports[1]} for link_id, ports in current.items() if ports in pairs]
        if len(created) != len(pairs):
            self.unlink_owned(created, current)
            return []
        return created

    def unlink_owned(self, links: Iterable[Mapping[str, str]], current: Mapping[str, tuple[str, str]] | None = None) -> None:
        """Remove links we made, only while each id still joins the same two ports."""
        current = current if current is not None else (self.links() or {})
        for link in links:
            if current.get(str(link.get("id"))) == (link.get("out"), link.get("in")):
                self._pw_link("-d", str(link["out"]), str(link["in"]))

    def unload_legacy(self, graph: AudioGraph) -> list[str]:
        """Remove null sinks/loopbacks left by a crashed Big Remote Play 2.x."""
        removed = []
        for module in graph.legacy_modules():
            result = self._pactl("unload-module", module.index)
            if result is not None and result.returncode == 0:
                removed.append(module.index)
        return removed

    def move_capture(self, stream_index: str, source: str) -> bool:
        result = self._pactl("move-source-output", stream_index, source)
        return result is not None and result.returncode == 0

    # ------------------------------------------------------------------
    # "Test audio": a short generated tone, measured on the captured monitor.
    # ------------------------------------------------------------------

    TONE_HZ = 660.0
    TONE_SECONDS = 0.8
    _RATE = 48_000

    def test_tone(self, manual_output: str = "") -> dict:
        """Play a short tone and measure it on the monitor Sunshine would record.

        Returns ``{"played", "detected", "level_db", "monitor", "output"}``.
        The level is the tone's own energy (Goertzel), so other sound playing
        at the same time does not count as a detection.
        """
        plan = capture_plan(self.snapshot(), manual_output)
        result = {"played": False, "detected": False, "level_db": None, "monitor": plan.monitor, "output": plan.output.description if plan.output else ""}
        if not plan.available or plan.output is None:
            return result
        samples = int(self._RATE * self.TONE_SECONDS)
        tone = array.array("h")
        for i in range(samples):
            fade = min(1.0, i / 2400, (samples - i) / 2400)  # 50 ms ramps, no clicks
            value = int(0.12 * 32767 * fade * math.sin(2 * math.pi * self.TONE_HZ * i / self._RATE))
            tone.extend((value, value))
        raw = ["--raw", f"--rate={self._RATE}", "--channels=2", "--format=s16le"]
        try:
            recorder = subprocess.Popen(["parecord", *raw, f"--device={plan.monitor}", "--latency-msec=20"], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        except OSError:
            return result
        chunks: list[bytes] = []
        reader = threading.Thread(target=lambda: chunks.append(recorder.stdout.read() if recorder.stdout else b""), daemon=True)
        reader.start()
        try:
            time.sleep(0.25)
            device = [f"--device={plan.output.name}"] if manual_output else []
            player = self._run(["paplay", *raw, *device, "--client-name=Big Remote Play", "--stream-name=Test audio"], input=tone.tobytes(), capture_output=True, timeout=10)
            result["played"] = player.returncode == 0
            time.sleep(0.3)
        except (OSError, subprocess.SubprocessError):
            pass
        finally:
            recorder.terminate()
            try:
                recorder.wait(3)
            except subprocess.TimeoutExpired:
                recorder.kill()
            reader.join(3)
        level = tone_level_db(b"".join(chunks), self.TONE_HZ, self._RATE)
        result["level_db"] = level
        result["detected"] = result["played"] and level is not None and level > -45.0
        return result


def tone_level_db(pcm: bytes, frequency: float, rate: int) -> float | None:
    """Level of ``frequency`` in interleaved stereo s16le, dBFS; ``None`` if empty."""
    data = array.array("h")
    data.frombytes(pcm[: len(pcm) // 4 * 4])
    left = data[::2]
    if len(left) < rate // 10:
        return None
    coefficient = 2 * math.cos(2 * math.pi * frequency / rate)
    block = rate // 10
    best = 0.0
    for start in range(0, len(left) - block + 1, block // 2):
        s1 = s2 = 0.0
        for sample in left[start : start + block]:
            s1, s2 = sample / 32768 + coefficient * s1 - s2, s1
        power = s1 * s1 + s2 * s2 - coefficient * s1 * s2
        best = max(best, 2 * math.sqrt(max(power, 0.0)) / block)
    return 20 * math.log10(best) if best > 0 else -120.0


# --------------------------------------------------------------------------
# One sharing session's ownership
# --------------------------------------------------------------------------


def _state_path() -> Path:
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if runtime and os.path.isdir(runtime):
        return Path(runtime) / "big-remote-play" / "audio-session.json"
    from big_remote_play import paths

    return Path(paths.CONFIG_DIR) / "audio-session.json"


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class AudioRoutingSession:
    """Snapshot, reconcile and restore for one sharing session.

    ``begin`` records the output in use; nothing is written to the sound
    server. ``reconcile`` adds or removes owned port links when Sunshine changes
    the default output, keeps Sunshine on the current output in Automatic, and
    moves Sunshine back to a monitor if it were ever recording a microphone.
    ``end`` removes only this session's bridges and, after Sunshine exited,
    puts back the output Sunshine switched away from if Sunshine could not.
    """

    def __init__(self, manager: AudioManager, *, manual_output: str = "", play_on_host: bool = True, state_path: Path | None = None) -> None:
        self.manager = manager
        self.manual_output = manual_output
        self.play_on_host = play_on_host
        self.state_path = state_path or _state_path()
        self.token = secrets.token_hex(8)
        self.original_sink = ""
        self.links: dict[Bridge, list[dict[str, str]]] = {}
        self.notes: tuple[str, ...] = ()
        self._owner_pid = os.getpid()
        self._lock = threading.Lock()

    # -- persistence -------------------------------------------------------
    def _save(self) -> None:
        from big_remote_play.utils.secure_io import secure_write_text

        state = {
            "owner_pid": os.getpid(),
            "token": self.token,
            "original_sink": self.original_sink,
            "manual_output": self.manual_output,
            "play_on_host": self.play_on_host,
            "bridges": [{"source": b.source_sink, "target": b.target_sink, "reason": b.reason, "links": links} for b, links in self.links.items()],
        }
        try:
            secure_write_text(str(self.state_path), json.dumps(state))
        except OSError as exc:
            _log.warning("Could not record audio session state: %s", exc)

    def _clear(self) -> None:
        try:
            self.state_path.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            _log.warning("Could not remove audio session state: %s", exc)

    @classmethod
    def load(cls, manager: AudioManager, state_path: Path | None = None) -> AudioRoutingSession | None:
        path = state_path or _state_path()
        try:
            state = json.loads(path.read_text())
        except (OSError, ValueError):
            return None
        if not isinstance(state, dict) or not re.fullmatch(r"[0-9a-f]{16}", str(state.get("token", ""))):
            return None
        session = cls(manager, manual_output=str(state.get("manual_output") or ""), play_on_host=bool(state.get("play_on_host", True)), state_path=path)
        session.token = state["token"]
        session.original_sink = str(state.get("original_sink") or "")
        for entry in state.get("bridges") or []:
            if not isinstance(entry, dict):
                continue
            links = [
                {"id": str(link["id"]), "out": str(link["out"]), "in": str(link["in"])}
                for link in entry.get("links") or []
                if isinstance(link, dict) and str(link.get("id", "")).isdigit() and isinstance(link.get("out"), str) and isinstance(link.get("in"), str)
            ]
            if links:
                session.links[Bridge(str(entry.get("source", "")), str(entry.get("target", "")), str(entry.get("reason", "")))] = links
        try:
            session._owner_pid = int(state.get("owner_pid") or 0)
        except (TypeError, ValueError):
            session._owner_pid = 0
        return session

    # -- lifecycle ---------------------------------------------------------
    def begin(self, graph: AudioGraph | None) -> None:
        with self._lock:
            default = graph.output(graph.default_sink) if graph else None
            self.original_sink = default.name if default is not None and default.is_real else ""
            self._save()

    def reconcile(self, graph: AudioGraph | None = None) -> AudioStatus:
        with self._lock:
            graph = graph if graph is not None else self.manager.snapshot()
            if graph is None:
                return audio_status(None, self.manual_output)
            notes: list[str] = []
            # Keep Sunshine on a monitor, and in Automatic on the output in use.
            plan = capture_plan(graph, self.manual_output)
            default = graph.output(graph.default_sink)
            if default is not None and default.is_real:
                self.original_sink = default.name  # the person's latest choice
            for capture in graph.sunshine_captures():
                recorded = graph.recorded_output(capture)
                if not recorded and plan.monitor:
                    if self.manager.move_capture(capture.index, plan.monitor):
                        notes.append("microphone-replaced")
                elif not self.manual_output and default is not None and default.is_real and recorded and recorded != default.name:
                    # The person changed output during the stream: follow it
                    # instead of recording a device nobody listens to.
                    monitor = graph.monitor_of(default.name)
                    if monitor and self.manager.move_capture(capture.index, monitor):
                        notes.append("followed-output")
            if notes:
                graph = self.manager.snapshot() or graph
            wanted, refused = desired_bridges(graph, original_sink=self.original_sink, play_on_host=self.play_on_host)
            notes += refused
            if wanted or self.links:
                current = self.manager.links() or {}
                for bridge, links in list(self.links.items()):
                    intact = all(current.get(link["id"]) == (link["out"], link["in"]) for link in links)
                    if bridge not in wanted or not intact:
                        self.manager.unlink_owned(links, current)
                        del self.links[bridge]
                for bridge in wanted:
                    if bridge not in self.links:
                        links = self.manager.link_bridge(bridge, self.token)
                        if links:
                            self.links[bridge] = links
                        else:
                            notes.append("bridge-failed")
            self.notes = tuple(dict.fromkeys(notes))
            self._save()
            return replace(audio_status(graph, self.manual_output, tuple(self.links)), notes=self.notes)

    def end(self, *, sunshine_stopped: bool = True) -> None:
        with self._lock:
            if self.links:
                current = self.manager.links()
                if current is not None:
                    for links in self.links.values():
                        self.manager.unlink_owned(links, current)
                self.links.clear()
            graph = self.manager.snapshot()
            if graph is not None:
                self._restore_output(graph, sunshine_stopped)
            self._clear()

    def _restore_output(self, graph: AudioGraph, sunshine_stopped: bool) -> None:
        """Sunshine restores the default output on a normal exit; not after a crash."""
        if not sunshine_stopped:
            return
        default = graph.output(graph.default_sink)
        if default is None or default.kind not in (SUNSHINE, LEGACY):
            return  # the person's own choice, or already restored by Sunshine
        if self.original_sink and graph.output(self.original_sink) is not None:
            if self.manager.set_default_sink(self.original_sink):
                _log.info("Restored the audio output Sunshine left on its virtual output.")

    @classmethod
    def recover(cls, manager: AudioManager, *, sunshine_running: bool, state_path: Path | None = None) -> AudioRoutingSession | None:
        """After a crash: adopt a live sharing session, or clean up what we own.

        Returns the adopted session when Sunshine is still sharing, else ``None``.
        """
        session = cls.load(manager, state_path)
        graph = manager.snapshot()
        if graph is not None and graph.legacy_modules() and not sunshine_running:
            manager.unload_legacy(graph)
            graph = manager.snapshot()
        if session is None:
            return None
        owner = session._owner_pid
        if owner and owner != os.getpid() and _pid_alive(owner) and _is_this_app(owner):
            return None  # another running window owns it
        if sunshine_running:
            session._save()  # this process owns it now
            return session
        session.end(sunshine_stopped=True)
        return None


def _is_this_app(pid: int) -> bool:
    try:
        cmdline = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
    except OSError:
        return False
    return any(b"big-remote-play" in part or b"big_remote_play" in part for part in cmdline)


class AudioWatcher:
    """Re-check audio when the sound server reports a change.

    Driven by ``pactl subscribe`` events, debounced, with a slow safety check.
    No polling of application streams.
    """

    # Default output changes arrive as "server" events; sink-input events
    # (volume, new application streams) do not affect what Sunshine records.
    RELEVANT = frozenset({"server", "sink", "source", "source-output"})
    _EVENT = re.compile(r"^Event '(new|change|remove)' on ([a-z-]+) #")

    def __init__(self, on_change: Callable[[], None], *, debounce: float = 0.4, safety_interval: float = 15.0, spawn: Callable[[], subprocess.Popen] | None = None) -> None:
        self._on_change = on_change
        self._debounce = debounce
        self._safety = safety_interval
        self._spawn = spawn or (
            lambda: subprocess.Popen(["pactl", "subscribe"], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, env={**os.environ, "LC_ALL": "C"})
        )
        self._stop = threading.Event()
        self._pending = threading.Event()
        self._process: subprocess.Popen | None = None
        self._threads: list[threading.Thread] = []

    def start(self) -> None:
        self._pending.set()  # check once right away
        for target in (self._read_events, self._dispatch):
            thread = threading.Thread(target=target, daemon=True)
            thread.start()
            self._threads.append(thread)

    def stop(self) -> None:
        self._stop.set()
        self._pending.set()
        process, self._process = self._process, None
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(2)
            except subprocess.TimeoutExpired:
                process.kill()

    def _read_events(self) -> None:
        while not self._stop.is_set():
            try:
                self._process = self._spawn()
            except OSError:
                return  # the safety check still runs
            stream = self._process.stdout
            for line in stream if stream is not None else ():
                if self._stop.is_set():
                    return
                match = self._EVENT.match(line.strip())
                if match and match.group(2) in self.RELEVANT:
                    self._pending.set()
            if self._stop.wait(2.0):  # the server restarted; subscribe again
                return

    def _dispatch(self) -> None:
        while not self._stop.is_set():
            self._pending.wait(self._safety)
            if self._stop.is_set():
                return
            self._stop.wait(self._debounce)  # let a burst of events settle
            self._pending.clear()
            if self._stop.is_set():
                return
            try:
                self._on_change()
            except Exception as exc:  # a failed check must not end the watcher
                _log.error("Audio check failed: %s", exc)
