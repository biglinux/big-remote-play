"""Audio discovery, Sunshine capture and owned routing (no real sound server).

A fake PulseAudio server renders ``pactl list`` text in the LC_ALL=C format
of pipewire-pulse and records every write, so each test states exactly which
changes Big Remote Play makes to the sound graph.
"""

from __future__ import annotations

import array
import json
import math
import os
import stat
import subprocess
from dataclasses import dataclass, field

import pytest

from big_remote_play.utils import audio
from big_remote_play.utils.audio import (
    SUNSHINE_STEREO_SINK,
    AudioManager,
    AudioRoutingSession,
    AudioWatcher,
    Bridge,
    audio_status,
    build_graph,
    capture_plan,
    classify_output,
    desired_bridges,
    manual_outputs,
    parse_pactl_list,
    sunshine_audio_sink,
    tone_level_db,
)

REAL_TEST_TONE = AudioManager.test_tone

HDMI = "alsa_output.pci-0000_03_00.1.hdmi-stereo"
USB = "alsa_output.usb-Generic_Headset-00.analog-stereo"
BT = "bluez_output.00_11_22_33_44_55.1"
MIC = "alsa_input.usb-Generic_Headset-00.mono-fallback"
EASY = "easyeffects_sink"
NULL51 = "sink-sunshine-surround51"


@dataclass
class Sink:
    name: str
    description: str
    props: dict = field(default_factory=dict)
    flags: str = "DECIBEL_VOLUME LATENCY"
    channels: tuple = ("FL", "FR")


@dataclass
class Stream:
    target: str
    props: dict


