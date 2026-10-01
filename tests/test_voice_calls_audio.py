"""Voice calls on the game computer stay out of the stream (no real sound server).

A call program plays everyone's voice, the person connecting included; sent
back through Sunshine, they heard themselves. The fake below adds PipeWire's
own view (``pw-dump``, links by port id) to the fake PulseAudio server of
test_audio, so each test states exactly what Big Remote Play changes.
"""

from __future__ import annotations

import json
import re
import subprocess
import threading
import time

from test_audio import EASY, HDMI, SUNSHINE_STEREO_SINK, FakePulse, FakeSubscribe, Sink, Stream

from big_remote_play.utils.audio import (
    CALL_MIX_SINK,
    AudioManager,
    AudioRoutingSession,
    AudioStream,
    AudioWatcher,
    Bridge,
    desired_bridges,
    is_call_stream,
    mix_channels,
    parse_pw_dump,
)

CALL = {"application.name": "Fluxer", "application.process.binary": "fluxer", "pipewire.access.portal.app_id": "app.fluxer.Fluxer", "node.name": "Fluxer", "object.id": "151"}
GAME = {"application.name": "Game", "application.process.binary": "game", "node.name": "game", "object.id": "160"}
MUSIC = {"application.name": "Music", "application.process.binary": "music", "node.name": "music", "object.id": "170"}


class FakePipeWire(FakePulse):
    """FakePulse plus pw-dump and links by port id, derived from the same model."""

    def __init__(self) -> None:
        super().__init__()
        self.filters: dict[str, str] = {}  # node name -> link group ("" for a filter program)
        self.id_links: dict[int, tuple[int, int, str]] = {}  # link id -> (out port, in port, owner)
        self.next_id_link = 5000
        self.dumps = 0

    # -- model -------------------------------------------------------------
    def _layout(self):
        nodes: dict[int, dict] = {}
        ports: dict[int, tuple[int, str, str, bool, str]] = {}  # id -> (node, direction, channel, monitor, name)
        counter = iter(range(2000, 100000))

        def add_node(node_id, props, inputs=(), outputs=(), monitors=()):
            nodes[node_id] = props
            for direction, names, monitor in (("input", inputs, False), ("output", outputs, False), ("output", monitors, True)):
                for name in names:
                    ports[next(counter)] = (node_id, direction, name.rsplit("_", 1)[-1], monitor, f"{props['node.name']}:{name}")

        for index, sink in enumerate(self.sinks):
            add_node(1000 + index, {"node.name": sink.name, "media.class": "Audio/Sink"}, [f"playback_{c}" for c in sink.channels], (), [f"monitor_{c}" for c in sink.channels])
        for stream in self.playback:
            add_node(int(stream.props["object.id"]), {"node.name": stream.props["node.name"], "media.class": "Stream/Output/Audio"}, (), ["output_FL", "output_FR"])
        for offset, (name, group) in enumerate(self.filters.items()):
            add_node(3000 + offset, {"node.name": name, **({"node.link-group": group} if group else {})}, ["input_FL", "input_FR"], ["output_FL", "output_FR"])
        by_name = {}
        for port_id, (_node, _direction, _channel, _monitor, name) in ports.items():
            by_name.setdefault(name, port_id)
        links: dict[int, tuple[int, int, str]] = {}
        for stream in self.playback:  # every stream plays into its output
            for channel in ("FL", "FR"):
                out, into = by_name.get(f"{stream.props['node.name']}:output_{channel}"), by_name.get(f"{stream.target}:playback_{channel}")
                if out and into:
                    links[len(links) + 4000] = (out, into, "")
        for out_name, in_name in self.links.values():
            if out_name in by_name and in_name in by_name:
                links[len(links) + 4000] = (by_name[out_name], by_name[in_name], "")
        links.update({k: v for k, v in self.id_links.items() if v[0] in ports and v[1] in ports})
        return nodes, ports, links

    def port_id(self, name: str) -> int:
        _nodes, ports, _links = self._layout()
        return next(port_id for port_id, port in ports.items() if port[4] == name)

    def mix_inputs(self) -> set[tuple[str, str]]:
        """(program port, mix port) names linked into the mix."""
        _nodes, ports, links = self._layout()
        return {(ports[o][4], ports[i][4]) for o, i, _owner in links.values() if ports[i][4].startswith(f"{CALL_MIX_SINK}:")}

    def _dump(self) -> str:
        nodes, ports, links = self._layout()
        objects = [{"id": i, "type": "PipeWire:Interface:Node", "info": {"props": p}} for i, p in nodes.items()]
        objects += [
            {"id": i, "type": "PipeWire:Interface:Port", "info": {"direction": d, "props": {"node.id": n, "audio.channel": c, **({"port.monitor": True} if m else {})}}}
            for i, (n, d, c, m, _name) in ports.items()
        ]
        objects += [
            {
                "id": i,
                "type": "PipeWire:Interface:Link",
                "info": {"output-node-id": ports[o][0], "output-port-id": o, "input-node-id": ports[n][0], "input-port-id": n, "props": {"big-remote-play.owner": owner} if owner else {}},
            }
            for i, (o, n, owner) in links.items()
        ]
        return json.dumps(objects)

    # -- commands ----------------------------------------------------------
    def _pw_link(self, argv):
        args = argv[1:]
        if args[:1] == ["-p"] and args[2].isdigit():
            self.writes.append(list(argv))
            self.next_id_link += 1
            self.id_links[self.next_id_link] = (int(args[2]), int(args[3]), json.loads(args[1])["big-remote-play.owner"])
            return subprocess.CompletedProcess(argv, 0, "", "")
        if args[:1] == ["-d"] and len(args) == 2 and args[1].isdigit():
            self.writes.append(list(argv))
            self.id_links.pop(int(args[1]), None)
            return subprocess.CompletedProcess(argv, 0, "", "")
        return super()._pw_link(argv)

    def __call__(self, argv, **kwargs):
        if argv[0] == "pw-dump":
            self.dumps += 1
            return subprocess.CompletedProcess(argv, 0, self._dump(), "")
        result = super().__call__(argv, **kwargs)
        args = argv[1:]
        if args[:2] == ["load-module", "module-null-sink"] and f"sink_name={CALL_MIX_SINK}" in args:
            token = re.search(r"big-remote-play\.owner=([0-9a-f]+)", " ".join(args)).group(1)
            self.sinks.append(Sink(CALL_MIX_SINK, "Big Remote Play", {"big-remote-play.owner": token}))
        if args[:1] == ["unload-module"] and not any(f"sink_name={CALL_MIX_SINK}" in m[2] for m in self.modules):
            if any(s.name == CALL_MIX_SINK for s in self.sinks):
                self.sinks = [s for s in self.sinks if s.name != CALL_MIX_SINK]
                self.id_links = {}
                for capture in self.capture:  # what the session manager does: some monitor
                    if capture.target == f"{CALL_MIX_SINK}.monitor":
                        capture.target = f"{self.default_sink}.monitor"
        return result


