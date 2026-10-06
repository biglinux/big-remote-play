"""Sunshine audio on PipeWire/PulseAudio: discover, verify and own only what we add.

Contract (see docs/audio-architecture.md):

- Sunshine records the *monitor* of an output — the sound this computer plays —
  never a microphone. A source counts as a monitor only when the server itself
  says so ("Monitor of Sink"), never because its name ends in ``.monitor``.
- Automatic mode writes nothing to the sound server: Sunshine records the
  monitor of whatever output is current when a client connects.
- Big Remote Play adds a "bridge": direct PipeWire port links from a
  Sunshine virtual output's monitor into a device. Port links are not
  streams, so effect programs that capture every new stream (EasyEffects,
  JamesDSP) cannot pull them into a feedback loop. Application streams are
  never moved, so per-process capture by other programs (Steam Remote Play,
  OBS, recorders) keeps working.
- While a voice call program plays into what Sunshine records, it adds its
  own output with every other program linked into it, and Sunshine records
  that instead: the person connecting no longer hears their own voice.
- After Sunshine exits, another program's output that played into a Sunshine
  output and is left linked to nothing is linked to the default device again,
  as that program would have done itself.
- Sunshine's own recording stream is kept unmuted at full volume. The session
  manager restores a per-application mute and volume to every new stream, so
  one mute of "sunshine" in a mixer silenced every later session.
- Object ids are never persisted except the ids of links and of the output
  this app created, always re-verified (ports, token) before removal.
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

from big_remote_play.utils.i18n import _

_log = logging.getLogger("big-remoteplay")

Runner = Callable[..., subprocess.CompletedProcess]

SUNSHINE_SINK_PREFIX = "sink-sunshine-"
SUNSHINE_STEREO_SINK = "sink-sunshine-stereo"
# Null sinks created by Big Remote Play 2.x and earlier. They are no longer
# created; leftovers from a crash of an old version are removed on recovery.
LEGACY_SINKS = frozenset({"SunshineGameSink", "SunshineStereo", "SunshineHybrid"})
OWNER_PROPERTY = "big-remote-play.owner"
_COMMAND_TIMEOUT = 5
# PA_VOLUME_NORM: 100 % as pactl prints raw volumes.
VOLUME_NORM = 65536

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
    channel_map: str = ""  # "front-left,front-right", as pactl reports it

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
    muted: bool = False
    volume: int | None = None  # the quietest channel, raw (VOLUME_NORM is 100 %); None if unknown

    @property
    def silenced(self) -> bool:
        """Muted or turned down: what it records or plays is quieter than the source."""
        return self.muted or (self.volume is not None and self.volume < VOLUME_NORM)


def _lowest_volume(text: str) -> int | None:
    """``front-left: 40632 /  62% / -12.46 dB, …`` → 40632: the raw value before each first ``/``."""
    tokens = (text or "").replace(",", " ").split()
    values = [int(tokens[i - 1]) for i, token in enumerate(tokens) if token == "/" and i > 0 and tokens[i - 1].isdigit()]
    return min(values) if values else None


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
        outputs.append(
            AudioOutput(item["index"], name, description, f.get("Monitor Source", ""), f.get("State", ""), classify_output(name, description, p, f.get("Flags", "")), f.get("Channel Map", ""))
        )
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
                    f.get("Mute", "") == "yes",
                    _lowest_volume(f.get("Volume", "")),
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


def _node_of(port: str) -> str:
    return port.rsplit(":", 1)[0]


def _is_sunshine_node(node: str) -> bool:
    return node.startswith(SUNSHINE_SINK_PREFIX) or node in LEGACY_SINKS


def feeder_ports(links: Mapping[str, tuple[str, str]], owned: Iterable[tuple[str, str]] = ()) -> set[str]:
    """Output ports of other programs that play into a Sunshine virtual output.

    Typically an effects program (JamesDSP, EasyEffects) that follows the
    default output with its own links. When Sunshine removes its outputs on
    exit, such a program can miss the change and keep its output unlinked.
    """
    skip = set(owned)
    found = set()
    for out_port, in_port in links.values():
        if (out_port, in_port) in skip or _is_sunshine_node(_node_of(out_port)):
            continue
        if _is_sunshine_node(_node_of(in_port)) and in_port.rsplit(":", 1)[-1].startswith("playback_"):
            found.add(out_port)
    return found


def relink_pairs(ports: Iterable[str], target_sink: str, inputs: Iterable[str]) -> list[tuple[str, str]]:
    """``(output port, playback port)`` of ``target_sink``, by channel name.

    ``output_FL`` goes to ``playback_FL``; a mono device takes the front
    channels. Ports whose channel the device lacks are left alone.
    """
    prefix = f"{target_sink}:playback_"
    channels = {port[len(prefix) :] for port in inputs if port.startswith(prefix)}
    pairs = []
    for port in sorted(ports):
        channel = port.rsplit(":", 1)[-1].rsplit("_", 1)[-1]
        if channels == {"MONO"} and channel in ("FL", "FR", "MONO"):
            pairs.append((port, prefix + "MONO"))
        elif channel in channels:
            pairs.append((port, prefix + channel))
    return pairs


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
    calls_kept_out: tuple[str, ...] = ()  # call programs playing here but not sent
    sunshine_muted: bool = False  # Sunshine's recording stream is muted now
    sunshine_volume: int | None = None  # its quietest channel in %, None without a client
    sunshine_log: SunshineAudioLog | None = None  # what Sunshine logged for its latest session
    level_restored: bool = False  # this session found Sunshine's recording muted or turned down and fixed it
    game_only: bool = False  # Game Window sends only the game's sound
    game_programs: tuple[str, ...] = ()  # the game's streams linked into the mix now
    game_separated: bool | None = None  # every Sunshine recording is on the game-only mix; None without a client


def audio_status(graph: AudioGraph | None, manual_output: str = "", bridges: Iterable[Bridge] = ()) -> AudioStatus:
    plan = capture_plan(graph, manual_output)
    if graph is None:
        return AudioStatus(plan)
    mic = graph.source(graph.default_source)
    captures = graph.sunshine_captures()
    source = captures[0].device if captures else ""
    records_monitor = bool(graph.recorded_output(captures[0])) if captures else None
    default = graph.output(graph.default_sink)
    volumes = [c.volume for c in captures if c.volume is not None]
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
        sunshine_muted=any(c.muted for c in captures),
        sunshine_volume=round(min(volumes) * 100 / VOLUME_NORM) if volumes else None,
    )


# Whether the other computer can get sound, from measured facts only.
STREAM_UNAVAILABLE = "unavailable"  # no output or no verified monitor here
STREAM_WAITING = "waiting"  # nobody is playing
STREAM_STARTING = "starting"  # a device plays, Sunshine has no recording yet
STREAM_CAPTURE_FAILED = "capture-failed"  # Sunshine logged that it could not open the sound
STREAM_MUTED = "muted"  # Sunshine's recording is muted or at 0 %
STREAM_MICROPHONE = "microphone"  # Sunshine records an input: never acceptable
STREAM_NOT_SEPARATED = "not-separated"  # Game Window: the game's sound could not be sent on its own
STREAM_SENDING = "sending"


def stream_audio_state(status: AudioStatus | None, devices_playing: int) -> str:
    """One state for the person: is this computer's sound reaching the stream?

    Read from the sound server and Sunshine's log. It ends at Sunshine: whether
    the other device plays the sound is only known on that device.
    """
    if status is None or not status.plan.available:
        return STREAM_UNAVAILABLE
    if status.microphone_sent:
        return STREAM_MICROPHONE
    if status.sunshine_source:
        if status.sunshine_muted or status.sunshine_volume == 0:
            return STREAM_MUTED
        if status.game_only and status.game_separated is False:
            return STREAM_NOT_SEPARATED
        return STREAM_SENDING
    if devices_playing <= 0:
        return STREAM_WAITING
    log = status.sunshine_log
    return STREAM_CAPTURE_FAILED if log is not None and log.failed else STREAM_STARTING


# --------------------------------------------------------------------------
# Sunshine's own log: which monitor it opened, the encoder, and failures.
# Messages are Sunshine's (English, never translated); see src/platform/linux/audio.cpp.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class SunshineAudioLog:
    source: str | None = None  # the monitor it opened; "" when it found none, None when not logged
    encoder: str = ""  # e.g. "48 kHz, 2 channels, 96 kbps (total)"
    failed: bool = False  # "Unable to initialize audio capture. The stream will not have audio."
    error: str = ""  # the sound-server error logged before that, as Sunshine wrote it


_LOG_MONITOR = re.compile(r"Found default monitor by name: ?(.*)$")
_LOG_OPUS = re.compile(r"Opus initialized: (.*?)(?:, LOWDELAY)?\s*$")
_LOG_PA_ERROR = re.compile(r"pa_simple_new\(\) failed: (.*)$")
SUNSHINE_LOG_TAIL = 512 * 1024


def parse_sunshine_audio_log(text: str) -> SunshineAudioLog | None:
    """The sound of the latest session of the running Sunshine; ``None`` before any session."""
    lines = (text or "").splitlines()
    start = max((i for i, line in enumerate(lines) if "Sunshine version:" in line), default=-1) + 1
    report: SunshineAudioLog | None = None
    for line in lines[start:]:
        if "New streaming session started" in line:
            report = SunshineAudioLog()
        elif report is None:
            continue
        elif match := _LOG_MONITOR.search(line):
            report = replace(report, source=match.group(1).strip()[:200])
        elif "Unable to initialize audio capture" in line:
            report = replace(report, failed=True)
        elif match := _LOG_PA_ERROR.search(line):
            report = replace(report, error=match.group(1).strip()[:200])
        elif match := _LOG_OPUS.search(line):
            report = replace(report, encoder=match.group(1).strip()[:120])
    return report


def read_sunshine_audio_log(path: Path | str) -> SunshineAudioLog | None:
    """Read only the end of Sunshine's log (it grows for the whole session)."""
    try:
        with open(path, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - SUNSHINE_LOG_TAIL))
            data = handle.read()
    except OSError:
        return None
    return parse_sunshine_audio_log(data.decode("utf-8", errors="replace"))