class FakePulse:
    """Just enough of pipewire-pulse for AudioManager."""

    def __init__(self) -> None:
        self.sinks: list[Sink] = [
            Sink(HDMI, "HDMI / DisplayPort", {"device.api": "alsa", "device.bus": "pci"}, "HARDWARE DECIBEL_VOLUME"),
            Sink(USB, "Fone USB — Estéreo analógico", {"device.api": "alsa", "device.bus": "usb"}, "HARDWARE"),
            Sink(BT, "Fone Bluetooth", {"device.api": "bluez5", "device.bus": "bluetooth"}),
            Sink(EASY, "Easy Effects Sink", {"factory.name": "support.null-audio-sink", "node.virtual": "true"}),
        ]
        self.mics = [(MIC, "Microfone do fone")]
        self.fake_monitors: dict[str, str] = {}  # a source *named* like a monitor that is not one
        self.playback: list[Stream] = []
        self.capture: list[Stream] = []
        self.modules: list[tuple[str, str, str]] = [("7", "module-loopback", "source=mic sink=hw latency_msec=20")]  # someone else's
        self.default_sink = HDMI
        self.default_source = MIC
        self.writes: list[list[str]] = []
        self.next_module = 536870912
        self.links: dict[str, tuple[str, str]] = {"90": ("mic:capture_FL", "hw:playback_FL")}  # someone else's
        self.next_link = 300
        self.fail_list = False
        self.extra_outputs: list[str] = []  # output ports of programs, e.g. an effects filter

    # -- helpers for tests -------------------------------------------------
    def sunshine_starts_session(self, *, host_audio: bool, surround: bool = False, audio_sink: str | None = None) -> None:
        """What Sunshine does on Linux (measured on PipeWire 1.6, see docs/audio-testing.md)."""
        for name in (SUNSHINE_STEREO_SINK, NULL51):
            if not any(s.name == name for s in self.sinks):
                channels = ("FL", "FR", "FC", "LFE", "RL", "RR") if name == NULL51 else ("FL", "FR")
                self.sinks.append(Sink(name, name, {"factory.name": "support.null-audio-sink"}, channels=channels))
        null = NULL51 if surround else SUNSHINE_STEREO_SINK
        recorded = audio_sink or (self.default_sink if host_audio else null)
        if not host_audio:
            self.default_sink = null  # the client's request decides the default output
        elif audio_sink:
            self.default_sink = audio_sink
        self.capture.append(Stream(f"{recorded}.monitor", {"application.name": "sunshine", "application.process.binary": "sunshine", "media.name": "sunshine-record"}))

    def sunshine_ends_session(self, restore_to: str) -> None:
        self.capture = [c for c in self.capture if c.props.get("application.process.binary") != "sunshine"]
        self.default_sink = restore_to

    def sunshine_exits(self, restore_to: str) -> None:
        """Sunshine quits: its outputs and every link to their ports disappear."""
        self.sunshine_ends_session(restore_to)
        self.sinks = [s for s in self.sinks if not s.name.startswith("sink-sunshine-")]
        self.links = {k: (o, i) for k, (o, i) in self.links.items() if not (o.startswith("sink-sunshine-") or i.startswith("sink-sunshine-"))}

    def effects_program_plays_into(self, sink: str, node: str = "jdsp_@PwJamesDspPlugin_JamesDsp") -> None:
        """A filter like JamesDSP that links its own output to the default output."""
        for channel in ("FL", "FR"):
            port = f"{node}:output_{channel}"
            if port not in self.extra_outputs:
                self.extra_outputs.append(port)
            self.links = {k: v for k, v in self.links.items() if v[0] != port}
            self.next_link += 1
            self.links[str(self.next_link)] = (port, f"{sink}:playback_{channel}")

    def sources(self) -> list[tuple[str, str, str]]:
        found = [(f"{s.name}.monitor", f"Monitor of {s.description}", s.name) for s in self.sinks]
        found += [(name, description, "") for name, description in self.mics]
        found += [(name, "Not a monitor", "") for name, _desc in self.fake_monitors.items()]
        return found

    # -- rendering ---------------------------------------------------------
    def _render_sinks(self) -> str:
        out = []
        for index, sink in enumerate(self.sinks, start=40):
            props = "".join(f'\t\t{k} = "{v}"\n' for k, v in {"node.name": sink.name, **sink.props}.items())
            out.append(
                f"Sink #{index}\n\tState: RUNNING\n\tName: {sink.name}\n\tDescription: {sink.description}\n\tDriver: PipeWire\n\tMonitor Source: {sink.name}.monitor\n\tFlags: {sink.flags}\n\tProperties:\n{props}\tFormats:\n\t\tpcm\n"
            )
        return "\n".join(out)

    def _render_sources(self) -> str:
        out = []
        for index, (name, description, monitor_of) in enumerate(self.sources(), start=100):
            out.append(f'Source #{index}\n\tState: RUNNING\n\tName: {name}\n\tDescription: {description}\n\tMonitor of Sink: {monitor_of or "n/a"}\n\tProperties:\n\t\tnode.name = "{name}"\n')
        return "\n".join(out)

    def _render_streams(self, kind: str, streams: list[Stream], key: str) -> str:
        out = []
        for index, stream in enumerate(streams, start=500 if kind == "Sink Input" else 800):
            props = "".join(f'\t\t{k} = "{v}"\n' for k, v in stream.props.items())
            out.append(f"{kind} #{index}\n\tDriver: PipeWire\n\t{key}: {stream.target}\n\tProperties:\n{props}")
        return "\n".join(out)

    def _pw_link(self, argv):
        args = argv[1:]
        ok = lambda out="": subprocess.CompletedProcess(argv, 0, out, "")  # noqa: E731
        if args == ["-o"]:
            return ok("\n".join([f"{s.name}:monitor_{c}" for s in self.sinks for c in s.channels] + self.extra_outputs) + "\n")
        if args == ["-i"]:
            return ok("\n".join(f"{s.name}:playback_{c}" for s in self.sinks for c in s.channels) + "\n")
        if args == ["-l", "-I"]:
            lines = []
            for index, (link_id, (out, inp)) in enumerate(self.links.items()):
                lines += [f" {1000 + 2 * index} {out}", f" {link_id}   |->  {1001 + 2 * index} {inp}", f" {1001 + 2 * index} {inp}", f" {link_id}   |<-  {1000 + 2 * index} {out}"]
            return ok("\n".join(lines) + "\n")
        self.writes.append(list(argv))
        if args[0] == "-p":
            props = json.loads(args[1])
            assert props.get("big-remote-play.owner")
            self.next_link += 1
            self.links[str(self.next_link)] = (args[2], args[3])
            return ok()
        if args[0] == "-d":
            pair = (args[1], args[2])
            self.links = {k: v for k, v in self.links.items() if v != pair}
            return ok()
        if len(args) == 2 and not args[0].startswith("-"):
            if (args[0], args[1]) in self.links.values():
                return subprocess.CompletedProcess(argv, 1, "", "failed to link ports: File exists")
            self.next_link += 1
            self.links[str(self.next_link)] = (args[0], args[1])
            return ok()
        raise AssertionError(f"unexpected pw-link call {argv}")

    def __call__(self, argv, **kwargs):
        assert kwargs.get("env", {}).get("LC_ALL") == "C"
        if argv[0] == "pw-link":
            return self._pw_link(argv)
        assert argv[0] == "pactl", argv
        args = argv[1:]
        ok = lambda out="": subprocess.CompletedProcess(argv, 0, out, "")  # noqa: E731
        if args[:1] == ["list"]:
            if self.fail_list:
                return subprocess.CompletedProcess(argv, 1, "", "Connection failure")
            what = " ".join(args[1:])
            return ok(
                {
                    "sinks": self._render_sinks(),
                    "sources": self._render_sources(),
                    "sink-inputs": self._render_streams("Sink Input", self.playback, "Sink"),
                    "source-outputs": self._render_streams("Source Output", self.capture, "Source"),
                    "short modules": "".join(f"{i}\t{n}\t{a}\t\n" for i, n, a in self.modules),
                }[what]
            )
        if args == ["get-default-sink"]:
            return ok(self.default_sink + "\n")
        if args == ["get-default-source"]:
            return ok(self.default_source + "\n")
        self.writes.append(list(args))
        if args[0] == "set-default-sink":
            self.default_sink = args[1]
            return ok()
        if args[0] == "load-module":
            self.next_module += 1
            self.modules.append((str(self.next_module), args[1], " ".join(args[2:])))
            return ok(f"{self.next_module}\n")
        if args[0] == "unload-module":
            before = len(self.modules)
            self.modules = [m for m in self.modules if m[0] != args[1]]
            return ok() if len(self.modules) < before else subprocess.CompletedProcess(argv, 1, "", "Failure: No such entity")
        if args[0] == "move-source-output":
            index = int(args[1]) - 800
            self.capture[index].target = args[2]
            return ok()
        raise AssertionError(f"unexpected pactl call {argv}")

    def bridges(self) -> list[tuple[str, str]]:
        """Bridged (monitor source, target output) pairs, from the port links."""
        found = []
        for out, inp in self.links.values():
            if ":monitor_" in out:
                pair = (out.split(":")[0] + ".monitor", inp.split(":")[0])
                if pair not in found:
                    found.append(pair)
        return found