def call_through_effects(pulse: FakePipeWire) -> None:
    """Fluxer plays into an effects program (JamesDSP-like) that forwards to HDMI; a game plays into HDMI."""
    pulse.filters["jdsp"] = ""
    for channel in ("FL", "FR"):
        pulse.links[f"e{channel}"] = (f"{EASY}:monitor_{channel}", f"jdsp:input_{channel}")
        pulse.links[f"j{channel}"] = (f"jdsp:output_{channel}", f"{HDMI}:playback_{channel}")
    pulse.playback = [Stream(EASY, dict(CALL)), Stream(HDMI, dict(GAME))]


def session(pulse, tmp_path, **kwargs):
    return AudioRoutingSession(AudioManager(runner=pulse), state_path=tmp_path / "audio-session.json", **kwargs)


def stream(**props) -> AudioStream:
    return AudioStream("1", "out", props.get("application.name", ""), props.get("application.process.binary", ""), "1", "", "", props)


# ----------------------------------------------------------- recognition


def test_call_programs_are_recognised_by_executable_name_or_flatpak_id():
    assert is_call_stream(stream(**CALL))
    assert is_call_stream(stream(**{"application.name": "WEBRTC VoiceEngine", "application.process.binary": "Discord"}))
    assert is_call_stream(stream(**{"application.process.binary": "electron", "pipewire.access.portal.app_id": "dev.vencord.Vesktop"}))
    assert is_call_stream(stream(**{"application.name": "Telegram", "application.process.binary": "Telegram", "pipewire.access.portal.app_id": "org.telegram.desktop"}))
    assert is_call_stream(stream(**{"application.name": "teams-for-linux", "application.process.binary": "teams-for-linux"}))
    assert is_call_stream(stream(**{"application.name": "Some phone", "media.role": "phone"}))