def desired_bridges(graph: AudioGraph, *, original_sink: str, play_on_host: bool, origins: Mapping[str, str] | None = None) -> tuple[list[Bridge], list[str]]:
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

    ``origins`` maps a capture that records Big Remote Play's call-free mix
    to the output Sunshine chose for it, which is what these rules are about.
    """
    bridges: list[Bridge] = []
    notes: list[str] = []
    captures = graph.sunshine_captures()
    if not captures:
        return bridges, notes
    default = graph.output(graph.default_sink)
    for capture in captures:
        recorded = (origins or {}).get(capture.index) or graph.recorded_output(capture)
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
# Voice calls stay out of the stream.
#
# A call program on this computer plays the voices of everyone in the call,
# including the person connecting. Sunshine records everything this computer
# plays, so that person heard their own voice come back through the stream.
# While a call program plays into the output Sunshine records, Sunshine
# records Big Remote Play's own output instead, which receives every other
# program's sound through port links. Nothing is moved: every program keeps
# playing where it played, so this computer hears the call and the game as
# before.
# --------------------------------------------------------------------------

CALL_MIX_SINK = "big-remote-play-stream"

# Executable, application name or last part of a Flatpak id, lowercased with
# everything but letters and digits removed.
# fmt: off
CALL_PROGRAMS = frozenset(
    {
        "discord", "discordptb", "discordcanary", "vesktop", "webcord", "armcord", "legcord", "equibop", "goofcord", "dorion",
        "webrtcvoiceengine",  # the name of Discord's voice stream
        "fluxer", "zoom", "teams", "teamsforlinux", "msteams", "skype", "skypeforlinux", "slack",
        "telegram", "telegramdesktop", "ayugramdesktop", "kotatogramdesktop", "64gram",
        "signal", "signaldesktop", "element", "elementdesktop", "riot", "mumble",
        "teamspeak", "teamspeak3", "teamspeakclient", "ts3client", "ts3clientlinuxamd64", "ts3clientlinuxx86",
        "jami", "jamiqt", "jamignome", "linphone", "gnomecalls", "zapzap", "whatsapp", "whatsie",
        "revolt", "revoltdesktop", "jitsimeet", "mattermost", "mattermostdesktop", "rocketchat", "rocketchatdesktop",
        "nheko", "wire", "wiredesktop",
    }
)
# fmt: on
# Flatpak ids whose last part is too generic to list above.
CALL_APP_IDS = frozenset({"com.skype.client", "org.telegram.desktop", "com.mattermost.desktop", "com.rocketchat.rocket.chat"})
_CALL_ROLES = frozenset({"phone", "communication"})


def _simplified(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def is_call_stream(stream: AudioStream) -> bool:
    """A call program's playback: the voices of the people in the call.

    A call in a web browser is part of the browser's single stream and
    cannot be told apart from the rest of its sound.
    """
    props = stream.properties
    if props.get("media.role", "").lower() in _CALL_ROLES:
        return True
    names = [stream.binary, stream.app]
    for key in ("pipewire.access.portal.app_id", "application.id"):
        app_id = props.get(key, "").lower()
        if app_id in CALL_APP_IDS:
            return True
        if app_id:
            names.append(app_id.rsplit(".", 1)[-1])
    return any(_simplified(name) in CALL_PROGRAMS for name in names if name)


def call_program_name(stream: AudioStream) -> str:
    return stream.app or stream.binary or "?"


@dataclass(frozen=True)
class PwPort:
    node: int
    direction: str  # "output" | "input"
    channel: str
    monitor: bool = False


@dataclass(frozen=True)
class PwLink:
    output_node: int
    output_port: int
    input_node: int
    input_port: int
    owner: str = ""


@dataclass(frozen=True)
class PipeWireGraph:
    """Nodes, ports and links as PipeWire itself reports them (``pw-dump``)."""

    nodes: Mapping[int, Mapping[str, object]] = field(default_factory=dict)
    ports: Mapping[int, PwPort] = field(default_factory=dict)
    links: Mapping[int, PwLink] = field(default_factory=dict)

    def node_named(self, name: str) -> int | None:
        return next((node for node, props in self.nodes.items() if props.get("node.name") == name), None)

    def ports_of(self, node: int, direction: str) -> dict[str, int]:
        """``{channel: port id}``; a sink's monitor ports are left out of its outputs."""
        return {port.channel: port_id for port_id, port in sorted(self.ports.items()) if port.node == node and port.direction == direction and not port.monitor and port.channel}

    def is_fed(self, node: int) -> bool:
        """Whether other nodes play into ``node``: then it forwards their sound."""
        return any(link.input_node == node for link in self.links.values())

    def link_group(self, node: int) -> str:
        return str(self.nodes.get(node, {}).get("node.link-group") or "")

    def reaches(self, start: int, target: int) -> bool:
        """Whether sound leaving ``start`` ends up in ``target``.

        Follows links, and the internal connection between the halves of a
        loopback, combined output or filter chain (they share a link group).
        """
        groups: dict[str, list[int]] = {}
        for node in self.nodes:
            group = self.link_group(node)
            if group:
                groups.setdefault(group, []).append(node)
        seen: set[int] = set()
        stack = [start]
        while stack:
            node = stack.pop()
            if node == target:
                return True
            if node in seen:
                continue
            seen.add(node)
            stack.extend(link.input_node for link in self.links.values() if link.output_node == node)
            stack.extend(groups.get(self.link_group(node), ()))
        return False