@pytest.fixture
def pulse():
    return FakePulse()


@pytest.fixture
def manager(pulse):
    return AudioManager(runner=pulse)


def session_for(manager, tmp_path, **kwargs):
    return AudioRoutingSession(manager, state_path=tmp_path / "audio-session.json", **kwargs)


# ---------------------------------------------------------------- discovery


def test_parser_reads_fields_properties_and_non_ascii_descriptions(pulse):
    items = parse_pactl_list(pulse._render_sinks())
    usb = next(i for i in items if i["fields"]["Name"] == USB)
    assert usb["kind"] == "Sink"
    assert usb["fields"]["Description"] == "Fone USB — Estéreo analógico"
    assert usb["properties"]["device.bus"] == "usb"
    assert usb["fields"]["Monitor Source"] == f"{USB}.monitor"


def test_outputs_are_classified_from_properties_not_only_names():
    assert classify_output(HDMI, "HDMI", {"device.api": "alsa"}) == "hardware"
    assert classify_output("x", "USB DAC", {}, "HARDWARE DECIBEL_VOLUME") == "hardware"
    assert classify_output(BT, "Headphones", {"device.api": "bluez5"}) == "bluetooth"
    assert classify_output(EASY, "Easy Effects Sink", {"node.virtual": "true"}) == "virtual"
    assert classify_output("jamesdsp_sink", "JamesDSP Sink", {"factory.name": "support.null-audio-sink"}) == "virtual"
    assert classify_output("sink-sunshine-stereo", "", {}) == "sunshine"
    assert classify_output("SunshineGameSink", "", {}) == "legacy"
    assert classify_output("steam_process", "", {"steam.autounload": "true"}) == "steam"
    assert classify_output("Steam Streaming Playback", "", {}) == "steam"
    assert classify_output("bridge", "", {"big-remote-play.owner": "abc"}) == "owned"


@pytest.mark.parametrize("default", [HDMI, USB, BT, EASY])
def test_automatic_records_the_verified_monitor_of_the_current_output(pulse, manager, default):
    pulse.default_sink = default
    plan = capture_plan(manager.snapshot())
    assert plan.available
    assert plan.output.name == default
    assert plan.monitor == f"{default}.monitor"


def test_a_source_named_like_a_monitor_is_not_trusted():
    """Appending ".monitor" is a guess; only "Monitor of Sink" proves it."""
    sinks = "Sink #1\n\tName: odd\n\tDescription: Odd output\n\tMonitor Source: odd.monitor\n\tFlags: HARDWARE\n"
    # A microphone that happens to carry the guessed name.
    sources = "Source #2\n\tName: odd.monitor\n\tDescription: USB microphone\n\tMonitor of Sink: n/a\n"
    graph = build_graph(sinks, sources, "", "", "", "odd", "odd.monitor")
    plan = capture_plan(graph)
    assert plan.monitor is None and plan.problem == "no-monitor"
    assert [s.name for s in graph.microphones()] == ["odd.monitor"]


def test_system_audio_is_unavailable_instead_of_falling_back_to_a_microphone(pulse, manager):
    pulse.sinks = []
    graph = manager.snapshot()
    plan = capture_plan(graph)
    assert not plan.available and plan.problem == "no-output"
    assert plan.monitor is None
    assert graph.microphones()  # a microphone exists, and is still not used
    assert capture_plan(None).problem == "no-sound-server"


def test_manual_choice_offers_devices_only_and_never_virtual_or_sunshine_outputs(pulse, manager):
    pulse.sunshine_starts_session(host_audio=True)
    pulse.sinks.append(Sink("SunshineGameSink", "SunshineGameSink"))
    names = [o.name for o in manual_outputs(manager.snapshot())]
    assert names == [HDMI, USB, BT]
    assert capture_plan(manager.snapshot(), EASY).problem == "not-selectable"
    assert capture_plan(manager.snapshot(), "gone").problem == "missing-output"
    assert [d["name"] for d in manager.get_passive_sinks()] == [HDMI, USB, BT]


