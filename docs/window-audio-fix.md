# Game Window audio

Report: with **Game Window**, some games arrived on the other computer with noise, crackling or broken sound, while **Full Desktop** sounded right. This page records what was examined, what was measured and what changed. How sound is captured in general is in [audio architecture](audio-architecture.md); the Game Window design is in [Game Window](game-window.md).

## What differs between the two modes

Reading the code and Sunshine's own log of real sessions on the development machine:

| | Full Desktop | Game Window |
|---|---|---|
| Sunshine `audio_sink`, recorded monitor, Big Remote Play's audio session, bridges, call separation | same | same |
| Video capture | `kms` (no PipeWire video in Sunshine) | `kwin` on a private headless KWin, a **PipeWire** video stream inside Sunshine with variable frame rate and Sunshine's frame pacing |
| Encoder GPU (this machine) | the discrete GPU | libdrm's first GPU (the Renoir iGPU here), Vulkan encode |
| Private screen refresh | — | switched to about 240 Hz when `kscreen-doctor` is available |
| The game | usually fullscreen and the active window | a window; activated once when sharing started |

At the time of these measurements Big Remote Play's audio code did not depend on the mode and the whole output was recorded in both. Since 2026-10-02 Game Window sends only the game's sound by default: the game's process family — including launcher, Wine/Proton and child processes started under the same game launch — is linked into Big Remote Play's own output ([audio architecture](audio-architecture.md#game-window-only-the-games-sound)).

## Measurements (2026-10-01)

Isolated rig on the development machine (KDE Plasma 6.7 Wayland, PipeWire 1.6.8, WirePlumber 0.5.17, Sunshine 2026.914, Moonlight Qt 6.1; AMD RX 9060 XT + Renoir iGPU): a private PipeWire instance with only null sinks (no real device), a separate Sunshine (port 48989) recording a `game` sink that played a continuous 1 kHz tone at −20 dBFS, a separate Moonlight on the same machine playing into a `lab` sink, and the received tone analysed in 10 ms windows (amplitude drops > 6 dB, phase jumps > 0.3 rad, silences ≥ 1 ms) plus PipeWire's own error counters (`pw-top`) for 58 s per run. The Game Window runs used the same private screen as Big Remote Play (`kwin_wayland --virtual`, its own D-Bus) with an animated client, `capture = kwin`, `adapter_name = /dev/dri/renderD129`.

| Run | Capture | Encoder | Private screen | Client | Drops | Phase jumps | Silences ≥ 1 ms | PipeWire errors |
|---|---|---|---|---|---|---|---|---|
| S1a | kms | Vulkan, dGPU | — | 1280×720, 60 fps | 1 | 1 | 1 | 0 |
| S1b | kms | Vulkan, dGPU | — | 60 fps | 0 | 0 | 0 | 0 |
| S2a | kwin | Vulkan, iGPU | 60 Hz | 60 fps | 0 | 0 | 1 | 0 |
| S2b | kwin | Vulkan, iGPU | 60 Hz | 60 fps | 0 | 0 | 0 | 0 |
| S4a | kwin | Vulkan, iGPU | 60 Hz | 120 fps (8.3 ms pacing) | 0 | 0 | 0 | 0 |
| S3a | kwin | Vulkan, iGPU | 240 Hz | 60 fps | 0 | 0 | 0 | 0 |
| S3b | kwin | Vulkan, iGPU | 240 Hz | 120 fps | 0 | 1 | 1 | 0 |
| S3c | kwin | Vulkan, iGPU | 240 Hz | 120 fps | 1 | 0 | 1 | 0 |

The tone played into Sunshine was clean in every run. The received sound had at most one isolated event per minute in **both** modes, and PipeWire reported no error in any run.

**Conclusion of the tone measurements.** Sunshine's Game Window pipeline — PipeWire video capture with frame pacing, the iGPU encoder, the 240 Hz private screen, 60 or 120 fps — does not by itself break the sound: it behaves like Full Desktop. So the cause is not Big Remote Play's audio routing and not the capture/encode path; it is in what the rig did not have: **a real game running as a window**. One part of that is in Big Remote Play's hands and was fixed (below); the rest needs a test with the affected games.

## With a real game (2026-10-01)

*Shadow of the Tomb Raider* (Steam, Proton) at its main menu, on the development machine, with the app's own Game Window capture (KDE portal, mirror, private screen at 240 Hz, iGPU). The game's sound was recorded read-only from the monitor of the real output, and PipeWire's own counters were read with `pw-top`; focus was moved by KWin scripting. (*TMNT: Shredder's Revenge*, the game in the report, did not start from Heroic during the session.)

| Run | Capture | Game is the active window | Game's audio stream | Gaps / clicks in the sound | PipeWire errors |
|---|---|---|---|---|---|
| G1 | off | yes | present for 40 of 40 s (float32, 48 kHz, 256-sample latency) | 0 / 0 | 0 |
| G2 | off | no | **removed by the game**: silence for all 40 s | — | 0 |
| G5 | on | yes, for all 40 s (logged every second) | present for 40 of 40 s | — | 0 |

The game **destroys its audio stream as soon as it is not the active window** and creates a new one when it is again. The Game Window capture itself never took the focus (G5). So, on the other computer, Game Window sound depends entirely on the game staying the active window: every time something else becomes active — Big Remote Play after approving a device, a notification clicked, someone using the computer — the sound stops and then restarts with a new stream. With Full Desktop the game is normally fullscreen and keeps the focus. This matches the reported symptoms (sound missing, cut or restarting) and is what the change below addresses.

## With TMNT and a real second computer (2026-10-01)

*TMNT: Shredder's Revenge* (Heroic, Wine) shared as a Game Window to a second Linux computer on the same home network running Big Remote Play → Connect (Moonlight Qt). For 120 seconds the sound was recorded at the same time at three points — the game's own stream on the sharing computer, the output Sunshine records, and the output of the connecting computer — and the game's focus was logged every second:

- the game stayed the active window for the whole run;
- all three points carried the sound for all 120 seconds, second by second in step (the connecting computer about 5 dB louder, its own volume), with no silent second;
- the person playing reported continuous, clean sound.

Earlier in the same session that computer had no sound for a few minutes and then recovered by itself on the same connection, with no setting changed. Its Moonlight log showed audio packets arriving and, at the same time, *Audio packet queue overflow* / *Network dropped audio data* — packets piling up faster than they were played. During the clean run these messages still appeared in short bursts about every five minutes. The silent period was not reproduced; the measurements above locate it outside Big Remote Play's routing and Sunshine's capture, on the receiving side or the network. *Also play sound on the game PC* (Moonlight's *audio on host*) was on throughout and is not the cause.

## What changed

- **The game is brought to the front again every time a device starts playing** (`window_capture.activate_game`, called from Share when **Connected now** gains a device). Before, the game was activated once when sharing started. Approving the device — now a dialog in Big Remote Play — moved the focus to Big Remote Play just before the stream began, so the game played while not the active window: many engines lower their frame rate or mute/duck sound in the background (for example Source's *mute when not focused*, Unity's *run in background* off, Wine games that minimize when they lose focus), and the other person's keys went to Big Remote Play. Full Desktop games normally keep the focus because they are fullscreen. Covered by `tests/test_game_window_focus.py`.

## Silence on the other computer (2026-10-02)

A later report — no sound at all on the other computer while the game played here — had another cause, not related to Game Window: Sunshine's recording was restored muted by the session manager. See [remote audio silence investigation](remote-audio-silence-investigation.md).

## Still to verify on a target machine

With *TMNT: Shredder's Revenge* and a real second computer ([release acceptance](release-testing.md)):

1. that the sound stays continuous for the whole session now that the game is reactivated when the device connects, as long as nobody uses this computer meanwhile;
2. the game's own options for sound in the background, if it has them;
3. whether the sound is also wrong on the sharing computer itself (then it is the game's own playback under load, before Sunshine);
4. CPU and GPU load: Game Window composites the game as a window and encodes on the private screen's GPU, which on a single-GPU computer is the GPU the game uses.

## Not changed

- Full Desktop capture and audio are untouched.
- The sound problems measured here are unrelated to what is recorded. Sending only the game's sound came later, on request; it follows the game's whole process family so Proton, Wine, launcher and child-process sound keep working.
