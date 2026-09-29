# Audio architecture

How the game PC's sound reaches the other computer, what Big Remote Play changes in the sound system and what it never touches. The behavior a person sees is described in the [user guide](user-guide.md#audio-behavior); how each statement here was verified is in [audio testing](audio-testing.md).

## The one rule

**The other computer hears what this computer plays. The microphone is not part of the stream.**

Sunshine records the *monitor* of an output — a copy of the sound going to a device — and never a microphone, line-in or other input. Big Remote Play never configures a microphone for Sunshine, never loops a microphone into an output and never enables microphone monitoring. Voice chat keeps working in its own apps; it is not carried by Sunshine.

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
| Port links | only while a client mutes the host (below) | only for a surround client (below) | only while a client mutes the host (below) |

`sunshine.conf` is merged, never replaced: unknown options, the apps file, pairings and credentials stay as they are, and the previous file is kept as `sunshine.conf.previous` (0600) whenever the content changes.

A chosen device must be a hardware or Bluetooth output. Sunshine makes it the default output; a virtual processing output such as EasyEffects or JamesDSP forwards to the default output, so choosing it would make it feed itself. Virtual outputs are captured through **Automatic**, which records the device they forward to (with effects) or, when the virtual output is itself the default, its own monitor.

## Bridges: the only routing Big Remote Play adds

Two measured Sunshine behaviors need help. A *bridge* links the monitor ports of one output directly to the playback ports of another, channel by channel, with `pw-link`:

1. **"Also play sound on this computer" with a client that asks to mute the host.** Sunshine moved the default to its virtual output, so this computer went silent. Bridge: Sunshine's virtual output → the device the person was using.
2. **Sunshine records a different output than the one it made default** (a chosen device, or "only the other computer" with a 5.1/7.1 client). Bridge: the new default → the recorded output. Surround into stereo keeps the front pair.

Bridges target only hardware/Bluetooth devices or Sunshine's own outputs, and a cycle check refuses any bridge that would reach its own source. A bridge into a virtual output is refused (it would feed itself); the details say so and this computer stays silent for that client.

Port links, not `module-loopback`: on the test machine JamesDSP pulled *every* new playback stream — even one created with an explicit target — into its own sink, which forwarded to the default output, which was Sunshine's virtual output. A loopback stream therefore formed a feedback loop. A port link is not a stream, so no effects program can capture it.

Links carry `big-remote-play.owner=<session token>`. Their ids and exact port names are kept in the session record and are removed only while each id still joins the same two ports. Other programs' links, loopbacks and virtual outputs are never removed.

## Session lifecycle

`AudioRoutingSession` (`utils/audio.py`) owns one sharing session:

- **begin** — records the output in use and writes the session record. Nothing is written to the sound server.
- **reconcile** — runs when the sound server reports a change (`pactl subscribe`: server, sink, source and capture-stream events; debounced 0.4 s, with a 15 s safety check; application volume and stream events are ignored). It adds or removes bridges, and:
  - in Automatic, if the person picks another output during the stream, moves Sunshine's capture stream to that output's monitor (the new choice is followed, never overridden);
  - if Sunshine were ever recording a non-monitor source, moves it back to the output monitor and reports it.
- **end** — after Sunshine stops: removes this session's links and, only if the default output is still a Sunshine virtual output (Sunshine could not restore it), restores the output the person was using. A default the person chose is left alone.

The session record lives at `$XDG_RUNTIME_DIR/big-remote-play/audio-session.json` (0600): owner PID, token, previous output, mode and link ids. No other object id is stored. On the next start, a record whose owner is gone is either **adopted** (Sunshine still sharing) or **cleaned up** (links removed, output restored). Null sinks and loopbacks named `SunshineGameSink`, `SunshineStereo`, `SunshineHybrid` or `SunshineLoopback` are left only by Big Remote Play 2.x; they are removed on start when Sunshine is not sharing.

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

## Limitations

- A client that mutes the host while the person uses a virtual output as default (EasyEffects/JamesDSP) leaves this computer silent for that session; a bridge would feed itself.
- Surround bridged into stereo keeps only the front pair.
- Measurements were made on one machine (KDE Plasma 6.7 on Wayland, AMD GPUs, USB outputs, JamesDSP). X11 sessions, Bluetooth, HDMI, EasyEffects and a real Steam Remote Play Together session still need target-machine testing; see [audio testing](audio-testing.md).