def test_unreadable_sound_server_is_reported_not_treated_as_empty(pulse, manager):
    pulse.fail_list = True
    assert manager.snapshot() is None
    assert manager.get_passive_sinks() == []


# ------------------------------------------------------ sunshine.conf value


def test_sunshine_audio_sink_never_names_a_microphone_or_a_default_source():
    """Regression: Sunshine must record an output monitor, never @DEFAULT_AUDIO_SOURCE@."""
    values = {sunshine_audio_sink("", True), sunshine_audio_sink("", False), sunshine_audio_sink(USB, True), sunshine_audio_sink(USB, False)}
    assert values == {None, SUNSHINE_STEREO_SINK, USB}
    for value in values - {None}:
        assert "@DEFAULT" not in value and "alsa_input" not in value and not value.endswith(".monitor")


def test_automatic_leaves_audio_sink_unset_so_sunshine_follows_the_current_output():
    assert sunshine_audio_sink("", play_on_host=True) is None


# ---------------------------------------------------------------- bridges


def graph_after(pulse, manager, **session):
    pulse.sunshine_starts_session(**session)
    return manager.snapshot()


def test_client_that_mutes_the_host_is_bridged_back_when_host_playback_is_wanted(pulse, manager):
    graph = graph_after(pulse, manager, host_audio=False)
    assert graph.default_sink == SUNSHINE_STEREO_SINK  # what Sunshine does
    bridges, notes = desired_bridges(graph, original_sink=HDMI, play_on_host=True)
    assert bridges == [Bridge(SUNSHINE_STEREO_SINK, HDMI, "client-muted-host")]
    assert not notes


def test_client_only_mode_adds_nothing(pulse, manager):
    graph = graph_after(pulse, manager, host_audio=False)
    assert desired_bridges(graph, original_sink=HDMI, play_on_host=False) == ([], [])


def test_client_that_keeps_host_audio_needs_no_routing(pulse, manager):
    graph = graph_after(pulse, manager, host_audio=True)
    assert desired_bridges(graph, original_sink=HDMI, play_on_host=True) == ([], [])


def test_no_loopback_into_a_virtual_output_that_would_feed_itself(pulse, manager):
    """EasyEffects/JamesDSP forward to the default output, which Sunshine just
    made its own virtual sink: a loopback into them would be a cycle."""
    pulse.default_sink = EASY
    graph = graph_after(pulse, manager, host_audio=False)
    bridges, notes = desired_bridges(graph, original_sink=EASY, play_on_host=True)
    assert bridges == [] and notes == ["unsafe-target"]


def test_explicit_output_with_a_host_muting_client_bridges_into_the_recorded_output(pulse, manager):
    """Measured: with audio_sink set, Sunshine still switches the default to its
    null sink for such a client, but keeps recording audio_sink -> silence."""
    graph = graph_after(pulse, manager, host_audio=False, audio_sink=USB)
    assert graph.default_sink == SUNSHINE_STEREO_SINK
    assert graph.recorded_output(graph.sunshine_captures()[0]) == USB
    bridges, _ = desired_bridges(graph, original_sink=HDMI, play_on_host=True)
    assert bridges == [Bridge(SUNSHINE_STEREO_SINK, USB, "sunshine-switched-output")]


def test_client_only_with_surround_bridges_surround_into_the_recorded_stereo_output(pulse, manager):
    graph = graph_after(pulse, manager, host_audio=False, surround=True, audio_sink=SUNSHINE_STEREO_SINK)
    assert graph.default_sink == NULL51
    bridges, _ = desired_bridges(graph, original_sink=HDMI, play_on_host=False)
    assert bridges == [Bridge(NULL51, SUNSHINE_STEREO_SINK, "sunshine-switched-output")]


def test_bridges_never_start_at_a_microphone_and_never_form_a_cycle(pulse, manager):
    for kwargs, play in (({"host_audio": False}, True), ({"host_audio": False, "audio_sink": USB}, True), ({"host_audio": False, "surround": True, "audio_sink": SUNSHINE_STEREO_SINK}, False)):
        pulse = FakePulse()
        graph = graph_after(pulse, AudioManager(runner=pulse), **kwargs)
        bridges, _ = desired_bridges(graph, original_sink=HDMI, play_on_host=play)
        microphones = {s.name for s in graph.microphones()}
        for bridge in bridges:
            assert bridge.source not in microphones
            assert graph.source(bridge.source).monitor_of == bridge.source_sink
            assert bridge.source_sink != bridge.target_sink
    assert audio._creates_cycle(Bridge("a", "b", ""), [Bridge("a", "b", ""), Bridge("b", "a", "")])
    assert not audio._creates_cycle(Bridge("a", "b", ""), [Bridge("a", "b", "")])


