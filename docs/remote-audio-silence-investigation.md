# Remote audio silence investigation

Report (2026-10-02): sharing a game as **Game Window**, the other computer received the picture and the controls worked, but it had **no game sound**. The game kept playing normally on the sharing computer. This page records what was examined on the real machine, where the sound disappeared, why, and what changed. The audio design is in [audio architecture](audio-architecture.md); earlier Game Window findings are in [Game Window audio](window-audio-fix.md).

## Result

| Point in the chain | Observed | How |
|---|---|---|
| Game stream | **present**: `gauntlet.exe` (Wine), 4 streams, s16le 44.1 kHz stereo, not muted, 130 % | `pactl list sink-inputs` |
| Game → output | **present**: game → `jamesdsp_sink` → JamesDSP → `sink-sunshine-stereo` (the default while the client muted the host) → Big Remote Play's bridge → USB device | `pw-link -l` |
| Output monitor | **sound present**: −29.5 dBFS | `parec` on the monitor |
| Sunshine opens the right source | **yes**: `Found default monitor by name: sink-sunshine-stereo.monitor`, `Opus initialized: 48 kHz, 2 channels` | Sunshine log |
| Sunshine's recording stream (`sunshine-record`) | **Mute: yes, volume 62 % (−12.46 dB)**, every one of them | `pactl list source-outputs` |
| What a recording of that monitor named "sunshine" receives | **digital silence**; the same monitor recorded under another name, at the same moment: −18.5 dBFS | two `parec` probes |
| Network, Moonlight, client output | not reached: Sunshine encoded silence | — |

**Root cause.** WirePlumber's stream restore had saved, for `Input/Audio:application.name:sunshine`, `"mute": true` and channel volumes 0.238 (62 %) in `~/.local/state/wireplumber/stream-properties`. Like every per-application level, it is applied to **every new stream with that name**: each `sunshine-record` that Sunshine opened for a connecting device started muted. Sunshine opened the correct monitor and sent Opus packets of silence; the game went on playing on this computer because the speakers are fed by the output, not by Sunshine's recording. Changing `audio_sink`, reconnecting or restarting Sunshine could not help: the next recording was muted again. This was observed twice in a row on the machine — the second time after the person restarted sharing with a chosen output device (`audio_sink` = the USB device): new stream `#4829`, same mute and 62 %.

How the mute was saved is not recorded. Any mixer writes it: the Recording tab of the Plasma volume applet or pavucontrol lists Sunshine as a program recording, and muting or turning it down there is remembered for the application name. Earlier measurement rigs used their own `XDG_STATE_HOME`, and none of the recorded sessions changed a recording level.

**Why Big Remote Play did not notice.** It checked *which* source Sunshine recorded (a verified monitor, never a microphone) but not the recording's own mute and volume, so the technical details showed a correct source while nothing was sent.

## Hypotheses tested