def parse_pw_dump(text: str) -> PipeWireGraph | None:
    try:
        objects = json.loads(text or "[]")
    except ValueError:
        return None
    if not isinstance(objects, list):
        return None
    nodes: dict[int, Mapping[str, object]] = {}
    ports: dict[int, PwPort] = {}
    links: dict[int, PwLink] = {}
    for item in objects:
        object_id = item.get("id") if isinstance(item, dict) else None
        if not isinstance(item, dict) or not isinstance(object_id, int):
            continue
        raw_info = item.get("info")
        info: dict = raw_info if isinstance(raw_info, dict) else {}
        raw_props = info.get("props")
        props: dict = raw_props if isinstance(raw_props, dict) else {}
        kind = str(item.get("type", ""))
        node = props.get("node.id")
        if kind.endswith(":Node"):
            nodes[object_id] = props
        elif kind.endswith(":Port") and isinstance(node, int):
            ports[object_id] = PwPort(node, str(info.get("direction") or ""), str(props.get("audio.channel") or ""), bool(props.get("port.monitor")))
        elif kind.endswith(":Link"):
            ids = [value for value in (info.get(key) for key in ("output-node-id", "output-port-id", "input-node-id", "input-port-id")) if isinstance(value, int)]
            if len(ids) == 4:
                links[object_id] = PwLink(*ids, owner=str(props.get(OWNER_PROPERTY) or ""))
    return PipeWireGraph(nodes, ports, links)