# ------------------------------------------------------------ the session


def test_automatic_session_makes_no_writes_to_the_sound_server(pulse, manager, tmp_path):
    session = session_for(manager, tmp_path)
    session.begin(manager.snapshot())
    pulse.sunshine_starts_session(host_audio=True)
    status = session.reconcile()
    assert pulse.writes == []
    assert status.sunshine_source == f"{HDMI}.monitor" and status.sunshine_records_monitor
    assert status.microphone_sent is False
    pulse.sunshine_ends_session(HDMI)
    session.end()
    assert pulse.writes == []
    assert not (tmp_path / "audio-session.json").exists()


def test_session_adds_one_owned_bridge_and_removes_only_its_own(pulse, manager, tmp_path):
    session = session_for(manager, tmp_path, play_on_host=True)
    session.begin(manager.snapshot())
    pulse.sunshine_starts_session(host_audio=False)
    session.reconcile()
    session.reconcile()  # idempotent
    assert pulse.bridges() == [(f"{SUNSHINE_STEREO_SINK}.monitor", HDMI)]
    assert sorted(pulse.links.values())[-2:] == [(f"{SUNSHINE_STEREO_SINK}:monitor_FL", f"{HDMI}:playback_FL"), (f"{SUNSHINE_STEREO_SINK}:monitor_FR", f"{HDMI}:playback_FR")]
    created = [w for w in pulse.writes if w[:2] == ["pw-link", "-p"]]
    assert len(created) == 2 and all(json.loads(w[2])["big-remote-play.owner"] == session.token for w in created)
    # Port links, not streams: nothing an effects program could capture, and
    # no module, application stream or default output was touched.
    assert not [w for w in pulse.writes if w[0] in ("load-module", "move-sink-input", "set-default-sink")]
    pulse.sunshine_ends_session(HDMI)
    session.reconcile()
    assert pulse.bridges() == []
    assert pulse.links == {"90": ("mic:capture_FL", "hw:playback_FL")}  # someone else's link stays
    assert ("7", "module-loopback", "source=mic sink=hw latency_msec=20") in pulse.modules


def test_a_link_someone_else_made_between_the_same_ports_is_not_removed(pulse, manager, tmp_path):
    session = session_for(manager, tmp_path)
    session.begin(manager.snapshot())
    pulse.sunshine_starts_session(host_audio=False)
    session.reconcile()
    # Our links vanish (e.g. the device was unplugged) and another program
    # links the same ports; the recorded ids no longer match.
    ours = dict(pulse.links)
    pulse.links = {"90": ours["90"], "777": (f"{SUNSHINE_STEREO_SINK}:monitor_FL", f"{HDMI}:playback_FL")}
    session.end()
    assert "777" in pulse.links
    assert not [w for w in pulse.writes if w[:2] == ["pw-link", "-d"]]


def test_surround_into_stereo_keeps_the_front_channels(pulse, manager, tmp_path):
    session = session_for(manager, tmp_path, play_on_host=False)
    session.begin(manager.snapshot())
    pulse.sunshine_starts_session(host_audio=False, surround=True, audio_sink=SUNSHINE_STEREO_SINK)
    session.reconcile()
    linked = sorted(v for k, v in pulse.links.items() if k != "90")
    assert linked == [(f"{NULL51}:monitor_FL", f"{SUNSHINE_STEREO_SINK}:playback_FL"), (f"{NULL51}:monitor_FR", f"{SUNSHINE_STEREO_SINK}:playback_FR")]


def test_after_a_sunshine_crash_the_original_output_is_restored(pulse, manager, tmp_path):
    session = session_for(manager, tmp_path)
    session.begin(manager.snapshot())
    pulse.sunshine_starts_session(host_audio=False)
    pulse.capture.clear()  # Sunshine died: its null sink stays the default
    session.end(sunshine_stopped=True)
    assert pulse.default_sink == HDMI
    assert ["set-default-sink", HDMI] in pulse.writes


def test_ending_never_overrides_an_output_the_person_chose(pulse, manager, tmp_path):
    session = session_for(manager, tmp_path)
    session.begin(manager.snapshot())
    pulse.default_sink = USB  # the person switched to the headset
    session.end()
    assert pulse.default_sink == USB
    assert pulse.writes == []


def test_sunshine_recording_a_microphone_is_moved_back_to_the_output_monitor(pulse, manager, tmp_path):
    session = session_for(manager, tmp_path)
    session.begin(manager.snapshot())
    pulse.capture.append(Stream(MIC, {"application.process.binary": "sunshine", "application.name": "sunshine", "media.name": "sunshine-record"}))
    before = audio_status(manager.snapshot())
    assert before.microphone_sent is True
    status = session.reconcile()
    assert ["move-source-output", "800", f"{HDMI}.monitor"] in pulse.writes
    assert status.microphone_sent is False and "microphone-replaced" in status.notes


