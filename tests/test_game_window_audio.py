"""Game Window sends only the game's sound (no real sound server, no real /proc).

The game's playback is linked into Big Remote Play's own output by port links
and Sunshine records that output's monitor. Nothing is moved: this computer
keeps hearing every program, and other programs' streams, Steam's capture and
the person's effects stay as they are.
"""

from __future__ import annotations

import json

from test_audio import HDMI, SUNSHINE_STEREO_SINK, Stream
from test_voice_calls_audio import CALL, FakePipeWire

from big_remote_play.host.game_windows import ProcessInfo, game_audio_root, game_process_family
from big_remote_play.utils import audio
from big_remote_play.utils.audio import CALL_MIX_SINK, AudioManager, AudioRoutingSession, GameScope, audio_status

GAME_PID = 4242
GAME = {"application.name": "STREET FIGHTER™ V", "application.process.binary": "wine64-preloader", "application.process.id": str(GAME_PID), "node.name": "game", "object.id": "160"}
MUSIC = {"application.name": "Music", "application.process.binary": "music", "application.process.id": "5000", "node.name": "music", "object.id": "170"}
BROWSER = {"application.name": "Google Chrome", "application.process.binary": "chrome", "application.process.id": "6000", "node.name": "chrome", "object.id": "180"}


def session_for(pulse, tmp_path, *, family=frozenset({GAME_PID, GAME_PID + 1}), names=("StreetFighterV.exe",), **kwargs):
    return AudioRoutingSession(AudioManager(runner=pulse), state_path=tmp_path / "audio-session.json", game=GameScope(GAME_PID, names), game_family=lambda _pid: family, **kwargs)


def desktop_with_game():
    pulse = FakePipeWire()
    pulse.playback += [Stream(HDMI, dict(GAME)), Stream(HDMI, dict(MUSIC)), Stream(HDMI, dict(BROWSER))]
    return pulse


def sent(pulse):
    """Programs whose ports are linked into the mix Sunshine records."""
    return {program.split(":")[0] for program, _mix in pulse.mix_inputs()}


# ---------------------------------------------------------------- the game's processes


def proc(pid, ppid, name, exe="", cmdline=""):
    return ProcessInfo(pid=pid, ppid=ppid, name=name, exe=exe, argv0=cmdline.split(" ")[0], cmdline=cmdline)


def test_a_steam_game_is_everything_below_its_reaper():
    chain = [
        proc(100, 90, "StreetFighterV.", "/usr/bin/wine64-preloader", "S:\\StreetFighterV.exe"),
        proc(90, 80, "pv-adverb"),
        proc(80, 70, "srt-bwrap"),
        proc(70, 60, "reaper", cmdline="reaper SteamLaunch AppId=310950 -- proton"),
        proc(60, 1, "steam"),
    ]
    assert game_audio_root(chain) == 70
    table = {p.pid: p for p in chain} | {110: proc(110, 70, "wineserver"), 120: proc(120, 110, "explorer.exe"), 200: proc(200, 60, "steamwebhelper")}
    family = game_process_family(100, read=table.get, parents=lambda: {pid: info.ppid for pid, info in table.items()})
    assert family == {70, 80, 90, 100, 110, 120}  # not Steam itself, not its web helper


def test_a_wine_game_from_a_launcher_stops_at_the_launcher():
    chain = [proc(300, 290, "game.exe", "/usr/bin/wine-preloader", "C:\\game.exe"), proc(290, 280, "start.exe", "/usr/bin/wine-preloader", "start.exe"), proc(280, 1, "heroic")]
    assert game_audio_root(chain) == 290


def test_a_lutris_game_is_everything_below_its_wrapper():
    chain = [proc(400, 390, "game"), proc(390, 380, "lutris-wrapper:"), proc(380, 1, "lutris")]
    assert game_audio_root(chain) == 390


def test_a_native_game_started_from_the_desktop_is_its_own_root():
    chain = [proc(500, 450, "supertuxkart", "/usr/bin/supertuxkart"), proc(450, 1, "plasmashell")]
    assert game_audio_root(chain) == 500
    family = game_process_family(500, read={p.pid: p for p in chain}.get, parents=lambda: {500: 450, 501: 500, 450: 1, 600: 450})
    assert family == {500, 501}