def mix_channels(channel: str, inputs: Iterable[str]) -> list[str]:
    """Where a program's ``channel`` goes in the mix: same channel, mono into both fronts."""
    available = set(inputs)
    if channel in available:
        return [channel]
    if channel == "MONO":
        return [c for c in ("FL", "FR") if c in available]
    if available == {"MONO"} and channel in ("FL", "FR"):
        return ["MONO"]
    return []


def _stream_node(stream: AudioStream) -> int | None:
    value = stream.properties.get("object.id", "")
    return int(value) if value.isdigit() else None


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

    def unlinked_ports(self, ports: Iterable[str]) -> set[str] | None:
        """Those of ``ports`` that exist and have no outgoing link; ``None`` if unknown."""
        outputs, current = self._pw_link("-o"), self.links()
        if outputs is None or outputs.returncode != 0 or current is None:
            return None
        existing = {line.strip() for line in outputs.stdout.split("\n")}
        linked = {out_port for out_port, _in_port in current.values()}
        return {port for port in ports if port in existing and port not in linked}

    def relink(self, ports: Iterable[str], target_sink: str) -> list[tuple[str, str]]:
        """Link unlinked ``ports`` into ``target_sink``; returns the pairs now linked.

        These links restore the person's own chain, so they are not tagged as
        ours and are never removed by Big Remote Play.
        """
        inputs = self._pw_link("-i")
        if inputs is None or inputs.returncode != 0:
            return []
        pairs = relink_pairs(ports, target_sink, inputs.stdout.split("\n"))
        for out_port, in_port in pairs:
            self._pw_link(out_port, in_port)
        current = set((self.links() or {}).values())
        return [pair for pair in pairs if pair in current]

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

    def unmute_playback(self, stream_index: str) -> bool:
        result = self._pactl("set-sink-input-mute", stream_index, "0")
        return result is not None and result.returncode == 0

    def mute_capture(self, stream: AudioStream) -> bool:
        """Mute ``stream`` (a capture): a microphone nothing can replace."""
        result = self._pactl("set-source-output-mute", stream.index, "1")
        return result is not None and result.returncode == 0

    def restore_capture_level(self, stream: AudioStream) -> bool:
        """Unmute ``stream`` (a capture) and raise it to 100 % when it is below."""
        ok = True
        if stream.muted:
            result = self._pactl("set-source-output-mute", stream.index, "0")
            ok = result is not None and result.returncode == 0
        if stream.volume is not None and stream.volume < VOLUME_NORM:
            result = self._pactl("set-source-output-volume", stream.index, str(VOLUME_NORM))
            ok = ok and result is not None and result.returncode == 0
        return ok

    def pipewire(self) -> PipeWireGraph | None:
        """PipeWire's own nodes, ports and links; ``None`` when unreadable."""
        try:
            result = self._run(["pw-dump"], capture_output=True, text=True, timeout=_COMMAND_TIMEOUT, env={**os.environ, "LC_ALL": "C"}, stdin=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError) as exc:
            _log.warning("pw-dump failed: %s", exc)
            return None
        return parse_pw_dump(result.stdout) if result.returncode == 0 else None

    def create_call_mix(self, token: str, channel_map: str, description: str) -> str:
        """Load Big Remote Play's output for the call-free mix; its module index, or ``""``."""
        # Both quote levels of pipewire-pulse's module arguments; a quote or a
        # backslash in the description would end them early.
        description = re.sub(r'["\\]', "", description)
        args = ["load-module", "module-null-sink", f"sink_name={CALL_MIX_SINK}"]
        if re.fullmatch(r"[a-z0-9-]+(,[a-z0-9-]+)*", channel_map or ""):
            args.append(f"channel_map={channel_map}")
        args.append(f'sink_properties="device.description=\\"{description}\\" {OWNER_PROPERTY}={token}"')
        result = self._pactl(*args)
        index = result.stdout.strip() if result is not None and result.returncode == 0 else ""
        return index if index.isdigit() else ""

    def unload_owned_module(self, index: str, token: str) -> bool:
        """Unload module ``index`` only while its arguments still carry ``token``."""
        result = self._pactl("list", "short", "modules")
        if result is None or result.returncode != 0:
            return False
        mark = f"{OWNER_PROPERTY}={token}"
        for line in result.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) >= 3 and parts[0].strip() == index and mark in parts[2]:
                unloaded = self._pactl("unload-module", index)
                return unloaded is not None and unloaded.returncode == 0
        return False

    def unload_call_mixes(self) -> list[str]:
        """Remove every call-free mix output, whoever's token it has; only when nothing shares."""
        result = self._pactl("list", "short", "modules")
        if result is None or result.returncode != 0:
            return []
        removed = []
        for line in result.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) >= 3 and parts[1].strip() == "module-null-sink" and f"sink_name={CALL_MIX_SINK}" in parts[2] and OWNER_PROPERTY in parts[2]:
                unloaded = self._pactl("unload-module", parts[0].strip())
                if unloaded is not None and unloaded.returncode == 0:
                    removed.append(parts[0].strip())
        return removed

    def link_port_ids(self, pairs: Iterable[tuple[int, int]], token: str) -> None:
        props = json.dumps({OWNER_PROPERTY: token, "object.linger": True})
        for out_port, in_port in pairs:
            self._pw_link("-p", props, str(out_port), str(in_port))

    def unlink_ids(self, link_ids: Iterable[int]) -> None:
        for link_id in link_ids:
            self._pw_link("-d", str(link_id))

    # ------------------------------------------------------------------
    # "Test audio": a short generated tone, measured on the captured monitor.
    # ------------------------------------------------------------------

    TONE_HZ = 660.0
    TONE_SECONDS = 0.8
    _RATE = 48_000

    def test_tone(self, manual_output: str = "", *, game_only: bool = False) -> dict:
        """Play a short tone and measure it on the monitor Sunshine would record.

        Returns ``{"played", "detected", "level_db", "monitor", "output", "capture", "capture_source"}``.
        The level is the tone's own energy (Goertzel), so other sound playing
        at the same time does not count as a detection. ``capture`` says what
        Sunshine's own recording does right after (see ``capture_check``).
        """
        plan = capture_plan(self.snapshot(), manual_output)
        result = {
            "played": False,
            "detected": False,
            "level_db": None,
            "monitor": plan.monitor,
            "output": plan.output.description if plan.output else "",
            "capture": CAPTURE_NONE,
            "capture_source": "",
        }
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
        result["capture"], result["capture_source"] = capture_check(self.snapshot(), plan.monitor or "", game_only=game_only)
        return result