def test_capture_follows_an_output_change_made_during_the_stream(pulse, manager, tmp_path):
    session = session_for(manager, tmp_path)
    session.begin(manager.snapshot())
    pulse.sunshine_starts_session(host_audio=True)
    pulse.default_sink = USB  # the person plugged in and chose the headset
    status = session.reconcile()
    assert ["move-source-output", "800", f"{USB}.monitor"] in pulse.writes
    assert status.sunshine_source == f"{USB}.monitor"
    assert session.original_sink == USB


def test_an_explicitly_chosen_output_is_not_followed(pulse, manager, tmp_path):
    session = session_for(manager, tmp_path, manual_output=HDMI)
    session.begin(manager.snapshot())
    pulse.sunshine_starts_session(host_audio=True, audio_sink=HDMI)
    pulse.default_sink = USB
    session.reconcile()
    assert not any(w[0] == "move-source-output" for w in pulse.writes)


def test_other_programs_capturing_the_output_are_left_alone(pulse, manager, tmp_path):
    """Steam Remote Play reroutes a game into its own combine sink; nothing we
    do may move that stream back (the 2.x enforcer did, every second)."""
    pulse.sinks.append(Sink("steam_rec", "steam_rec", {"steam.autounload": "true"}))
    pulse.playback.append(Stream("steam_rec", {"application.name": "Game", "application.process.binary": "game.exe"}))
    pulse.capture.append(Stream("steam_rec.monitor", {"application.name": "Steam", "application.process.binary": "steam"}))
    session = session_for(manager, tmp_path)
    session.begin(manager.snapshot())
    pulse.sunshine_starts_session(host_audio=True)
    status = session.reconcile()
    assert pulse.writes == []
    assert status.steam_sources == (("steam_rec.monitor", True),)


def test_state_file_is_private_and_holds_no_stream_ids(pulse, manager, tmp_path):
    session = session_for(manager, tmp_path)
    session.begin(manager.snapshot())
    pulse.sunshine_starts_session(host_audio=False)
    session.reconcile()
    path = tmp_path / "audio-session.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    state = json.loads(path.read_text())
    assert set(state) == {"owner_pid", "token", "original_sink", "manual_output", "play_on_host", "bridges", "feeders", "mix_module"}
    assert state["original_sink"] == HDMI and len(state["bridges"]) == 1 and len(state["bridges"][0]["links"]) == 2


def test_recovery_removes_what_a_crashed_window_left_and_restores_the_output(pulse, manager, tmp_path):
    session = session_for(manager, tmp_path)
    session.begin(manager.snapshot())
    pulse.sunshine_starts_session(host_audio=False)
    session.reconcile()
    state = json.loads((tmp_path / "audio-session.json").read_text())
    state["owner_pid"] = 2**22 + 1  # no such process
    (tmp_path / "audio-session.json").write_text(json.dumps(state))
    pulse.capture.clear()  # Sunshine is gone too
    assert AudioRoutingSession.recover(manager, sunshine_running=False, state_path=tmp_path / "audio-session.json") is None
    assert pulse.bridges() == []
    assert pulse.default_sink == HDMI
    assert not (tmp_path / "audio-session.json").exists()


def test_recovery_adopts_a_session_whose_sharing_is_still_running(pulse, manager, tmp_path):
    session = session_for(manager, tmp_path)
    session.begin(manager.snapshot())
    pulse.sunshine_starts_session(host_audio=False)
    session.reconcile()
    state = json.loads((tmp_path / "audio-session.json").read_text())
    state["owner_pid"] = 2**22 + 1
    (tmp_path / "audio-session.json").write_text(json.dumps(state))
    adopted = AudioRoutingSession.recover(manager, sunshine_running=True, state_path=tmp_path / "audio-session.json")
    assert adopted is not None and adopted.token == session.token
    assert pulse.bridges() == [(f"{SUNSHINE_STEREO_SINK}.monitor", HDMI)]
    assert json.loads((tmp_path / "audio-session.json").read_text())["owner_pid"] == os.getpid()


def test_recovery_removes_legacy_routing_left_by_old_versions_only_when_not_sharing(pulse, manager, tmp_path):
    pulse.modules += [
        ("40", "module-null-sink", "sink_name=SunshineGameSink sink_properties=device.description=SunshineGameSink"),
        ("41", "module-loopback", "source=SunshineGameSink.monitor sink=hw latency_msec=60"),
    ]
    AudioRoutingSession.recover(manager, sunshine_running=True, state_path=tmp_path / "none.json")
    assert len(pulse.modules) == 3
    AudioRoutingSession.recover(manager, sunshine_running=False, state_path=tmp_path / "none.json")
    assert [m[0] for m in pulse.modules] == ["7"]