def test_games_music_and_web_browsers_are_not_calls():
    """A call in a browser shares the browser's one stream; it cannot be told apart."""
    for props in (GAME, MUSIC, {"application.name": "Google Chrome", "application.process.binary": "chrome"}, {"application.name": "Firefox", "application.process.binary": "firefox"}):
        assert not is_call_stream(stream(**props))


def test_pipewire_graph_follows_links_and_the_halves_of_a_loopback():
    dump = [
        {"id": 1, "type": "PipeWire:Interface:Node", "info": {"props": {"node.name": "call"}}},
        {"id": 2, "type": "PipeWire:Interface:Node", "info": {"props": {"node.name": "input.loopback", "node.link-group": "lb-1"}}},
        {"id": 3, "type": "PipeWire:Interface:Node", "info": {"props": {"node.name": "output.loopback", "node.link-group": "lb-1"}}},
        {"id": 4, "type": "PipeWire:Interface:Node", "info": {"props": {"node.name": "speakers"}}},
        {"id": 5, "type": "PipeWire:Interface:Node", "info": {"props": {"node.name": "headphones"}}},
        {"id": 10, "type": "PipeWire:Interface:Link", "info": {"output-node-id": 1, "output-port-id": 11, "input-node-id": 2, "input-port-id": 12}},
        {"id": 13, "type": "PipeWire:Interface:Link", "info": {"output-node-id": 3, "output-port-id": 14, "input-node-id": 4, "input-port-id": 15}},
        {"id": 20, "type": "PipeWire:Interface:Port", "info": {"direction": "output", "props": {"node.id": 4, "audio.channel": "FL", "port.monitor": True}}},
        {"id": 21, "type": "PipeWire:Interface:Port", "info": {"direction": "input", "props": {"node.id": 4, "audio.channel": "FL"}}},
    ]
    graph = parse_pw_dump(json.dumps(dump))
    assert graph is not None
    assert graph.reaches(1, 4) and not graph.reaches(1, 5) and not graph.reaches(4, 1)
    assert graph.is_fed(2) and not graph.is_fed(1)
    assert graph.link_group(3) == "lb-1"
    assert graph.ports_of(4, "input") == {"FL": 21} and graph.ports_of(4, "output") == {}  # monitors are not outputs
    assert parse_pw_dump("not json") is None


def test_mix_channels_map_by_name_and_mono_into_both_fronts():
    assert mix_channels("FL", {"FL", "FR"}) == ["FL"]
    assert mix_channels("MONO", {"FL", "FR"}) == ["FL", "FR"]
    assert mix_channels("FR", {"MONO"}) == ["MONO"]
    assert mix_channels("LFE", {"FL", "FR"}) == []


# --------------------------------------------------------------- session


def test_a_call_is_kept_out_while_every_other_program_still_reaches_the_stream(tmp_path):
    pulse = FakePipeWire()
    call_through_effects(pulse)
    routing = session(pulse, tmp_path)
    routing.begin(AudioManager(runner=pulse).snapshot())
    pulse.sunshine_starts_session(host_audio=True)
    assert pulse.capture[0].target == f"{HDMI}.monitor"  # Sunshine records the call with the game

    status = routing.reconcile()

    loads = [w for w in pulse.writes if w[:1] == ["load-module"]]
    assert len(loads) == 1 and f"big-remote-play.owner={routing.token}" in loads[0][-1] and "channel_map" not in " ".join(loads[0])
    assert pulse.capture[0].target == f"{CALL_MIX_SINK}.monitor"
    assert pulse.mix_inputs() == {("game:output_FL", f"{CALL_MIX_SINK}:playback_FL"), ("game:output_FR", f"{CALL_MIX_SINK}:playback_FR")}
    assert all(owner == routing.token for _o, _i, owner in pulse.id_links.values())
    assert status.calls_kept_out == ("Fluxer",) and status.sunshine_records_monitor and not status.microphone_sent
    # Nothing was moved: the call and the game still play where they played.
    assert [s.target for s in pulse.playback] == [EASY, HDMI]
    assert not [w for w in pulse.writes if w[:1] in (["move-sink-input"], ["set-default-sink"])]

    writes = len(pulse.writes)
    routing.reconcile()
    assert len(pulse.writes) == writes  # idempotent