# ---------------------------------------------------------------- only the game is sent


def test_only_the_games_sound_is_sent_and_nothing_is_moved(tmp_path):
    pulse = desktop_with_game()
    session = session_for(pulse, tmp_path)
    session.begin(AudioManager(runner=pulse).snapshot())
    pulse.sunshine_starts_session(host_audio=True)
    status = session.reconcile()
    assert pulse.capture[-1].target == f"{CALL_MIX_SINK}.monitor"
    assert sent(pulse) == {"game"}
    assert [s.target for s in pulse.playback] == [HDMI, HDMI, HDMI]  # every program still plays here
    assert not any(w[0] in ("move-sink-input", "set-sink-input-mute", "set-sink-input-volume") for w in pulse.writes)
    assert status.game_only and status.game_separated and status.game_programs == ("STREET FIGHTER™ V",)
    assert audio.stream_audio_state(status, 1) == audio.STREAM_SENDING


def test_a_game_window_without_a_pid_still_sends_only_the_game(tmp_path):
    """X11 windows without _NET_WM_PID: the game is found by name, and no
    process family is read for PID 0."""
    pulse = desktop_with_game()
    asked: list[int] = []
    session = AudioRoutingSession(
        AudioManager(runner=pulse),
        state_path=tmp_path / "audio-session.json",
        game=GameScope(0, ("STREET FIGHTER™ V",)),
        game_family=lambda pid: asked.append(pid) or frozenset(),
    )
    session.begin(AudioManager(runner=pulse).snapshot())
    pulse.sunshine_starts_session(host_audio=True)
    status = session.reconcile()
    assert sent(pulse) == {"game"}
    assert asked == []
    assert status.game_only and status.game_separated


def test_a_call_and_other_programs_stay_out_even_through_effects(tmp_path):
    pulse = desktop_with_game()
    pulse.playback.append(Stream(HDMI, dict(CALL, **{"application.process.id": "7000"})))
    session = session_for(pulse, tmp_path)
    session.begin(AudioManager(runner=pulse).snapshot())
    pulse.sunshine_starts_session(host_audio=True)
    session.reconcile()
    assert sent(pulse) == {"game"}


def test_a_game_that_recreates_its_stream_is_followed(tmp_path):
    """Games destroy their stream when they lose focus and make a new one later."""
    pulse = desktop_with_game()
    session = session_for(pulse, tmp_path)
    session.begin(AudioManager(runner=pulse).snapshot())
    pulse.sunshine_starts_session(host_audio=True)
    session.reconcile()
    pulse.playback = [s for s in pulse.playback if s.props["node.name"] != "game"]
    status = session.reconcile()
    assert sent(pulse) == set() and status.game_programs == ()
    assert pulse.capture[-1].target == f"{CALL_MIX_SINK}.monitor"  # silence, never the desktop
    pulse.playback.append(Stream(HDMI, dict(GAME, **{"application.process.id": str(GAME_PID + 1), "node.name": "game2", "object.id": "161"})))
    session.reconcile()
    assert sent(pulse) == {"game2"}


def test_a_sandboxed_game_is_found_by_its_name(tmp_path):
    pulse = desktop_with_game()
    pulse.playback[0].props["application.process.id"] = "2"  # a PID from another namespace
    pulse.playback[0].props["application.name"] = "StreetFighterV.exe"
    session = session_for(pulse, tmp_path, family=frozenset({GAME_PID}))
    session.begin(AudioManager(runner=pulse).snapshot())
    pulse.sunshine_starts_session(host_audio=True)
    session.reconcile()
    assert sent(pulse) == {"game"}


def test_a_client_that_mutes_the_host_keeps_its_bridge(tmp_path):
    pulse = desktop_with_game()
    session = session_for(pulse, tmp_path)
    session.begin(AudioManager(runner=pulse).snapshot())
    pulse.sunshine_starts_session(host_audio=False)
    for stream in pulse.playback:
        stream.target = SUNSHINE_STEREO_SINK  # programs follow Sunshine's default
    status = session.reconcile()
    assert pulse.capture[-1].target == f"{CALL_MIX_SINK}.monitor" and sent(pulse) == {"game"}
    assert [(b.source_sink, b.target_sink) for b in status.bridges] == [(SUNSHINE_STEREO_SINK, HDMI)]  # this computer still hears