def test_a_corrupt_state_file_is_ignored(manager, tmp_path):
    (tmp_path / "audio-session.json").write_text('{"token": "../../etc", "bridges": [{"links": [{"id": "1; rm"}]}]}')
    assert AudioRoutingSession.load(manager, tmp_path / "audio-session.json") is None
    (tmp_path / "audio-session.json").write_text(
        json.dumps({"token": "0123456789abcdef", "owner_pid": "x", "bridges": [{"source": "a", "target": "b", "links": [{"id": "1; rm", "out": "a", "in": "b"}]}]})
    )
    session = AudioRoutingSession.load(manager, tmp_path / "audio-session.json")
    assert session is not None and session.links == {}


# -------------------------------------------- programs left unlinked on exit

JDSP_FL, JDSP_FR = "jdsp_@PwJamesDspPlugin_JamesDsp:output_FL", "jdsp_@PwJamesDspPlugin_JamesDsp:output_FR"


def muted_session_with_effects(pulse, manager, tmp_path):
    session = session_for(manager, tmp_path, play_on_host=False)
    session.begin(manager.snapshot())
    pulse.sunshine_starts_session(host_audio=False)
    pulse.effects_program_plays_into(SUNSHINE_STEREO_SINK)  # it follows the default output
    session.reconcile()
    return session


def test_an_effects_program_left_unlinked_by_sunshine_exit_is_reconnected(pulse, manager, tmp_path):
    session = muted_session_with_effects(pulse, manager, tmp_path)
    assert json.loads((tmp_path / "audio-session.json").read_text())["feeders"] == [JDSP_FL, JDSP_FR]
    # Measured on 2026-09-29: JamesDSP missed the change when the default
    # went back and Sunshine's output vanished at the same moment.
    pulse.sunshine_exits(HDMI)
    session.end(settle=0)
    assert (JDSP_FL, f"{HDMI}:playback_FL") in pulse.links.values()
    assert (JDSP_FR, f"{HDMI}:playback_FR") in pulse.links.values()
    relinks = [w for w in pulse.writes if w[0] == "pw-link" and w[1] in (JDSP_FL, JDSP_FR)]
    assert relinks == [["pw-link", JDSP_FL, f"{HDMI}:playback_FL"], ["pw-link", JDSP_FR, f"{HDMI}:playback_FR"]]  # not tagged as ours
    assert not [w for w in pulse.writes if w[:2] == ["pw-link", "-d"] and w[2].startswith("jdsp")]
    assert not (tmp_path / "audio-session.json").exists()


def test_a_program_that_followed_the_output_by_itself_is_left_alone(pulse, manager, tmp_path):
    session = muted_session_with_effects(pulse, manager, tmp_path)
    pulse.sunshine_exits(HDMI)
    pulse.effects_program_plays_into(HDMI)
    writes = len(pulse.writes)
    session.end(settle=0)
    assert pulse.writes[writes:] == []


def test_the_program_gets_time_to_relink_itself_first(pulse, manager, tmp_path, monkeypatch):
    session = muted_session_with_effects(pulse, manager, tmp_path)
    pulse.sunshine_exits(HDMI)
    waits = []

    def sleep(seconds):
        waits.append(seconds)
        pulse.effects_program_plays_into(HDMI)

    monkeypatch.setattr(audio.time, "sleep", sleep)
    writes = len(pulse.writes)
    session.end(settle=5)
    assert waits and pulse.writes[writes:] == []


def test_nothing_is_linked_into_a_virtual_default_output(pulse, manager, tmp_path):
    # EasyEffects' own sink as default: linking a filter into it could feed it back into itself.
    session = muted_session_with_effects(pulse, manager, tmp_path)
    pulse.sunshine_exits(EASY)
    session.end(settle=0)
    assert not any(o in (JDSP_FL, JDSP_FR) for o, _i in pulse.links.values())


def test_a_mono_device_gets_the_front_channels(pulse, manager, tmp_path):
    pulse.sinks.append(Sink("alsa_output.usb-Mono-00.mono", "Mono speaker", {"device.api": "alsa"}, "HARDWARE", channels=("MONO",)))
    session = muted_session_with_effects(pulse, manager, tmp_path)
    pulse.sunshine_exits("alsa_output.usb-Mono-00.mono")
    session.end(settle=0)
    assert {(JDSP_FL, "alsa_output.usb-Mono-00.mono:playback_MONO"), (JDSP_FR, "alsa_output.usb-Mono-00.mono:playback_MONO")} <= set(pulse.links.values())


def test_nothing_is_relinked_while_sunshine_still_runs(pulse, manager, tmp_path):
    session = muted_session_with_effects(pulse, manager, tmp_path)
    pulse.links = {k: v for k, v in pulse.links.items() if v[0] not in (JDSP_FL, JDSP_FR)}
    session.end(sunshine_stopped=False, settle=0)
    assert not any(o in (JDSP_FL, JDSP_FR) for o, _i in pulse.links.values())