def test_when_the_call_ends_sunshine_records_the_output_again_and_the_mix_goes(tmp_path):
    pulse = FakePipeWire()
    call_through_effects(pulse)
    routing = session(pulse, tmp_path)
    routing.begin(AudioManager(runner=pulse).snapshot())
    pulse.sunshine_starts_session(host_audio=True)
    routing.reconcile()

    pulse.playback = [s for s in pulse.playback if s.props["node.name"] != "Fluxer"]
    status = routing.reconcile()

    assert pulse.capture[0].target == f"{HDMI}.monitor"
    assert not any(s.name == CALL_MIX_SINK for s in pulse.sinks) and routing.mix_module == ""
    assert status.calls_kept_out == ()
    # The capture moved back before the mix was removed, never after.
    moves = [i for i, w in enumerate(pulse.writes) if w[:1] == ["move-source-output"]]
    unloads = [i for i, w in enumerate(pulse.writes) if w[:1] == ["unload-module"]]
    assert moves[-1] < unloads[-1]


def test_without_a_call_program_nothing_new_is_read_or_written(tmp_path):
    pulse = FakePipeWire()
    pulse.playback = [Stream(HDMI, dict(GAME))]
    routing = session(pulse, tmp_path)
    routing.begin(AudioManager(runner=pulse).snapshot())
    pulse.sunshine_starts_session(host_audio=True)
    routing.reconcile()
    assert pulse.writes == [] and pulse.dumps == 0


def test_a_call_on_another_device_is_not_sent_anyway_and_changes_nothing(tmp_path):
    """Headphones Sunshine does not record: the call never reached the stream."""
    pulse = FakePipeWire()
    pulse.playback = [Stream("bluez_output.00_11_22_33_44_55.1", dict(CALL)), Stream(HDMI, dict(GAME))]
    routing = session(pulse, tmp_path)
    routing.begin(AudioManager(runner=pulse).snapshot())
    pulse.sunshine_starts_session(host_audio=True)
    assert routing.reconcile().calls_kept_out == ()
    assert pulse.writes == []


def test_programs_that_forward_other_sound_are_not_added_to_the_mix(tmp_path):
    """The effects program and a loopback carry the call: adding them would send it."""
    pulse = FakePipeWire()
    call_through_effects(pulse)
    pulse.filters["output.loopback-1"] = "loopback-1"
    pulse.filters["input.loopback-1"] = "loopback-1"
    pulse.playback.append(Stream(HDMI, {"node.name": "output.loopback-1", "node.link-group": "loopback-1", "object.id": "3001"}))
    routing = session(pulse, tmp_path)
    routing.begin(AudioManager(runner=pulse).snapshot())
    pulse.sunshine_starts_session(host_audio=True)
    routing.reconcile()
    assert {out.split(":")[0] for out, _in in pulse.mix_inputs()} == {"game"}


def test_a_program_that_starts_during_the_call_is_added(tmp_path):
    pulse = FakePipeWire()
    call_through_effects(pulse)
    routing = session(pulse, tmp_path)
    routing.begin(AudioManager(runner=pulse).snapshot())
    pulse.sunshine_starts_session(host_audio=True)
    routing.reconcile()
    pulse.playback.append(Stream(EASY, dict(MUSIC)))  # through the effects, like the call
    routing.reconcile()
    assert {out.split(":")[0] for out, _in in pulse.mix_inputs()} == {"game", "music"}


def test_a_client_that_mutes_the_host_keeps_its_bridge_and_the_call_stays_out(tmp_path):
    pulse = FakePipeWire()
    pulse.playback = [Stream(HDMI, dict(CALL)), Stream(HDMI, dict(GAME))]
    routing = session(pulse, tmp_path, play_on_host=True)
    routing.begin(AudioManager(runner=pulse).snapshot())
    pulse.sunshine_starts_session(host_audio=False)  # default -> Sunshine's output, recorded
    for s in pulse.playback:
        s.target = SUNSHINE_STEREO_SINK  # applications follow the default output
    status = routing.reconcile()
    assert pulse.capture[0].target == f"{CALL_MIX_SINK}.monitor"
    assert status.bridges == (Bridge(SUNSHINE_STEREO_SINK, HDMI, "client-muted-host"),)
    assert status.calls_kept_out == ("Fluxer",)
    graph = AudioManager(runner=pulse).snapshot()
    assert desired_bridges(graph, original_sink=HDMI, play_on_host=True, origins=routing.capture_origins)[0] == [Bridge(SUNSHINE_STEREO_SINK, HDMI, "client-muted-host")]


