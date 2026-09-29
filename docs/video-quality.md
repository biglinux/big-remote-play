# Video quality: the real pipeline and what decides the picture

Big Remote Play does not capture, encode or decode video itself. It configures **Sunshine** on the sharing computer and **Moonlight** on the connecting computer, then starts them. This page describes the pipeline they run, what Big Remote Play sets in it, and what was measured. The user-facing options are in the [user guide](user-guide.md#share-a-game); diagnosis is in [troubleshooting](troubleshooting.md#colors-look-grey-or-washed-out-on-the-other-computer).

## The pipeline

```text
Sharing computer (Sunshine)                                    Connecting computer (Moonlight Qt)
screen ─ capture (KMS | portal | wlr | x11 | NvFBC) ─ colour conversion (RGB → YUV 4:2:0 or 4:4:4,
  BT.601/709/2020, full or limited range) ─ scaling to the client's resolution ─ hardware encoder
  (NVENC | Vulkan | VAAPI | software; H.264 / HEVC / AV1) ─ RTP over UDP + FEC ──▶ FFmpeg decoder
  (VAAPI | Vulkan | software) ─ YUV → RGB with the stream's colour space and range ─ SDL/Vulkan output
```

There is no second encode: Moonlight shows what Sunshine encoded. The **client** asks for resolution, frame rate, bitrate, codec, HDR and 4:4:4 when the stream starts; Sunshine chooses the colour space and range for that request and says it in its log (`Color coding`, `Color depth`, `Color range`, `Streaming bitrate`), which **Share → Connected now → (i)** shows as, for example, `HEVC (VULKAN) · SDR (Rec. 709) · 8-bit · limited range · 15.0 Mbps · capture: KMS`.

| Setting | Owner | Big Remote Play default |
|---|---|---|
| Capture method, screen, encoder backend | Share → Image and capture → `capture`, `output_name`, `encoder` | Automatic (Sunshine probes NVENC, Vulkan, VAAPI, software, in that order) |
| HEVC/AV1 offered | `hevc_mode`, `av1_mode` | `0` = automatic by encoder capability (`1` would disable them) |
| Encoder preset | `nvenc_preset`, `amd_quality`, `sw_preset` | balanced |
| Error correction | `fec_percentage` | 20 % (30 % in unstable network mode); Sunshine takes it out of the requested bitrate |
| Host bitrate ceiling | `max_bitrate` | 0 = follow the client |
| HDR screens, resolution | `global_prep_cmd` (below) | SDR for SDR clients on; resolution matching off |
| Resolution, FPS, bitrate, codec, decoder, HDR, 4:4:4 | Connect → Image | follows this screen; 1080p60 ≈ 20 Mbps, 1440p60 ≈ 40 Mbps, 4K60 ≈ 60 Mbps (less on Wi-Fi) |

## Washed-out colours: HDR screens captured as SDR

When KDE Plasma drives a monitor in **HDR**, the image sent to that monitor is PQ-encoded BT.2020. Sunshine's KMS capture reads exactly those pixels. For a client that asked for SDR — the usual case — Sunshine labels the stream `SDR (Rec. 709)` but does not tone map, so PQ values are shown as if they were sRGB: grey blacks and whites, low contrast, weak colours.

Measured on 2026-09-29 (KDE Plasma 6.7 Wayland, Sunshine 2026.914, `hevc_vulkan` on an AMD RX 9060 XT, Moonlight Qt 6.1 with VAAPI decoding, a 3440×1440 HDR monitor showing a test pattern, client at 1920×1080):

| Patch (sRGB) | HDR on (before) | HDR off / fixed |
|---|---|---|
| white 255 | 140 | 255 |
| grey 235 / 128 / 16 | 135 / 102 / 27 | 234 / 123 / 17 |
| red (255, 0, 0) | (129, 74, 48) | (255, 0, 0) |
| mean error of 13 patches | **52 / 255** | **1.6 / 255** |

Colour space, range and decoder were not the cause: both full- and limited-range streams decoded within 2/255 once the capture was SDR.

**Fix.** Big Remote Play adds one Sunshine `global_prep_cmd` (`host/stream_display.py`). Sunshine runs it before capture starts and after the session ends, with the client's request in `SUNSHINE_CLIENT_HDR`, `SUNSHINE_CLIENT_WIDTH`, `SUNSHINE_CLIENT_HEIGHT` and `SUNSHINE_CLIENT_FPS`:

- the shared screen in HDR → `kscreen-doctor output.<screen>.hdr.disable output.<screen>.wcg.disable` for the session, then enabled again, **whatever the first device asked for**. Sunshine runs the command once per app launch and every device that joins later shares the capture. From an SDR screen Sunshine serves SDR devices correctly and an HDR request as `SDR (Rec. 2020)` 10-bit; from an HDR screen only HDR devices look right. Seen for real on 2026-09-29: an HDR TV launched the session, then a phone, an Xbox and a PC joined in SDR and got washed-out colours; switching the screen to SDR during the session made Sunshine re-create all four encoders as SDR (the HDR one as `SDR (Rec. 2020)`);
- with the screen set to **Automatic**, every HDR screen is switched, because Sunshine chooses the one it captures.

The previous state is kept in `$XDG_RUNTIME_DIR/big-remote-play/stream-display.json` (0600). If Sunshine is killed and never runs the undo, **Stop sharing**, the next session or the next start of Big Remote Play puts it back. Entries the person added to `global_prep_cmd` are kept; an unreadable value is left untouched. Only KDE Plasma (`kscreen-doctor`) is handled; on other desktops the screen is not changed.

Verified end to end on 2026-09-29: during a real session the screen was SDR, afterwards HDR and wide colour gamut were back and the output configuration was identical to before; the client's picture matched a screenshot of the real screen in brightness (42 vs 44) and contrast (42 vs 41).

## Blurry small text: scaling, not compression

Sunshine scales the captured screen to the resolution the client requested. A 3440×1440 (21:9) screen sent to a 1920×1080 client becomes a 1920×804 letterboxed picture: every detail is reduced to 56 %.

| Same pattern, HDR fixed | 1-px line contrast (ideal 127) | text edge energy (pattern 21.2) |
|---|---|---|
| 1920×1080, HEVC 15 Mbps | 74 | 15.7 |
| 1920×1080, H.264 15 Mbps | 74 | 16.8 |
| 3440×1440 (native), HEVC 31 Mbps | **127** | **21.7** |

At the native resolution single-pixel lines and 8-px text survive intact at ordinary bitrates, so the loss was the scaling. Two ways to avoid it:

- on the connecting computer, request the sharing screen's resolution when its screen can show it;
- on the sharing computer, **Screen resolution while sharing**: for the session, the chosen screen uses a fixed mode (1920×1080, 2560×1440, 1280×720) or the first device's size, when the screen has that exact mode, and returns afterwards. It changes that screen for anyone sitting at it, which is why the default keeps it.

Measured on 2026-09-29 with H.264, the bitrates TVs and car screens usually request, and the real screen as the reference (SSIM/PSNR against a Lanczos downscale of a screenshot of that screen):

| Client | Shared screen | Text SSIM | Text PSNR |
|---|---|---|---|
| 1920×1080 TV, 10 Mbps | 3440×1440 | 0.893 | 21.0 dB |
| 1920×1080 TV, 10 Mbps | **1920×1080** | **0.990** | **33.9 dB** |
| 1280×720 car screen, 8 Mbps | 3440×1440 | 0.825 | 18.6 dB |
| 1280×720 car screen, 8 Mbps | **1920×1080** | **0.940** | **26.5 dB** |

Host capture + encode time stayed 1.8–2.5 ms. With a static picture, doubling the bitrate from 10 to 20 Mbps changed text SSIM by less than 0.005; the scaling, not the bitrate, decided sharpness. The aliasing (moiré on 1-px lines, jagged text) is Sunshine's scaler reducing 3440 px to 1920 px without an anti-aliasing filter; Sunshine's VAAPI encoder scaled no better than its Vulkan encoder (text SSIM 0.914 vs 0.913 at 10 Mbps H.264).

The client's requested size is known only for the device that launched the session: Sunshine runs `global_prep_cmd` when the app is launched and the undo when it closes, not for every device that joins later. **Same as the first device that connects** therefore follows that first device, and HDR screens are always shared in SDR; a fixed size serves every device.

## Codecs, 4:4:4 and latency on the test machine

Moonlight's own statistics at the end of each 14-second session (same machine, so network time is local):

| Stream | Host capture + encode (avg) | Decode (avg) |
|---|---|---|
| 1080p60 H.264 15 Mbps | 2.4 ms | 0.65 ms |
| 1080p60 HEVC 15 Mbps | 2.9 ms | 0.87 ms |
| 3440×1440@60 HEVC 31 Mbps | 3.2 ms | 1.1 ms |
| 1080p60 AV1 15 Mbps | 3.4 ms | 3.3 ms |
| 1080p60 HEVC, software decoding | 1.9 ms | 4.2 ms |

No frames were dropped by the network. Sunshine chose its Vulkan encoder automatically; Big Remote Play does not force NVENC, VAAPI or Vulkan. A request for **4:4:4** fell back to 4:2:0: neither Sunshine's Vulkan nor VAAPI encoder offers 4:4:4 on this AMD GPU (NVENC does on supported NVIDIA cards). 4:2:0 at native resolution already kept black-on-white 8-px text sharp; coloured text shows some colour fringing.

## Not done on purpose

- No saturation, contrast or sharpening filter: the picture matches the source once the capture is right.
- No second encoder, no transcoding and no bitrate adaptation of its own: Moonlight and Sunshine own the stream.
- No HDR tone mapping of its own: an SDR client gets an SDR capture instead.

## Physical devices (2026-09-29)

A phone, a TV and a car multimedia unit (2.4 GHz Wi-Fi only) streamed at the same time from the 3440×1440 HDR screen after the colour fix. Colours were correct on all three; the phone (its own 20:9 resolution, HEVC, 26 Mbps) looked very good. The TV and the car unit requested the Moonlight default of about 10 Mbps (7.3 Mbps of video after error correction), H.264 on the car unit, and showed jagged, hard-to-read text: the scaling measured above. The car unit's link had 12–25 ms of ping variation (Wi-Fi), which Moonlight shows as stutter.

## Still to verify on other hardware

NVIDIA (NVENC, 4:4:4), Intel (VAAPI/QSV), X11 sessions, GNOME (no `kscreen-doctor`), a real two-computer session over Wi-Fi and over the internet, fast motion and games, and resolution matching on a screen someone is using.