def test_feeders_are_programs_not_sunshine_nor_our_bridges():
    links = {
        "1": (JDSP_FL, f"{SUNSHINE_STEREO_SINK}:playback_FL"),
        "2": (f"{NULL51}:monitor_FL", f"{SUNSHINE_STEREO_SINK}:playback_FL"),  # Sunshine into Sunshine
        "3": (f"{HDMI}:monitor_FL", f"{SUNSHINE_STEREO_SINK}:playback_FL"),  # our bridge
        "4": ("firefox:output_FL", f"{HDMI}:playback_FL"),  # not into Sunshine
    }
    assert audio.feeder_ports(links, owned=[links["3"]]) == {JDSP_FL}


def test_recovery_after_a_crash_reconnects_the_recorded_programs(pulse, manager, tmp_path, monkeypatch):
    muted_session_with_effects(pulse, manager, tmp_path)
    state = json.loads((tmp_path / "audio-session.json").read_text())
    state["owner_pid"] = 2**22 + 1
    (tmp_path / "audio-session.json").write_text(json.dumps(state))
    pulse.sunshine_exits(HDMI)
    monkeypatch.setattr(AudioRoutingSession, "RELINK_SETTLE_SECONDS", 0)
    assert AudioRoutingSession.recover(manager, sunshine_running=False, state_path=tmp_path / "audio-session.json") is None
    assert (JDSP_FL, f"{HDMI}:playback_FL") in pulse.links.values()


def test_recorded_ports_from_a_corrupt_state_are_filtered(manager, tmp_path):
    (tmp_path / "audio-session.json").write_text(json.dumps({"token": "0123456789abcdef", "feeders": ["ok:output_FL", 7, "no-colon", "x:" + "y" * 300]}))
    session = AudioRoutingSession.load(manager, tmp_path / "audio-session.json")
    assert session is not None and session.feeders == {"ok:output_FL"}


# ----------------------------------------------------------------- watcher


class FakeSubscribe:
    def __init__(self, lines):
        self.stdout = lines

    def poll(self):
        return 0


def test_watcher_reacts_to_default_and_capture_events_but_not_application_streams():
    import threading
    import time

    calls = []
    release = threading.Event()

    def events():
        yield from ["Event 'change' on sink-input #512\n"] * 20
        release.wait(2)
        yield "Event 'change' on server #4294967295\n"

    watcher = AudioWatcher(lambda: calls.append(time.monotonic()), debounce=0.05, safety_interval=30, spawn=lambda: FakeSubscribe(events()))
    watcher.start()
    time.sleep(0.4)
    assert len(calls) == 1  # the initial check only: application streams do not count
    release.set()
    deadline = time.monotonic() + 2
    while len(calls) < 2 and time.monotonic() < deadline:
        time.sleep(0.02)
    watcher.stop()
    assert len(calls) == 2


def test_watcher_event_filter():
    match = AudioWatcher._EVENT.match
    assert match("Event 'change' on server #1").group(2) in AudioWatcher.RELEVANT
    assert match("Event 'new' on source-output #3").group(2) in AudioWatcher.RELEVANT
    assert match("Event 'change' on sink-input #3").group(2) not in AudioWatcher.RELEVANT


# -------------------------------------------------------------- test tone


def tone(frequency, seconds=0.5, amplitude=0.12, rate=48_000):
    data = array.array("h")
    for i in range(int(rate * seconds)):
        v = int(amplitude * 32767 * math.sin(2 * math.pi * frequency * i / rate))
        data.extend((v, v))
    return data.tobytes()


def test_tone_level_measures_only_the_test_frequency():
    assert tone_level_db(tone(660), 660, 48_000) == pytest.approx(20 * math.log10(0.12), abs=1.0)
    assert tone_level_db(tone(1000), 660, 48_000) < -40
    assert tone_level_db(b"", 660, 48_000) is None


def test_test_tone_plays_the_generated_tone_and_measures_the_recorded_monitor(pulse, monkeypatch):
    monkeypatch.setattr(AudioManager, "test_tone", REAL_TEST_TONE)
    played = []

    def runner(argv, **kwargs):
        if argv[0] == "paplay":
            played.append((argv, len(kwargs["input"])))
            return subprocess.CompletedProcess(argv, 0, b"", b"")
        return pulse(argv, **kwargs)

    class Recorder:
        def __init__(self, argv, **_kwargs):
            self.argv = argv
            import io

            self.stdout = io.BytesIO(tone(660, 1.0))

        def terminate(self):
            pass

        def wait(self, _timeout=None):
            return 0

    recorders = []
    monkeypatch.setattr(audio.subprocess, "Popen", lambda argv, **kw: recorders.append(Recorder(argv)) or recorders[-1])
    monkeypatch.setattr(audio.time, "sleep", lambda _s: None)
    result = AudioManager(runner=runner).test_tone()
    assert result["played"] and result["detected"]
    assert result["monitor"] == f"{HDMI}.monitor"
    assert f"--device={HDMI}.monitor" in recorders[0].argv
    assert not any(a.startswith("--device=") for a in played[0][0])  # automatic: the current output
    assert pulse.writes == []