def test_the_mix_stays_while_sunshine_still_records_it(tmp_path):
    """Removing it then would leave the choice of a new source to the session manager."""
    pulse = FakePipeWire()
    call_through_effects(pulse)
    routing = session(pulse, tmp_path)
    routing.begin(AudioManager(runner=pulse).snapshot())
    pulse.sunshine_starts_session(host_audio=True)
    routing.reconcile()
    real = pulse.__call__

    def refuse_moves(argv, **kwargs):
        if argv[:2] == ["pactl", "move-source-output"]:
            return subprocess.CompletedProcess(argv, 1, "", "Failure")
        return real(argv, **kwargs)

    routing.manager = AudioManager(runner=refuse_moves)
    pulse.playback = [s for s in pulse.playback if s.props["node.name"] != "Fluxer"]
    routing.reconcile()
    assert any(s.name == CALL_MIX_SINK for s in pulse.sinks)
    assert pulse.capture[0].target == f"{CALL_MIX_SINK}.monitor"


def test_ending_the_session_removes_the_mix_and_our_links_only(tmp_path):
    pulse = FakePipeWire()
    call_through_effects(pulse)
    routing = session(pulse, tmp_path)
    routing.begin(AudioManager(runner=pulse).snapshot())
    pulse.sunshine_starts_session(host_audio=True)
    routing.reconcile()
    state = json.loads((tmp_path / "audio-session.json").read_text())
    assert state["mix_module"] == routing.mix_module and "capture_origins" not in state
    pulse.sunshine_exits(HDMI)
    routing.end(settle=0)
    assert not any(s.name == CALL_MIX_SINK for s in pulse.sinks)
    assert pulse.links["90"] == ("mic:capture_FL", "hw:playback_FL")  # someone else's
    assert ("7", "module-loopback", "source=mic sink=hw latency_msec=20") in pulse.modules


def test_recovery_removes_a_mix_left_by_a_crashed_window_when_not_sharing(tmp_path):
    pulse = FakePipeWire()
    call_through_effects(pulse)
    routing = session(pulse, tmp_path)
    routing.begin(AudioManager(runner=pulse).snapshot())
    pulse.sunshine_starts_session(host_audio=True)
    routing.reconcile()
    (tmp_path / "audio-session.json").unlink()  # the state was lost too
    pulse.sunshine_exits(HDMI)
    AudioRoutingSession.recover(AudioManager(runner=pulse), sunshine_running=False, state_path=tmp_path / "audio-session.json")
    assert not any(s.name == CALL_MIX_SINK for s in pulse.sinks)
    assert ("7", "module-loopback", "source=mic sink=hw latency_msec=20") in pulse.modules


def test_mix_description_cannot_break_the_module_arguments():
    calls = []

    def runner(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "536870999\n", "")

    index = AudioManager(runner=runner).create_call_mix("0123456789abcdef", "front-left,front-right", 'Big "Remote\\" Play')
    assert index == "536870999"
    args = calls[0]
    assert "channel_map=front-left,front-right" in args
    assert args[-1] == 'sink_properties="device.description=\\"Big Remote Play\\" big-remote-play.owner=0123456789abcdef"'
    AudioManager(runner=runner).create_call_mix("0123456789abcdef", "front-left front-right; rm", "x")
    assert not any(a.startswith("channel_map") for a in calls[-1])


def test_watcher_reacts_to_a_program_starting_or_stopping_but_not_to_its_volume():
    calls = []
    release = threading.Event()

    def events():
        yield from ["Event 'change' on sink-input #512\n"] * 10
        release.wait(2)
        yield "Event 'new' on sink-input #513\n"

    watcher = AudioWatcher(lambda: calls.append(time.monotonic()), debounce=0.05, safety_interval=30, spawn=lambda: FakeSubscribe(events()))
    watcher.start()
    time.sleep(0.3)
    assert len(calls) == 1  # the initial check only
    release.set()
    deadline = time.monotonic() + 2
    while len(calls) < 2 and time.monotonic() < deadline:
        time.sleep(0.02)
    watcher.stop()
    assert len(calls) == 2
