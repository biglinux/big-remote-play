# Audio architecture

How the game PC's sound reaches the other computer, what Big Remote Play changes in the sound system and what it never touches. The behavior a person sees is described in the [user guide](user-guide.md#audio-behavior); how each statement here was verified is in [audio testing](audio-testing.md).

## The one rule

**The other computer hears what this computer plays. The microphone is not part of the stream.**

Sunshine records the *monitor* of an output — a copy of the sound going to a device — and never a microphone, line-in or other input. Big Remote Play never configures a microphone for Sunshine, never loops a microphone into an output and never enables microphone monitoring. Voice chat keeps working in its own apps; it is not carried by Sunshine.

One kind of playback is kept out as well: a **voice call**. A call program plays the voices of everyone in the call, including the person connecting, so the monitor carried their own voice back to them. See [Voice calls](#voice-calls-stay-out-of-the-stream).

## How Sunshine captures sound on Linux

Measured with Sunshine 2026.914 on PipeWire 1.6.8 (pipewire-pulse, WirePlumber 0.5.17), matching Sunshine's `src/audio.cpp` and `src/platform/linux/audio.cpp`:

| `audio_sink` in sunshine.conf | Client plays sound on the host | Client asks to mute the host |
|---|---|---|
| **not set** | records the monitor of the current default output; nothing changes | makes its own `sink-sunshine-stereo` (or `-surround51/71`) the **system default output** for the session, records its monitor, restores the default when the session ends normally |
| a device name | makes that device the default for the session and records its monitor | switches the default to its virtual output **but keeps recording the configured device**: applications follow the default and the other computer hears silence |
| a missing name | the other computer hears silence | silence |

Sunshine creates its three virtual outputs when it starts. `virtual_sink` is not used by Sunshine on Linux. When Sunshine is killed during a session that switched the default, the default output stays on its virtual output and the whole desktop goes silent until something restores it.

## What Big Remote Play writes

| Where | Automatic, play here (default) | Automatic, only the other computer | A chosen device |
|---|---|---|---|
| `sunshine.conf` `audio_sink` | removed | `sink-sunshine-stereo` | that device |
| Default output | never changed by Big Remote Play | Sunshine switches it during sessions and restores it | Sunshine switches it during sessions and restores it |
| Application streams | never moved | never moved | never moved |
| Sunshine's recording stream | unmuted and at 100 % when found otherwise (below) | the same | the same |
| Port links | only while a client mutes the host (below) | only for a surround client (below) | only while a client mutes the host (below) |
| While a call program plays into the recorded output | its own output with every other program linked into it; Sunshine records it | the same | the same |
| Game Window, **Send only the game's sound** (default) | its own output with only the game linked into it; Sunshine records it | the same | the same |

`sunshine.conf` is merged, never replaced: unknown options, the apps file, pairings and credentials stay as they are, and the previous file is kept as `sunshine.conf.previous` (0600) whenever the content changes.

A chosen device must be a hardware or Bluetooth output. Sunshine makes it the default output; a virtual processing output such as EasyEffects or JamesDSP forwards to the default output, so choosing it would make it feed itself. Virtual outputs are captured through **Automatic**, which records the device they forward to (with effects) or, when the virtual output is itself the default, its own monitor.

## Sunshine's recording level

The session manager (WirePlumber's stream restore) remembers a mute and volume per application name and applies them to **every new stream** with that name. Muting or turning down "sunshine" once — in the Recording tab of the Plasma volume applet or pavucontrol — therefore silenced every later session: Sunshine opened the right monitor and encoded silence, while this computer kept playing the game. Measured on 2026-10-02 ([investigation](remote-audio-silence-investigation.md)): `sunshine-record` restored at *muted, 62 %*; a recording of the same monitor under another name carried the game at −18.5 dBFS.

`reconcile` reads each stream's `Mute` and `Volume`. When Sunshine's recording is muted or below 100 %, it unmutes that stream and sets 100 % (`pactl set-source-output-mute` / `set-source-output-volume`, that stream only); WirePlumber then saves the corrected level for the next session. A recording muted again is restored at most three times, so a deliberate mute is not fought; **Test audio** allows it once more. Other programs' playback, microphone captures and Steam's captures keep their levels.

## Bridges: the only routing Big Remote Play adds

Two measured Sunshine behaviors need help. A *bridge* links the monitor ports of one output directly to the playback ports of another, channel by channel, with `pw-link`:

1. **"Also play sound on this computer" with a client that asks to mute the host.** Sunshine moved the default to its virtual output, so this computer went silent. Bridge: Sunshine's virtual output → the device the person was using.
2. **Sunshine records a different output than the one it made default** (a chosen device, or "only the other computer" with a 5.1/7.1 client). Bridge: the new default → the recorded output. Surround into stereo keeps the front pair.

Bridges target only hardware/Bluetooth devices or Sunshine's own outputs, and a cycle check refuses any bridge that would reach its own source. A bridge into a virtual output is refused (it would feed itself); the details say so and this computer stays silent for that client.

Port links, not `module-loopback`: on the test machine JamesDSP pulled *every* new playback stream — even one created with an explicit target — into its own sink, which forwarded to the default output, which was Sunshine's virtual output. A loopback stream therefore formed a feedback loop. A port link is not a stream, so no effects program can capture it.

## Voice calls stay out of the stream

A call program on this computer (Discord and its clients, Fluxer, Zoom, Teams, Skype, Slack, Telegram, Signal, Element, Mumble, TeamSpeak, Jami, WhatsApp clients…) plays everyone's voice, the other person's included. Measured in an isolated PipeWire 1.6.8 instance: with a call playing through an effects program into the recorded device, Sunshine's capture carried the call at −14 dB next to the game.

While Sunshine records and a call program's sound reaches the output Sunshine records:

1. Big Remote Play loads its own output, `big-remote-play-stream` (`module-null-sink`, same channels as the recorded output, `big-remote-play.owner=<session token>`, shown in the sound settings as “Big Remote Play: sound sent to the other computer”).
2. Every other program whose sound reaches the recorded output gets port links from its output ports into that output (by port id, tagged with the token). Only programs themselves: a node that others play into (an effects filter) or one half of a loopback, combined output or filter chain (they share a `node.link-group`) forwards sound that is linked already, the call included.
3. Sunshine's capture stream is moved to `big-remote-play-stream.monitor`, a monitor like any other.

No application stream is moved and no default changes: this computer hears the call, the game and its effects as before. Whether a sound reaches the recorded output is read from PipeWire's own graph (`pw-dump`: links, plus the internal connection of a link group); `pw-dump` runs only while a call program has a stream, or in Game Window while a device receives sound. A call on another device (headphones Sunshine does not record) is left alone: it was never sent.

Call programs are recognized by `media.role` `phone`/`communication`, or by executable, application name or Flatpak id against a fixed list. A call in a web browser is part of the browser's single stream and cannot be separated; it is still sent.

When the call ends (the program closes its stream) or the client disconnects, the capture is moved back to the monitor of the output Sunshine had chosen, and only then the output is removed: removing it while Sunshine records it would leave the choice of a new source to the session manager. Streams starting or ending (`sink-input` `new`/`remove` events) trigger a check; their volume changes do not.

Measured in the isolated instance (tone levels on Sunshine's capture; this computer's device in the last column):

| Scenario | Call | Game | This computer |
|---|---|---|---|
| Before | −14 dB | −8 dB | both |
| Call program playing through an effects program (port links) | −284 dB | −8 dB | both at the same level |
| The same through a `module-loopback` (link group) | −284 dB | −8 dB | both |
| Client muted the host, **Also play sound on this computer** on | −297 dB | −23 dB, unchanged | both, through the bridge |
| The call ended | −283 dB (none) | −8 dB | — ; capture back on the device monitor, output removed |

Links carry `big-remote-play.owner=<session token>`. Their ids and exact port names are kept in the session record and are removed only while each id still joins the same two ports. Other programs' links, loopbacks and virtual outputs are never removed.

## Game Window: only the game's sound

With **Game Window** the picture is one game; by default the sound is too. It uses the same private output as [voice calls](#voice-calls-stay-out-of-the-stream), `big-remote-play-stream`, with only the game linked into it:

1. **Which program is the game.** The window's process and every process started under the same game launch: from the window's process up to Steam's per-game `reaper` (`SteamLaunch AppId=…`), Lutris's `lutris-wrapper`, or the last Wine/Proton/runtime wrapper (`*.exe`, `wine*`, `pv-adverb`, `srt-bwrap`, `pressure-vessel`, `gamescope`…) before a launcher, shell or the desktop; then all its descendants (from `/proc/*/stat`, read again at each check). A native game started from the desktop is its own process and its children. Measured with *Street Fighter V* (Steam, Proton): 17 processes, `reaper` down to the game, `wineserver` and Wine's services, and neither Steam nor the browser. A playback stream belongs to the game when its `application.process.id` is in that family, or — for a sandbox that reports another PID — when its application name is the game's executable, name or window title.
2. **Linking.** Each of the game's playback streams gets port links (tagged with the session token) from its output ports into the private output, wherever the game plays (its device, an effects program, Steam's recorder). Call streams, Sunshine and filter halves are never linked. Sunshine's capture is moved to the private output's monitor. No stream is moved, so this computer hears everything as before, with its effects; the game is sent before EasyEffects or JamesDSP.
3. **Changes.** A game that closes its stream when it loses focus and makes a new one later is followed (stream `new`/`remove` events). While the game plays nothing, the other computer gets silence, never the desktop. If PipeWire's graph cannot be read, what Sunshine records is left as it is and the interface says *Sending all of this computer's sound: the game's sound could not be separated*.
4. **End.** As for calls: Sunshine back on the output monitor, then the private output removed.

Measured on 2026-10-02 in an isolated PipeWire 1.6.8 + WirePlumber instance (no real device): a "game" process (660 Hz) and another program (1000 Hz) playing to the same output, and a capture with Sunshine's names. Before: both tones at −20 dB in the capture. With only the game: the game at −20.0 dB, the other program at −305 dB (digital silence), this computer's output still both at −20 dB. After the end: the capture back on the output monitor, the private output removed.

The first fraction of a second of a new connection (until the next check, about 0.4 s) is recorded from the output, because Sunshine opens its recording on the output monitor itself. The setting **Send only the game's sound** in Preferences → Audio turns this off; Full Desktop always sends everything this computer plays.

## Session lifecycle

`AudioRoutingSession` (`utils/audio.py`) owns one sharing session:

- **begin** — records the output in use and writes the session record. Nothing is written to the sound server.
- **reconcile** — runs when the sound server reports a change (`pactl subscribe`: server, sink, source and capture-stream events; debounced 0.4 s, with a 15 s safety check; application volume and stream events are ignored). It adds or removes bridges, and:
  - in Automatic, if the person picks another output during the stream, moves Sunshine's capture stream to that output's monitor (the new choice is followed, never overridden);
  - if Sunshine were ever recording a non-monitor source, moves it back to the output monitor and reports it;
  - unmutes Sunshine's recording and sets it to 100 % when a saved level made it quieter ([above](#sunshines-recording-level)).
- **status** — what the interface shows comes from measured facts only: whether Sunshine records, which source, its mute and volume, and what Sunshine's own log says about the latest session (`Found default monitor by name`, `Opus initialized`, `pa_simple_new() failed`, `Unable to initialize audio capture`; only the end of the log is read). **Sound** on Overview and **Sound for the other computer** in Preferences say *Sending…*, *Ready…*, or why nothing is sent.
- **logging** — `[AUDIO]` lines only when a fact changes: default output, programs playing, what each Sunshine recording records and its level, a restored level, Sunshine's log summary.
- **end** — after Sunshine stops: removes this session's links and, only if the default output is still a Sunshine virtual output (Sunshine could not restore it), restores the output the person was using. A default the person chose is left alone. Then it reconnects programs that Sunshine's exit left playing into nothing (below).

### Programs left without an output when Sunshine stops

An effects program such as JamesDSP links its own output ports to the default output and moves them when the default changes. While a client mutes this computer, that default is Sunshine's virtual output. When Sunshine exits it sets the default back and removes its outputs at the same moment; measured on 2026-09-29, JamesDSP then failed (`unknown resource`) and left its output linked to nothing. The default output was correct, but everything played through JamesDSP was silent until the next login. Setting the same default again does not wake it; a link does.

Therefore, while a Sunshine output exists (each reconcile and once more right before **Stop sharing** stops Sunshine), the session records the **port names** of other programs' outputs that play into a Sunshine output: not Sunshine's own ports, not our bridges. After Sunshine stops, each recorded port that still exists and still has no link at all after 2 s — enough for the program to follow the output by itself — is linked to the default output's playback port of the same channel (front channels into a mono device). Only when the default output is a device (hardware or Bluetooth): a virtual default usually forwards to the program itself. These links restore the person's own chain, so they are not tagged as ours and are never removed; the program replaces them the next time it moves (JamesDSP did, measured). A port that is linked anywhere is left alone.

The session record lives at `$XDG_RUNTIME_DIR/big-remote-play/audio-session.json` (0600): owner PID, token, previous output, mode, link ids, the recorded port names and the module index of the call-free output (removed only while its arguments still carry the token). No other object id is stored; which output a moved capture came from is kept in memory only, and an adopted session uses the output in use. On the next start, a record whose owner is gone is either **adopted** (Sunshine still sharing) or **cleaned up** (links removed, output restored). Null sinks and loopbacks named `SunshineGameSink`, `SunshineStereo`, `SunshineHybrid` or `SunshineLoopback` are left only by Big Remote Play 2.x; they are removed on start when Sunshine is not sharing, as is a `big-remote-play-stream` output left by a window that crashed together with its record.

## Identifying outputs, monitors and streams

- The sound server is read with `pactl list` in the C locale. The text form is used because `pactl -f json` 17 drops non-ASCII descriptions.
- An output's kind comes from its properties: `device.api=alsa` or the `HARDWARE` flag → device; `bluez5` → Bluetooth; `sink-sunshine-*` → Sunshine; `steam.autounload` or "Steam Streaming" → Steam; our token → ours; anything else (EasyEffects, JamesDSP, combined or network outputs) → virtual.
- A monitor counts only when the source says **Monitor of Sink: <output>**. Appending `.monitor` to a name is never trusted; without a verified monitor the interface says **System audio unavailable** instead of falling back to any input.
- Sunshine's capture is the stream whose `application.process.binary` is `sunshine` (or `application.name`=`sunshine` with `media.name`=`sunshine-record`). Steam's capture streams are matched by the `steam`/`steamwebhelper` binaries.

## Coexisting with Steam Remote Play Together

Steam's host recorder (`gamestream/audiorecorder.cpp` in `steamui.so`) records per process: it loads `module-combine-sink … sink_properties=steam.autounload=true slaves=<the game's output>`, moves the game's stream into it by `application.process.id`, and records that monitor. When rerouting fails it falls back to system recording of the default output's monitor. On PipeWire it can also create "Steam Streaming Playback" and "Steam Streaming Capture" nodes.

The previous explicit routing moved every application stream back into `SunshineGameSink` once per second. That undid Steam's reroute within half a second, so Steam recorded silence while the Big Remote Play client still heard the game. Big Remote Play no longer moves application streams at all, and Steam's outputs are never offered, followed or bridged into. The technical details show which source Steam records and whether it is a monitor or a microphone.

## Device changes and multiple outputs

- Unplugging a recorded device: WirePlumber 0.5 moved a monitor capture to the new default output's monitor, not to the microphone (measured). The reconcile step still verifies the result.
- HDMI/DisplayPort outputs of several GPUs, USB and Bluetooth devices are plain outputs; the default is whatever the desktop selected.
- The volume slider of this computer does not change what the other computer hears: the monitor is taken before the device volume (measured at 100 % and 50 %).
- 44.1 kHz and 48 kHz sources reach the client at the same level; Sunshine encodes Opus at 48 kHz and the sound server resamples.

## On the connecting computer

Moonlight is started with `stream <address> Desktop`, the resolution, frame rate, bitrate and display mode, `--audio-on-host` or `--no-audio-on-host`, and `--video-decoder`. Nothing on that command line turns sound off or picks a device; the layout (stereo, 5.1, 7.1) and *mute when Moonlight is not the active window* are Moonlight's saved settings.

Moonlight's output is read for its own sound messages: `Received first audio packet`, `Failed to open audio device`, `No audio traffic was ever received from the host!`, `Audio packet queue overflow`, `Network dropped audio data`. Loss messages arrive in bursts; the first is logged and the rest are summarized every 30 s, and more than 50 in one interval is told to the person once (*Sound from the game PC is being lost on the way*).

After the stream starts, Big Remote Play waits — for Moonlight's playback stream or a failure message, at most 10 s — and checks the stream. The same per-application restore applies here: a Moonlight stream restored **muted** is unmuted; its volume is the person's listening level and is left alone. A device Moonlight could not open, or no Moonlight stream at all, is said in words.

## Limitations

- A client that mutes the host while the person uses a virtual output as default (EasyEffects/JamesDSP) leaves this computer silent for that session; a bridge would feed itself.
- Surround bridged into stereo keeps only the front pair.
- During a voice call the other computer hears each program before the effects of EasyEffects or JamesDSP, and a call in a web browser is still sent.
- Game Window sends the game without the effects of EasyEffects or JamesDSP, and a game whose sound is played by an unrelated process (an external audio server, a browser) is not sent.
- Muting Sunshine's recording on purpose is undone up to three times per recording; to silence the other device, mute Moonlight there.
- Measurements were made on one machine (KDE Plasma 6.7 on Wayland, AMD GPUs, USB outputs, JamesDSP). X11 sessions, Bluetooth, HDMI, EasyEffects and a real Steam Remote Play Together session still need target-machine testing; see [audio testing](audio-testing.md).