CAPTURE_NONE = "none"  # no client is playing: Sunshine records nothing
CAPTURE_OK = "ok"  # Sunshine records the tested monitor (or the call-free mix), unmuted
CAPTURE_MUTED = "muted"
CAPTURE_ELSEWHERE = "elsewhere"  # Sunshine records another source
CAPTURE_GAME_ONLY = "game-only"  # Game Window sends only the game: the tone is not sent


def capture_check(graph: AudioGraph | None, monitor: str, *, game_only: bool = False) -> tuple[str, str]:
    """Whether Sunshine's recording carries what was measured on ``monitor``."""
    captures = graph.sunshine_captures() if graph is not None else []
    if graph is None or not captures:
        return CAPTURE_NONE, ""
    if game_only and all(c.device == f"{CALL_MIX_SINK}.monitor" for c in captures):
        return CAPTURE_GAME_ONLY, captures[0].device
    for capture in captures:
        if capture.device not in (monitor, f"{CALL_MIX_SINK}.monitor"):
            return CAPTURE_ELSEWHERE, capture.device
    if any(c.muted or c.volume == 0 for c in captures):
        return CAPTURE_MUTED, captures[0].device
    return CAPTURE_OK, captures[0].device


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


@dataclass(frozen=True)
class GameScope:
    """The shared game window's process and names, for "only the game's sound"."""

    pid: int
    names: tuple[str, ...] = ()  # executable, game name, window title: a fallback for sandboxed PIDs

    @classmethod
    def from_state(cls, data: object) -> GameScope | None:
        """A scope, also for a window with no PID (0): then its names alone
        decide, and a game nothing matches sends nothing rather than every
        program's sound."""
        if not isinstance(data, dict) or not isinstance(data.get("pid"), int) or data["pid"] < 0:
            return None
        names = tuple(str(n)[:256] for n in (data.get("names") or []) if isinstance(n, str))[:8]
        return cls(data["pid"] if data["pid"] > 1 else 0, names)