| Hypothesis | Verdict | Evidence |
|---|---|---|
| The game destroys its stream when it loses focus ([earlier finding](window-audio-fix.md)) | not this case | the game's 4 streams were present and playing; this computer heard it |
| Sunshine records a microphone or a wrong/stale source | no | Sunshine's capture was on the monitor of the default output, then on the chosen device's monitor |
| `audio_sink` persisted wrongly or naming a missing device | no | Automatic: `audio_sink` absent; later the chosen USB device, present |
| Race: Sunshine creates its recording before the default output changes | no | the log shows `Setting default sink` before `Found default monitor`, and the capture followed the right monitor |
| Game Window changes Sunshine's audio configuration | no | both modes write the same `stream_audio` and `audio_sink`; Game Window changes only `capture`, `output_name` and `adapter_name` |
| Game Window load or the 240 Hz private screen starves audio | no | [measured earlier](window-audio-fix.md#measurements-2026-10-01) with a clean tone at 60/240 Hz; here the sound was already missing inside Sunshine's own stream |
| Network loss or Moonlight queue overflow | not this case | Sunshine sent silence; the loss messages explain short gaps, not a silent session ([TMNT run](window-audio-fix.md#with-tmnt-and-a-real-second-computer-2026-10-01)) |
| Moonlight started without sound (`--no-audio`, a wrong device) | no | the real command line is `moonlight stream <address> Desktop --resolution … --fps … --bitrate … --display-mode … --audio-on-host`/`--no-audio-on-host` `--video-decoder auto`: nothing turns sound off; the layout (stereo/5.1/7.1) and *mute when not the active window* come from Moonlight's saved settings |
| **A saved mute/volume applied to Sunshine's recording** | **confirmed** | above; fixing the level brought the sound back and WirePlumber then saved `"mute": false, "volume": 1.0` |

Other failures seen in the same machine's Sunshine logs (older sessions, February): `Found default monitor by name:` with an empty name, then `pa_simple_new() failed: Invalid argument` and `Unable to initialize audio capture. The stream will not have audio.` (33 times). Those sessions had no sound at all either; Big Remote Play now reports this case too.

## Commands

```bash
LC_ALL=C pactl list sink-inputs        # the game's streams, their output, mute and volume
LC_ALL=C pactl list source-outputs     # sunshine-record: source, Mute, Volume
pw-link -l                              # the real path game → effects → output → monitor
grep -n "application.name:sunshine" ~/.local/state/wireplumber/stream-properties
grep -n "New streaming session\|Found default monitor\|Opus initialized\|Unable to initialize audio" \
  ~/.config/big-remote-play/sunshine/sunshine.log
# Same monitor, same moment: as "sunshine" (restored mute applies) and as a control
parec --raw --format=s16le --rate=48000 --channels=2 -d <monitor> --client-name=sunshine --stream-name=probe > a.raw
parec --raw --format=s16le --rate=48000 --channels=2 -d <monitor> --client-name=control  --stream-name=probe > b.raw
```

The probes used a media name other than `sunshine-record`, so Big Remote Play did not take them for Sunshine.

## Architecture before and after

Before: `reconcile` kept Sunshine's recording on a verified monitor (following the output in Automatic, moving it off a microphone, keeping calls out) and never looked at the recording's level. The interface showed the recorded source only.

After:

- **Host.** `pactl list` is also read for each stream's `Mute` and `Volume`. When Sunshine's recording is muted or below 100 %, `reconcile` unmutes it and sets 100 % (`set-source-output-mute`, `set-source-output-volume` on that stream only). It reacts within 0.4 s, because the session manager's change arrives as a `source-output` event. A recording that someone mutes again is restored at most three times; after that it is left as chosen and the interface says it is muted, until **Test** is pressed. A new recording (Sunshine creates one per device) is checked again. Restoring the level also makes WirePlumber save the corrected level, so the next session starts right. No program's playback, no microphone capture and no Steam capture is touched.
- **What the person sees.** **Share → Overview** has a **Sound** row in *Sharing now*, and **Preferences → Audio** has **Sound for the other computer**, both from measured facts: *Sending the sound this computer plays*, *Ready…*, *Not sent: Sunshine's recording is muted…*, *Not sent: Sunshine could not open this computer's sound…*, *Not sent: Sunshine is recording a microphone…*. **Technical audio details** adds **Sunshine recording level** and **Sunshine log** (the monitor it opened and the encoder, or the error).
- **Test audio** while a device plays: re-allows the restore, plays the tone, measures it on the monitor, then checks that Sunshine's recording is on that monitor and not muted: *The tone reached the shared sound … and Sunshine is recording it. If the other device still hears nothing, the problem is on that device or the network.*
- **Connecting computer.** Moonlight's own messages are classified (`Received first audio packet`, `Failed to open audio device`, `No audio traffic was ever received from the host!`, `Audio packet queue overflow`, `Network dropped audio data`). After the stream starts, Big Remote Play waits for Moonlight's playback stream to appear (or for a failure message, at most 10 s) and checks it: a Moonlight stream restored muted is unmuted (its volume is the person's listening level and is left alone). It says in words when Moonlight could not open the sound device, does not play here, or when sound is being lost on the way.
- **Logs.** `[AUDIO]` lines, written only when a fact changes: default output, programs playing, what each Sunshine recording records and its level, a restored level, Sunshine's log summary, Moonlight's sound check. Bursts of Moonlight loss messages are written once, then summarized every 30 s.

## Files changed

- `src/big_remote_play/utils/audio.py` — stream mute/volume, level restore in `reconcile`, `stream_audio_state`, Sunshine log parser, `capture_check` in the tone test, `[AUDIO]` change log.
- `src/big_remote_play/guest/moonlight_audio.py` (new) — Moonlight message classification and the playback check.
- `src/big_remote_play/guest/moonlight_client.py` — per-connection sound report, collapsed loss lines.
- `src/big_remote_play/ui/host_view.py` — **Sound** rows, details, Test.
- `src/big_remote_play/ui/guest_view.py` — the check after the stream starts, the loss notice.
- `tests/test_audio.py`, `tests/test_moonlight_audio.py` (new), `tests/test_home_image_audio_policy.py`.
- Catalogs in `locale/` and `usr/share/locale/`; the audio documents.

## Tests

Automated (fake sound server, see [audio testing](audio-testing.md#automated-coverage)): the reported case with Sunshine's own output as default and the bridge in place; a recording only turned down; no fight with a deliberate mute; a recording Sunshine creates again; other programs', microphones' and Steam's levels untouched; speakers → headset/Bluetooth; a removed output; Sunshine's log (success, `pa_simple_new` failure, previous runs, large files); the tone test's capture check; Moonlight messages, collapsed loss bursts, the muted Moonlight stream, a missing stream, a device failure, the end of the connection; the Share rows' four states and stale results. The main regression test fails with the restore disabled.

Real machine (2026-10-02, the live session of the report): the checkout's `restore_capture_level` on the live `sunshine-record #4829` changed it from *muted, 62 %* to *not muted, 100 %*; WirePlumber saved `"mute": false, "volume": 1.0`; a new recording named "sunshine" of the same monitor then received −25.1 dBFS instead of silence.

## Limitations

- The game PC can prove that its sound reaches Sunshine's recording, unmuted, on the right monitor. Whether it arrives on the other device is only known there (the Connect side checks Moonlight's own messages and playback stream).
- Mute on purpose: muting Sunshine's recording in a mixer during a session is undone at most three times per recording; a person who wants the other device silent should mute Moonlight there.
- A Moonlight stream that plays turned down (not muted) on the connecting computer is reported in the log only; it is the person's listening level.
- Not re-measured in this investigation with a second physical computer after the change; the person playing confirms by ear. X11, NVIDIA, Bluetooth and HDMI remain as listed in [audio testing](audio-testing.md#pending-on-target-machines).
