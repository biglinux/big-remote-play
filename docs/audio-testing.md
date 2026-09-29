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

### Sunshine behavior (before the fix)

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

### Big Remote Play after the fix

| Scenario | This computer's `sunshine.conf` | Other computer | This computer's speaker | Sound graph afterwards |
|---|---|---|---|---|
| Automatic, client plays on host | `audio_sink` unset | −18.5 dB | −18.5 dB | nothing was written |
| Automatic, client mutes the host | unset | −18.9 dB | −19.0 dB (bridge) | link removed, output unchanged |
| Output changed to the headset during the stream | unset | −20.0 dB (capture followed) | −19.9 dB | unchanged |
| Only the other computer, 5.1 client mutes the host | `sink-sunshine-stereo` | −17.4 dB (bridge) | silent, as chosen | link removed |
| Sunshine killed during a host-muting session | unset | — | output restored by Big Remote Play | no links |
| 44.1 kHz source | unset | −19.6 dB | −19.7 dB | — |
| Output volume at 50 % | unset | −19.8 dB (unchanged) | — | volume restored |

The first bridge attempt used `module-loopback`. JamesDSP captured the loopback's stream and forwarded it to the default output — Sunshine's virtual output — which the loopback read: a feedback loop, measured as a louder stream and a silent speaker. Bridges are now PipeWire port links, which effects programs do not capture.

## Automated coverage

- Discovery: parser, non-ASCII descriptions, output kinds from properties, verified monitors, a microphone that carries a monitor-like name, no server, no outputs, unreadable server.
- Configuration: `audio_sink` for each mode, never a microphone or `@DEFAULT_AUDIO_SOURCE@`; unknown `sunshine.conf` options and `virtual_sink` preserved; previous file kept.
- Bridges: each measured Sunshine behavior, refusal into virtual outputs, no microphone source, cycle check, surround to stereo.
- Session: no writes in Automatic, idempotent reconcile, only our links removed (id and ports re-checked), restoring the output after a Sunshine crash, never overriding a newer choice, following an output change, moving a microphone capture back, leaving Steam's capture alone, private session record, crash recovery and adoption, removal of 2.x leftovers, corrupt records.
- Watcher: reacts to default and capture events, ignores application streams.
- Interface: labels, migration of the old "Other computer" choice, virtual outputs not offered, stale status rejected, test-tone results in words.
- Sunshine process: a zombie no longer blocks a new start; a Sunshine that ignores SIGTERM is killed and collected.

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
