# Audio testing

What has been verified about Sunshine audio, how, and what still needs a real machine. The design is in [audio architecture](audio-architecture.md); release acceptance is in [release testing](release-testing.md).

## Evidence levels

| Level | Meaning |
|---|---|
| **Unit tested** | `tests/test_audio.py`: a fake PulseAudio server renders `pactl list` output and records every write |
| **UI tested** | `tests/test_home_image_audio_policy.py`, `tests/test_ui_task_flows.py`: real GTK, fake sound server |
| **Measured** | Real Sunshine and Moonlight on the development machine, with the date |
| **Pending** | Needs other hardware, another session type or another person |

The automated tests never reach the real sound server: `tests/conftest.py` gives every `AudioManager` a runner that finds no server, keeps the session record in a temporary runtime directory and disables the test tone.

## Measured on 2026-09-29

Machine: KDE Plasma 6.7.4 on Wayland, Linux 7.2, PipeWire 1.6.8 with pipewire-pulse, WirePlumber 0.5.17, Sunshine 2026.914, Moonlight Qt 6.1.0, two AMD GPUs, USB digital output as default, a USB headset output, JamesDSP active (it moves every new playback stream into its own sink and forwards to the default output).

Method: an isolated Sunshine configuration (own port, state, credentials and log) and an isolated Moonlight configuration on the same machine. Moonlight's playback was moved to a lab null sink so the stream could not echo into the real output. A 660 Hz tone at −20 dBFS was played by an ordinary application; levels are the tone's own energy (Goertzel), so other sound playing at the time does not count.

### Sunshine on its own

| Scenario | Default output during the session | Sunshine records | Other computer |
|---|---|---|---|
| Client plays sound on host | unchanged | monitor of the current output | hears the game |
| Client mutes the host | Sunshine's `sink-sunshine-stereo` | its monitor | hears; **this computer silent** |
| 5.1 client mutes the host | `sink-sunshine-surround51` | its monitor | hears |
| `audio_sink` = current device, client mutes the host | `sink-sunshine-stereo` | the device's monitor | **silent** |
| `audio_sink` = a missing name | unchanged / Sunshine's | the default output's monitor | **silent** |
| `audio_sink` = `sink-sunshine-stereo`, 5.1 client mutes the host | `sink-sunshine-surround51` | stereo monitor | **silent** |
| Sunshine stopped normally during a session | restored in 0.2 s | — | — |
| **Sunshine killed** during a host-muting session | **stays on Sunshine's output**; desktop silent | — | — |

In no scenario did Sunshine record a microphone. Removing the recorded device moved a monitor capture to the new default output's monitor, not to the microphone.

Latency from Sunshine's capture to Moonlight's playback on the same machine, by cross-correlation: **44 ms** when recording the output directly, **82 ms** through Sunshine's virtual output. Direct capture is the default.

### Steam Remote Play