def test_unreadable_pipewire_never_switches_to_sending_everything_silently(tmp_path):
    pulse = desktop_with_game()
    session = session_for(pulse, tmp_path)
    session.begin(AudioManager(runner=pulse).snapshot())
    pulse.sunshine_starts_session(host_audio=True)
    session.manager.pipewire = lambda: None
    status = session.reconcile()
    assert "game-not-separated" in status.notes and status.game_separated is False
    assert audio.stream_audio_state(status, 1) == audio.STREAM_NOT_SEPARATED


def test_ending_puts_sunshine_back_and_removes_the_game_output(tmp_path):
    pulse = desktop_with_game()
    session = session_for(pulse, tmp_path)
    session.begin(AudioManager(runner=pulse).snapshot())
    pulse.sunshine_starts_session(host_audio=True)
    session.reconcile()
    session.end(sunshine_stopped=True, settle=0)
    assert not any(s.name == CALL_MIX_SINK for s in pulse.sinks) and pulse.mix_inputs() == set()
    assert [m[0] for m in pulse.modules] == ["7"]  # someone else's module only


def test_steam_capture_is_left_alone(tmp_path):
    pulse = desktop_with_game()
    pulse.capture.append(Stream("steam_rec.monitor", {"application.name": "Steam", "application.process.binary": "steam"}))
    session = session_for(pulse, tmp_path)
    session.begin(AudioManager(runner=pulse).snapshot())
    pulse.sunshine_starts_session(host_audio=True)
    session.reconcile()
    assert pulse.capture[0].target == "steam_rec.monitor"


def test_full_desktop_without_a_call_still_records_the_output_directly(tmp_path):
    pulse = desktop_with_game()
    session = AudioRoutingSession(AudioManager(runner=pulse), state_path=tmp_path / "audio-session.json")
    session.begin(AudioManager(runner=pulse).snapshot())
    pulse.sunshine_starts_session(host_audio=True)
    status = session.reconcile()
    assert pulse.capture[-1].target == f"{HDMI}.monitor" and pulse.writes == [] and pulse.dumps == 0
    assert not status.game_only and status.game_separated is None


def test_an_adopted_session_keeps_sending_only_the_game(tmp_path):
    pulse = desktop_with_game()
    session = session_for(pulse, tmp_path)
    session.begin(AudioManager(runner=pulse).snapshot())
    state = json.loads((tmp_path / "audio-session.json").read_text())
    assert state["game"] == {"pid": GAME_PID, "names": ["StreetFighterV.exe"]}
    loaded = AudioRoutingSession.load(AudioManager(runner=pulse), tmp_path / "audio-session.json")
    assert loaded is not None and loaded.game == GameScope(GAME_PID, ("StreetFighterV.exe",))
    assert GameScope.from_state({"pid": "4242"}) is None and GameScope.from_state({"pid": -1}) is None
    # A window with no PID is still a game scope (names only), never "everything".
    assert GameScope.from_state({"pid": 1}) == GameScope(0, ())
    assert GameScope.from_state({"pid": 0, "names": ["STREET FIGHTER™ V"]}) == GameScope(0, ("STREET FIGHTER™ V",))


def test_the_tone_test_says_the_tone_is_not_sent_in_game_only(tmp_path):
    pulse = desktop_with_game()
    session = session_for(pulse, tmp_path)
    session.begin(AudioManager(runner=pulse).snapshot())
    pulse.sunshine_starts_session(host_audio=True)
    session.reconcile()
    graph = AudioManager(runner=pulse).snapshot()
    assert audio.capture_check(graph, f"{HDMI}.monitor", game_only=True)[0] == audio.CAPTURE_GAME_ONLY
    assert audio_status(graph).sunshine_source == f"{CALL_MIX_SINK}.monitor"