def _game_process_family(pid: int) -> frozenset[int]:
    from big_remote_play.host.game_windows import game_process_family

    return game_process_family(pid)


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
    It also reconnects other programs' outputs that played into a Sunshine
    output and were left linked to nothing when Sunshine removed it.
    """

    # How long the person's own programs get to follow the default output
    # themselves before a port left without a link is reconnected.
    RELINK_SETTLE_SECONDS = 2.0
    _MAX_FEEDERS = 64
    MAX_LEVEL_RESTORES = 3

    def __init__(
        self,
        manager: AudioManager,
        *,
        manual_output: str = "",
        play_on_host: bool = True,
        state_path: Path | None = None,
        game: GameScope | None = None,
        game_family: Callable[[int], frozenset[int]] | None = None,
    ) -> None:
        self.manager = manager
        # Game Window with "only the game's sound": the mix carries the game alone.
        self.game = game
        self.game_family = game_family or _game_process_family
        self.game_programs: tuple[str, ...] = ()
        self.manual_output = manual_output
        self.play_on_host = play_on_host
        self.state_path = state_path or _state_path()
        self.token = secrets.token_hex(8)
        self.original_sink = ""
        self.links: dict[Bridge, list[dict[str, str]]] = {}
        self.feeders: set[str] = set()  # port names only, re-checked before use
        # The call-free mix: its module index while it exists, and for each
        # capture moved into it the output Sunshine had chosen (in memory
        # only: stream ids are never persisted).
        self.mix_module = ""
        self.capture_origins: dict[str, str] = {}
        self.calls_kept_out: tuple[str, ...] = ()
        # Shown in the desktop's sound settings while the mix exists.
        self.mix_description = _("Big Remote Play: sound sent to the other computer")
        self.notes: tuple[str, ...] = ()
        # Sunshine captures whose mute/volume this session restored, and how
        # often: the session manager may apply a saved level right after us,
        # but a person muting it again on purpose is not fought forever.
        self._level_restores: dict[str, int] = {}
        self.restored_level = False  # at least once this session
        self._logged: dict[str, str] = {}
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
            "feeders": sorted(self.feeders),
            "mix_module": self.mix_module,
            "game": {"pid": self.game.pid, "names": list(self.game.names)} if self.game is not None else None,
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
        session = cls(manager, manual_output=str(state.get("manual_output") or ""), play_on_host=bool(state.get("play_on_host", True)), state_path=path, game=GameScope.from_state(state.get("game")))
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
        feeders = state.get("feeders")
        if isinstance(feeders, list):
            session.feeders = {port for port in feeders if isinstance(port, str) and ":" in port and len(port) <= 256}
            session.feeders = set(sorted(session.feeders)[: cls._MAX_FEEDERS])
        mix_module = str(state.get("mix_module") or "")
        session.mix_module = mix_module if mix_module.isdigit() else ""
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
                if not recorded:
                    # Sunshine records an input (a microphone). Its level is
                    # never restored: replaced by the output's monitor, or kept
                    # silent when there is no monitor to record instead.
                    if plan.monitor:
                        if self.manager.move_capture(capture.index, plan.monitor):
                            notes.append("microphone-replaced")
                    elif not capture.muted and self.manager.mute_capture(capture):
                        notes.append("microphone-muted")
                        _log.warning("[AUDIO] Sunshine's recording #%s records a microphone and no output can replace it; muted.", capture.index)
                    continue
                if capture.silenced and self._level_restores.get(capture.index, 0) < self.MAX_LEVEL_RESTORES:
                    # A saved per-application level (one mute of "sunshine" in a
                    # mixer) is applied to every new recording: the other
                    # computer heard silence while this one played normally.
                    self._level_restores[capture.index] = self._level_restores.get(capture.index, 0) + 1
                    if self.manager.restore_capture_level(capture):
                        notes.append("capture-unmuted")
                        self.restored_level = True
                        _log.warning(
                            "[AUDIO] Sunshine's recording #%s was %s; restored to 100 %% unmuted.",
                            capture.index,
                            "muted" if capture.muted else f"at {round((capture.volume or 0) * 100 / VOLUME_NORM)} %",
                        )
                if recorded == CALL_MIX_SINK:
                    # Our call-free mix; below, it follows the person's output.
                    origin = graph.output(self.capture_origins.get(capture.index, ""))
                    if not self.manual_output and default is not None and default.is_real and origin is not None and origin.is_real and origin.name != default.name:
                        self.capture_origins[capture.index] = default.name
                    continue
                if not self.manual_output and default is not None and default.is_real and recorded != default.name:
                    # The person changed output during the stream: follow it
                    # instead of recording a device nobody listens to.
                    monitor = graph.monitor_of(default.name)
                    if monitor and self.manager.move_capture(capture.index, monitor):
                        notes.append("followed-output")
            if notes:
                graph = self.manager.snapshot() or graph
            graph, call_notes = self._keep_calls_out(graph)
            notes += call_notes
            wanted, refused = desired_bridges(graph, original_sink=self.original_sink, play_on_host=self.play_on_host, origins=self.capture_origins)
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
            if any(o.kind == SUNSHINE for o in graph.outputs):
                self._note_feeders()
            self.notes = tuple(dict.fromkeys(notes))
            self._save()
            live = {c.index for c in graph.sunshine_captures()}
            self._level_restores = {index: count for index, count in self._level_restores.items() if index in live}
            self._log_changes(graph)
            return self.decorate(audio_status(graph, self.manual_output, tuple(self.links)), graph)

    def decorate(self, status: AudioStatus, graph: AudioGraph | None) -> AudioStatus:
        """Add what only this session knows: notes, calls, restored level, the game-only mix."""
        captures = graph.sunshine_captures() if graph is not None else []
        separated = all(graph.recorded_output(c) == CALL_MIX_SINK for c in captures) if graph is not None and captures and self.game is not None else None
        return replace(
            status,
            notes=self.notes,
            calls_kept_out=self.calls_kept_out,
            level_restored=self.restored_level,
            game_only=self.game is not None,
            game_programs=self.game_programs if self.game is not None else (),
            game_separated=separated,
        )

    def allow_level_restore(self) -> None:
        """The person asked for a check (Test audio): restore the recording level again."""
        with self._lock:
            self._level_restores.clear()

    def _log_changes(self, graph: AudioGraph) -> None:
        """One ``[AUDIO]`` line per fact, only when it changes: enough to trace a silent stream."""
        default = graph.output(graph.default_sink)
        programs = sorted({call_program_name(s) for s in graph.playback if not _is_sunshine_process(s)})
        facts = {
            "output": f"Default output: {graph.default_sink or 'none'} ({default.kind if default else 'missing'})",
            "programs": f"Programs playing: {', '.join(programs) if programs else 'none'}",
        }
        if self.game is not None:
            facts["game"] = f"Only the game's sound is sent; game streams: {', '.join(self.game_programs) if self.game_programs else 'none playing'}"
        captures = graph.sunshine_captures()
        if not captures:
            facts["sunshine"] = "Sunshine is not recording (no client playing)"
        for capture in captures:
            recorded = graph.recorded_output(capture)
            level = "muted" if capture.muted else (f"{round(capture.volume * 100 / VOLUME_NORM)} %" if capture.volume is not None else "level unknown")
            what = f"monitor of {recorded}" if recorded else "NOT a monitor"
            facts[f"sunshine-{capture.index}"] = f"Sunshine records #{capture.index}: {capture.device} ({what}), {level}"
        for key in [k for k in self._logged if k not in facts]:
            del self._logged[key]
        for key, text in facts.items():
            if self._logged.get(key) != text:
                self._logged[key] = text
                _log.info("[AUDIO] %s", text)

    def _keep_calls_out(self, graph: AudioGraph) -> tuple[AudioGraph, list[str]]:
        """While a call program plays into what Sunshine records, record the call-free mix.

        Every other program's sound is linked into the mix too, so the other
        computer still hears it; nothing is moved, so this computer hears
        everything as before. Without a call, Sunshine records the output
        again and the mix is removed.
        """
        captures = graph.sunshine_captures()
        origins: dict[str, str] = {}  # capture index -> the output Sunshine chose
        on_mix: set[str] = set()
        for capture in captures:
            recorded = graph.recorded_output(capture)
            if recorded == CALL_MIX_SINK:
                on_mix.add(capture.index)
                # Unknown after adopting a session: the output in use.
                fallback = capture_plan(graph, self.manual_output).output
                recorded = self.capture_origins.get(capture.index) or (fallback.name if fallback else "")
            if recorded and graph.output(recorded) is not None:
                origins[capture.index] = recorded
        # Only a capture in the mix needs its origin remembered.
        self.capture_origins = {index: origin for index, origin in origins.items() if index in on_mix}
        candidates = [s for s in graph.playback if is_call_stream(s)]
        game_only = self.game is not None
        pw = self.manager.pipewire() if origins and (candidates or game_only) else None
        targets = {node for node in (pw.node_named(name) for name in set(origins.values())) if node is not None} if pw is not None else set()

        def reaching(stream: AudioStream) -> bool:
            node = _stream_node(stream)
            return pw is not None and node is not None and any(pw.reaches(node, target) for target in targets)

        calls = [s for s in candidates if reaching(s)]
        game = self._game_streams(graph) if game_only and origins else []
        self.game_programs = tuple(dict.fromkeys(call_program_name(s) for s in game))
        if game_only and origins and pw is None:
            # Never fall back to sending everything on purpose: what Sunshine
            # records stays as it is, and the interface says so.
            self.calls_kept_out = ()
            return graph, ["game-not-separated"]
        if not game_only and (not calls or pw is None):
            self.calls_kept_out = ()
            return self._release_mix(graph, captures, origins), (["calls-unreadable"] if candidates and origins and pw is None else [])
        if not origins or pw is None:
            self.calls_kept_out = ()
            return self._release_mix(graph, captures, origins), []
        if graph.output(CALL_MIX_SINK) is None:
            origin = graph.output(next(iter(origins.values())))
            self.mix_module = self.manager.create_call_mix(self.token, origin.channel_map if origin else "", self.mix_description)
            self._save()
            fresh, fresh_pw = self.manager.snapshot(), self.manager.pipewire()
            if not self.mix_module or fresh is None or fresh_pw is None or fresh.output(CALL_MIX_SINK) is None:
                self.calls_kept_out = ()
                return self._release_mix(fresh or graph, captures, origins), ["game-not-separated" if game_only else "calls-not-separated"]
            graph, pw = fresh, fresh_pw
            targets = {node for node in (pw.node_named(name) for name in set(origins.values())) if node is not None}
        mix = graph.output(CALL_MIX_SINK)
        mix_node = pw.node_named(CALL_MIX_SINK)
        if mix is None or mix.kind != OWNED or mix_node is None:
            self.calls_kept_out = ()
            return graph, ["game-not-separated" if game_only else "calls-not-separated"]  # someone else's output with our name
        inputs = pw.ports_of(mix_node, "input")
        wanted: set[tuple[int, int]] = set()
        game_nodes = {_stream_node(s) for s in game}
        for stream in graph.playback:
            node = _stream_node(stream)
            # Only programs themselves: a node others play into, or half of a
            # loopback or filter chain, forwards sound that is linked already.
            if node is None or is_call_stream(stream) or _is_sunshine_process(stream) or pw.is_fed(node) or pw.link_group(node):
                continue
            # Game Window sends the game wherever it plays; otherwise every
            # program that reaches the recorded output, except calls.
            if (node not in game_nodes) if game_only else not reaching(stream):
                continue
            for channel, port in pw.ports_of(node, "output").items():
                wanted |= {(port, inputs[target]) for target in mix_channels(channel, inputs)}
        into_mix = {link_id: link for link_id, link in pw.links.items() if link.input_node == mix_node}
        present = {(link.output_port, link.input_port) for link in into_mix.values()}
        self.manager.unlink_ids(link_id for link_id, link in into_mix.items() if link.owner == self.token and (link.output_port, link.input_port) not in wanted)
        self.manager.link_port_ids(sorted(wanted - present), self.token)
        notes = []
        for capture in captures:
            if capture.index in origins and graph.recorded_output(capture) != CALL_MIX_SINK:
                if self.manager.move_capture(capture.index, f"{CALL_MIX_SINK}.monitor"):
                    self.capture_origins[capture.index] = origins[capture.index]
                else:
                    notes.append("game-not-separated" if game_only else "calls-not-separated")
        self.calls_kept_out = tuple(dict.fromkeys(call_program_name(s) for s in calls))
        self._save()
        return self.manager.snapshot() or graph, notes

    def _game_streams(self, graph: AudioGraph) -> list[AudioStream]:
        """The game's playback: its process family, or a stream named like the game."""
        scope = self.game
        if scope is None:
            return []
        try:
            family = self.game_family(scope.pid) if scope.pid > 1 else frozenset()
        except Exception as exc:  # /proc unreadable: names still work
            _log.warning("[AUDIO] Could not read the game's processes: %s", exc)
            family = frozenset()
        names = {name.casefold() for name in scope.names if len(name) >= 3}
        found = []
        for stream in graph.playback:
            if _is_sunshine_process(stream) or is_call_stream(stream):
                continue
            pid = int(stream.pid) if stream.pid.isdigit() else 0
            if pid in family or (stream.app and stream.app.casefold() in names):
                found.append(stream)
        return found

    def _release_mix(self, graph: AudioGraph, captures: Iterable[AudioStream], origins: Mapping[str, str]) -> AudioGraph:
        """Put Sunshine back on the output it chose, then remove the mix.

        The mix is removed only when no capture records it any more: a capture
        whose source disappears is moved by the session manager, and that
        choice is not ours to leave to chance.
        """
        moved = False
        on_mix = [c for c in captures if graph.recorded_output(c) == CALL_MIX_SINK]
        for capture in on_mix:
            monitor = graph.monitor_of(origins.get(capture.index, "")) or capture_plan(graph, self.manual_output).monitor
            if monitor and self.manager.move_capture(capture.index, monitor):
                moved = True
        if moved:
            graph = self.manager.snapshot() or graph
        if self.mix_module and not any(graph.recorded_output(c) == CALL_MIX_SINK for c in graph.sunshine_captures()):
            if self.manager.unload_owned_module(self.mix_module, self.token) or graph.output(CALL_MIX_SINK) is None:
                self.mix_module = ""
                self.capture_origins = {}
                self._save()
                graph = self.manager.snapshot() or graph
        return graph

    def remember_feeders(self) -> None:
        """Record who plays into Sunshine's outputs; call right before stopping Sunshine."""
        with self._lock:
            if self._note_feeders():
                self._save()

    def _note_feeders(self) -> bool:
        current = self.manager.links()
        if current is None:
            return False
        owned = [(link["out"], link["in"]) for links in self.links.values() for link in links]
        found = feeder_ports(current, owned) - self.feeders
        if not found:
            return False
        self.feeders |= set(sorted(found)[: max(0, self._MAX_FEEDERS - len(self.feeders))])
        return True

    def end(self, *, sunshine_stopped: bool = True, settle: float | None = None) -> None:
        with self._lock:
            if self.links:
                current = self.manager.links()
                if current is not None:
                    for links in self.links.values():
                        self.manager.unlink_owned(links, current)
                self.links.clear()
            graph = self.manager.snapshot()
            if graph is not None and self.mix_module:
                graph = self._release_mix(graph, graph.sunshine_captures(), self.capture_origins)
            self.calls_kept_out = ()
            if graph is not None:
                self._restore_output(graph, sunshine_stopped)
            if sunshine_stopped and self.feeders:
                self._relink_feeders(self.RELINK_SETTLE_SECONDS if settle is None else settle)
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

    def _relink_feeders(self, settle: float) -> None:
        """Reconnect ports Sunshine's exit left linked to nothing, into the default device.

        A program that follows the default output (JamesDSP) can fail to
        relink when the default changes and Sunshine's output disappears at
        the same moment; then everything played through it is silent.
        """
        deadline = time.monotonic() + settle
        while True:
            orphans = self.manager.unlinked_ports(self.feeders)
            if not orphans:
                return  # every program followed the output by itself
            if time.monotonic() >= deadline:
                break
            time.sleep(0.25)
        graph = self.manager.snapshot(streams=False)
        default = graph.output(graph.default_sink) if graph is not None else None
        if default is None or default.kind not in (HARDWARE, BLUETOOTH):
            # A virtual default usually forwards to the program itself.
            _log.warning("Audio ports left without an output after Sunshine stopped: %s", ", ".join(sorted(orphans)))
            return
        orphans = {port for port in orphans if _node_of(port) != default.name}
        linked = self.manager.relink(orphans, default.name)
        if linked:
            _log.info("Reconnected audio left without an output after Sunshine stopped: %s -> %s", ", ".join(sorted({_node_of(o) for o, _i in linked})), default.name)
        missing = orphans - {o for o, _i in linked}
        if missing:
            _log.warning("Could not reconnect audio ports after Sunshine stopped: %s", ", ".join(sorted(missing)))

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
        if graph is not None and graph.output(CALL_MIX_SINK) is not None and not sunshine_running:
            manager.unload_call_mixes()  # left by a window that crashed with its state
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

    # Default output changes arrive as "server" events. Of the application
    # streams, only a new or closed one matters: a call program starting or
    # ending, or a program to add to the call-free mix. Their volume changes
    # ("change" events) do not affect what Sunshine records.
    RELEVANT = frozenset({"server", "sink", "source", "source-output"})
    STREAMS = frozenset({"sink-input"})
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
                if match and (match.group(2) in self.RELEVANT or (match.group(2) in self.STREAMS and match.group(1) != "change")):
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