No Steam Remote Play Together session could be run (it needs a second Steam account online). Steam's recorder was inspected in `steamui.so`, and its per-process reroute was reproduced on the real sound server: a combine sink with `steam.autounload=true` and `slaves=<the game's output>`, with the game's stream moved into it and its monitor recorded.

| Condition | Game stream stays in Steam's sink | Steam's recording |
|---|---|---|
| No Big Remote Play routing | 10 of 10 checks over 5 s | tone at the expected level |
| Big Remote Play 2.x explicit routing | moved back to `SunshineGameSink` within 0.5 s | only the first half second |

The 2.x enforcer also moved Telegram, speech and browser streams into the shared output.

### With Big Remote Play

| Scenario | This computer's `sunshine.conf` | Other computer | This computer's speaker | Sound graph afterwards |
|---|---|---|---|---|
| Automatic, client plays on host | `audio_sink` unset | −18.5 dB | −18.5 dB | nothing was written |
| Automatic, client mutes the host | unset | −18.9 dB | −19.0 dB (bridge) | link removed, output unchanged |
| Output changed to the headset during the stream | unset | −20.0 dB (capture followed) | −19.9 dB | unchanged |
| Only the other computer, 5.1 client mutes the host | `sink-sunshine-stereo` | −17.4 dB (bridge) | silent, as chosen | link removed |
| Sunshine killed during a host-muting session | unset | — | output restored by Big Remote Play | no links |
| Stop sharing after a host-muting session, JamesDSP follows the output by itself | unset | — | −22.4 dB | nothing added; `end` took 0.05 s |
| Stop sharing, JamesDSP left linked to nothing (the incident, forced by removing its links after Sunshine exited) | unset | — | silent → −22.6 dB | JamesDSP output linked to the device after 2 s; JamesDSP moved it itself on the next output change |
| 44.1 kHz source | unset | −19.6 dB | −19.7 dB | — |
| Output volume at 50 % | unset | −19.8 dB (unchanged) | — | volume restored |

A `module-loopback` bridge was measured as well: JamesDSP captured the loopback's stream and forwarded it to the default output — Sunshine's virtual output — which the loopback read: a feedback loop, measured as a louder stream and a silent speaker. Bridges are therefore PipeWire port links, which effects programs do not capture.

### Voice calls kept out of the stream

Measured on 2026-09-29 in an isolated PipeWire 1.6.8 + WirePlumber 0.5 instance (private `XDG_RUNTIME_DIR`, ALSA and Bluetooth monitors disabled, so no real device was touched). Outputs were null sinks, the call a tone played by an executable named `fluxer`, the game another tone, and Sunshine's capture a `parecord` with Sunshine's client and stream names; the real `AudioRoutingSession` reconciled. Results are in the [architecture](audio-architecture.md#voice-calls-stay-out-of-the-stream). Also measured there: `pipewire-pulse` takes `sink_properties="device.description=\"…\" key=value"` and nothing simpler with spaces; a capture whose source is removed was moved by WirePlumber to the default output's monitor, not to the default microphone, even with one present.

### Sunshine's recording restored muted (2026-10-02)

The live session of a report (Game Window, *Gauntlet* through Wine, JamesDSP, a client muting the host): the game played here and the other computer heard nothing. Sunshine opened the right monitor, but WirePlumber had restored *muted, 62 %* to its `sunshine-record`. Recording the same monitor at the same moment: as "sunshine" digital silence, under another name −18.5 dBFS. After `restore_capture_level` on the live stream: not muted, 100 %, WirePlumber saved `"mute": false, "volume": 1.0`, and a new "sunshine" recording received −25.1 dBFS. The mechanism and the fix are in [audio architecture](audio-architecture.md#sunshines-recording-level).

### Game Window: only the game's sound (2026-10-02)

Isolated PipeWire 1.6.8 + WirePlumber (private runtime and state, ALSA and Bluetooth monitors disabled), one null output, a "game" (`sh` running `paplay`, 660 Hz, −20 dBFS), another program (`paplay`, 1000 Hz) and a capture named like Sunshine's; the real `AudioRoutingSession` with the game's process family. Tone levels in Sunshine's capture and on the output:

| Phase | Capture: game | Capture: other program | Output: game / other |
|---|---|---|---|
| Before (whole output) | −20.0 dB | −20.0 dB | −20.0 / −20.0 dB |
| Only the game | −20.0 dB | −305 dB | −20.0 / −20.0 dB |
| After the end | −20.0 dB | −20.0 dB | −20.0 / −20.0 dB |

Process family read live for *Street Fighter V* (Steam, Proton): `reaper` and its 16 descendants (`srt-bwrap`, `pv-adverb`, `wineserver`, Wine services, the game), and the game's audio stream carried the window's PID.

### Game Window compared with Full Desktop (2026-10-01)

Isolated rig on the development machine (KDE Plasma 6.7 Wayland, PipeWire 1.6.8, WirePlumber 0.5.17, Sunshine 2026.914, Moonlight Qt 6.1; AMD RX 9060 XT + Renoir iGPU): a private PipeWire instance with only null sinks, a separate Sunshine (port 48989) recording a `game` sink that played a continuous 1 kHz tone at −20 dBFS, and a separate Moonlight on the same machine playing into a `lab` sink. The received tone was analysed in 10 ms windows (amplitude drops > 6 dB, phase jumps > 0.3 rad, silences ≥ 1 ms) together with PipeWire's error counters (`pw-top`), 58 s per run. The Game Window runs used the same private screen as Big Remote Play (`kwin_wayland --virtual`) with an animated client, `capture = kwin` and the iGPU as `adapter_name`.

| Capture | Encoder | Private screen | Client | Runs | Events per run (drops / phase jumps / silences) | PipeWire errors |
|---|---|---|---|---|---|---|
| kms | Vulkan, dGPU | — | 60 fps | 2 | at most 1 / 1 / 1 | 0 |
| kwin | Vulkan, iGPU | 60 Hz | 60 or 120 fps | 3 | at most 0 / 0 / 1 | 0 |
| kwin | Vulkan, iGPU | 240 Hz | 60 or 120 fps | 3 | at most 1 / 1 / 1 | 0 |

The tone played into Sunshine was clean in every run, and the received sound had at most one isolated event per minute in both modes. The Game Window pipeline (PipeWire video capture with frame pacing, the iGPU encoder, the 240 Hz private screen, 60 or 120 fps) does not by itself break the sound.

### Game Window with real games

*Shadow of the Tomb Raider* (Steam, Proton) at its main menu, shared with Game Window on the development machine (2026-10-01). The game's sound was recorded read-only from the monitor of the real output; focus was moved by KWin scripting:

| Capture | Game is the active window | Game's audio stream (40 s) | PipeWire errors |
|---|---|---|---|
| off | yes | present for 40 s, no gaps or clicks | 0 |
| off | no | **removed by the game**: silence for 40 s | 0 |
| on | yes, for all 40 s | present for 40 s | 0 |

The game destroys its audio stream as soon as it is not the active window and creates a new one when it is again; the capture itself never took the focus. This is why Game Window activates the game when sharing starts and every time a device starts playing (`window_capture.activate_game`, `tests/test_game_window_focus.py`), and why the game's stream is followed by `new`/`remove` events ([audio architecture](audio-architecture.md#game-window-only-the-games-sound)).

*TMNT: Shredder's Revenge* (Heroic, Wine) shared to a second Linux computer on the same home network running Big Remote Play → Connect (2026-10-01): for 120 s the sound was recorded at the game's own stream, at the output Sunshine records and at the connecting computer's output, with the game's focus logged every second. The game stayed the active window, all three points carried the sound for all 120 s in step, and the person playing heard continuous, clean sound. Earlier in that session the connecting computer had no sound for a few minutes and recovered by itself; its Moonlight log showed *Audio packet queue overflow* / *Network dropped audio data* bursts (also every few minutes during the clean run), which places that silence on the receiving side or the network, not in Big Remote Play's routing or Sunshine's capture.

## Automated coverage

- Discovery: parser, non-ASCII descriptions, output kinds from properties, verified monitors, a microphone that carries a monitor-like name, no server, no outputs, unreadable server.
- Configuration: `audio_sink` for each mode, never a microphone or `@DEFAULT_AUDIO_SOURCE@`; unknown `sunshine.conf` options and `virtual_sink` preserved; previous file kept.
- Bridges: each measured Sunshine behavior, refusal into virtual outputs, no microphone source, cycle check, surround to stereo.
- Session: no writes in Automatic, idempotent reconcile, only our links removed (id and ports re-checked), restoring the output after a Sunshine crash, never overriding a newer choice, reconnecting a program left without an output when Sunshine exits (only when still unlinked after the wait, never into a virtual output, mono devices, also after a crash, recorded before **Stop sharing**), following an output change, moving a microphone capture back, leaving Steam's capture alone, private session record, crash recovery and adoption, removal of 2.x leftovers, corrupt records.
- Recording level: mute and volume read from `pactl`; a muted or turned-down Sunshine recording restored (also while the client mutes the host and a bridge is in place); never more than three times for one recording; again for a recording Sunshine creates anew and after **Test audio**; other programs', microphones' and Steam's levels never changed; a change logged once.
- Sound state in words: unavailable, waiting, starting, could not open, muted, microphone, sending; Sunshine's log (monitor and encoder, `pa_simple_new` failure, an earlier run, only the end of a large file); the tone test says whether Sunshine records the tested monitor; output switched to a headset or Bluetooth; an output removed during the stream.
- Connecting computer (`tests/test_moonlight_audio.py`): Moonlight's sound messages, loss bursts logged once and told once, Moonlight's stream found by binary or Flatpak id, a muted Moonlight stream unmuted with its volume kept, no stream, a device Moonlight could not open, a connection that ended, no sound server.
- Game Window, only the game's sound (`tests/test_game_window_audio.py`): the game's process family for Steam (`reaper`), Lutris, Wine under a launcher and a native game; only the game linked, nothing moved; calls and other programs kept out; a game that recreates its stream; a sandboxed PID found by name; a host-muting client keeps its bridge; unreadable PipeWire never silently sends everything; end removes the output; Steam's capture untouched; Full Desktop without a call writes nothing; the scope kept by an adopted session; the tone test; the option in the interface.
- Watcher: reacts to default and capture events and to programs starting or stopping, ignores their volume changes.
- Voice calls: recognition by executable, application name, Flatpak id and role, never browsers; PipeWire graph reading (links, link groups, monitors); the call kept out while every other program is linked and nothing is moved; idempotent; a program added during the call; filters and loopbacks never added; a call on a device Sunshine does not record changes nothing; no `pw-dump` without a call program; back to the device and the output removed when the call ends, in that order; the output kept while Sunshine still records it; a host-muting client keeps its bridge; removal on end and after a crash; module arguments that a description cannot break.
- Interface: labels, migration of the old "Other computer" choice, virtual outputs not offered, stale status rejected, test-tone results in words.
- Sunshine process: a zombie does not block a new start; a Sunshine that ignores SIGTERM is killed and collected.

## Reproducing the measurements

Use an isolated Sunshine directory. **Set `file_state`, `credentials_file` and `log_path` inside it**: without them `sunshine <dir>/sunshine.conf --creds` writes `~/.config/sunshine/sunshine_state.json`, replacing the credentials of the everyday Sunshine. Back up `~/.config/sunshine`, `~/.config/big-remote-play` and Moonlight's configuration first.

1. Create a lab null sink and move Moonlight's playback into it after the stream starts; otherwise the stream plays into the recorded output and echoes.
2. Start Sunshine with the lab configuration on another port, pair an isolated Moonlight configuration (`XDG_CONFIG_HOME`) to `127.0.0.1:<port>`.
3. For each scenario, `pactl list source-outputs` shows what `sunshine-record` records; `pactl get-default-sink` shows Sunshine's switch.
4. Record the lab sink's monitor and the device monitor with `parecord --raw` while playing a tone, and compare the tone level.
5. Remove the lab sink, check that the default output and microphone are unchanged and that no link from `sink-sunshine-*` remains (`pw-link -l`).

## Pending on target machines

- A real Steam Remote Play Together session with Sunshine sharing at the same time (host sound, guest sound, Steam's chosen source).
- X11 session (Sunshine's audio path does not depend on the display server, but it has not been measured there).
- HDMI/DisplayPort output of each GPU, Bluetooth headphones (A2DP and headset profiles), EasyEffects as default output.
- A physical second computer over the LAN and over a private network.
- 7.1 clients.
- Voice calls with real call programs (Discord, Zoom, Teams) and a real second computer; whether each one's stream carries the names Big Remote Play recognizes.
