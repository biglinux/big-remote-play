"""Who is streaming from Share right now, from Sunshine's own evidence.

Connected means Sunshine's log says a session is active; the address comes
from that session's RTSP handshake. Paired devices, Moonlight's routine
``/serverinfo`` polling and computers that merely answer a ping never count.
"""

from __future__ import annotations

from big_remote_play.host.sunshine_sessions import LogReader, SessionTracker, count_sessions, handshake_peers

START = "[2026-09-29 11:14:21.617]: Info: Sunshine version: 2026.914.233613 commit: 63d35f7\n"
CONNECT = "[2026-09-29 11:16:54.896]: Info: New streaming session started [active sessions: 1]\n[2026-09-29 11:16:54.918]: Info: CLIENT CONNECTED\n"
DISCONNECT = "[2026-09-29 11:17:45.287]: Info: CLIENT DISCONNECTED\n"
TERMINATE = "[2026-09-29 11:26:41.117]: Info: Terminate handler called\n"

SS_HANDSHAKE = """State      Recv-Q Send-Q Local Address:Port  Peer Address:Port
LISTEN     0      4096   0.0.0.0:48010       0.0.0.0:*
TIME-WAIT  0      0      192.168.56.10:48010  192.168.56.50:53012
TIME-WAIT  0      0      [::ffff:192.168.56.10]:48010 [::ffff:192.168.56.50]:53013
ESTAB      0      0      192.168.56.10:47989  192.168.56.77:41000
ESTAB      0      0      127.0.0.1:47990     127.0.0.1:52000
"""


def test_the_log_markers_count_active_sessions():
    assert count_sessions((START + CONNECT).splitlines()) == 1
    assert count_sessions((START + CONNECT + DISCONNECT).splitlines()) == 0
    assert count_sessions((START + CONNECT + CONNECT).splitlines()) == 2
    assert count_sessions((START + CONNECT + TERMINATE).splitlines()) == 0  # Sunshine stopped
    assert count_sessions((DISCONNECT + DISCONNECT).splitlines()) == 0  # never negative


def test_only_the_stream_handshake_port_identifies_a_device():
    peers = handshake_peers(SS_HANDSHAKE, 48010)
    assert peers == ["192.168.56.50"]  # mapped IPv6 unwrapped, duplicates merged
    assert "192.168.56.77" not in peers  # /serverinfo polling on 47989
    assert "127.0.0.1" not in peers  # this app's own web API
    assert handshake_peers("TIME-WAIT 0 0 10.0.0.1:480100 10.0.0.9:1\n", 48010) == []  # exact port, no substring


def test_the_log_is_followed_incrementally_and_a_new_run_resets_it(tmp_path):
    log = tmp_path / "sunshine.log"
    log.write_text(START)
    reader = LogReader(log)
    assert reader.poll() == 0
    with log.open("a") as handle:
        handle.write(CONNECT)
    assert reader.poll() == 1
    offset = reader._offset
    assert reader.poll() == 1 and reader._offset == offset  # nothing re-read
    with log.open("a") as handle:
        handle.write("[x]: Info: CLIENT DISCON")  # a line still being written
    assert reader.poll() == 1
    with log.open("a") as handle:
        handle.write("NECTED\n")
    assert reader.poll() == 0
    rotated = tmp_path / "new.log"
    rotated.write_text(START + CONNECT)
    rotated.replace(log)  # Sunshine starts again with a fresh file
    assert reader.poll() == 1
    log.unlink()
    assert reader.poll() == 0


class Scene:
    def __init__(self, tmp_path):
        self.log = tmp_path / "sunshine.log"
        self.log.write_text(START)
        self.ss_output = ""
        self.now = 1000.0
        self.running = True
        self.tracker = SessionTracker(self.log, ss=lambda argv: self.ss_output, clock=lambda: self.now, running=lambda: self.running)

    def write(self, text):
        with self.log.open("a") as handle:
            handle.write(text)


def test_a_device_is_connected_only_while_its_session_is_active(tmp_path):
    scene = Scene(tmp_path)
    scene.ss_output = SS_HANDSHAKE
    assert scene.tracker.poll() == []  # a handshake alone is not a session
    scene.write(CONNECT)
    scene.now += 3
    sessions = scene.tracker.poll()
    assert [session.address for session in sessions] == ["192.168.56.50"]
    scene.write(DISCONNECT)
    scene.now += 3
    assert scene.tracker.poll() == []


def test_a_session_without_a_seen_handshake_is_listed_without_a_guessed_address(tmp_path):
    scene = Scene(tmp_path)
    scene.write(CONNECT)
    sessions = scene.tracker.poll()
    assert len(sessions) == 1 and sessions[0].address == ""


def test_an_old_handshake_is_not_attributed_to_a_later_session(tmp_path):
    scene = Scene(tmp_path)
    scene.ss_output = SS_HANDSHAKE
    scene.tracker.poll()
    scene.ss_output = ""
    scene.now += 300  # five minutes later someone else starts a stream
    scene.write(CONNECT)
    assert scene.tracker.poll()[0].address == ""


def test_two_devices_and_one_leaving(tmp_path):
    scene = Scene(tmp_path)
    scene.ss_output = SS_HANDSHAKE
    scene.write(CONNECT)
    scene.tracker.poll()
    scene.now += 10
    scene.ss_output = "TIME-WAIT 0 0 10.0.0.1:48010 100.64.0.7:5000\n"
    scene.write(CONNECT)
    assert {session.address for session in scene.tracker.poll()} == {"192.168.56.50", "100.64.0.7"}
    scene.write(DISCONNECT)
    assert len(scene.tracker.poll()) == 1


def test_nothing_is_connected_when_sunshine_is_not_running(tmp_path):
    scene = Scene(tmp_path)
    scene.write(CONNECT)
    scene.running = False
    assert scene.tracker.poll() == []
