<p align="center">
  <img src="usr/share/big-remote-play/icons/big-remote-play.svg" alt="Big Remote Play" width="128" height="128">
</p>

<h1 align="center">🎮 Big Remote Play</h1>

<p align="center">
  <b>Free & Open Source Remote Cooperative Gaming System — Multi-Platform</b>
</p>

<p align="center">
  <a href="#-features">Features</a> •
  <a href="#-the-story-behind-the-project">Our Story</a> •
  <a href="#-use-cases">Use Cases</a> •
  <a href="#-installation">Installation</a> •
  <a href="#-how-it-works">How It Works</a> •
  <a href="#-architecture">Architecture</a> •
  <a href="#-contributing">Contributing</a> •
  <a href="#-license">License</a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/License-GPLv3-blue.svg" alt="License: GPL v3">
  <img src="https://img.shields.io/badge/Platform-Linux-green.svg" alt="Platform: Linux">
  <img src="https://img.shields.io/badge/GTK-4.0-blue.svg" alt="GTK 4.0">
  <img src="https://img.shields.io/badge/Libadwaita-1.0-purple.svg" alt="Libadwaita">
  <img src="https://img.shields.io/badge/Python-3.x-yellow.svg" alt="Python 3">
  <img src="https://img.shields.io/badge/Version-2.0.0-brightgreen.svg" alt="Version 2.0.0">
</p>

---

## 📖 The Story Behind the Project

Big Remote Play was born from a **real story** of friendship, determination, and the passion for Free Software.

**Alessandro e Silva Xavier** (known as **Alessandro**) and **Alexasandro Pacheco Feliciano** (known as **Pacheco**) wanted to play games together on [BigLinux](https://www.biglinux.com.br) using a feature that only existed on proprietary platforms like **Steam Remote Play** and **GeForce NOW**. The problem? These systems are **proprietary**, locked to their own ecosystems. If a game wasn't available on their platform, it was nearly impossible to play remotely with friends.

Refusing to accept this limitation, Alessandro and Pacheco embarked on a journey of countless attempts and extensive research. After trying many different approaches, they finally found a working solution by **combining multiple free software programs** — including [Sunshine](https://github.com/LizardByte/Sunshine), [Moonlight](https://moonlight-stream.org/), scripts, and VPN tools. They had achieved what the proprietary platforms kept locked behind their walls, and the best part: it was **Free Software** and **multi-platform**!

Excited by their success, they started sharing their achievement during their **live streams**, which generated tremendous enthusiasm from the community. However, there was a catch — the setup was complicated. It required configuring multiple separate solutions: Sunshine, Moonlight, custom scripts, VPN connections... it was a lot for anyone to handle.

That's when a friend decided to step in and help develop a unified application to simplify the entire process. And so, **Big Remote Play** was born! 🎉

An all-in-one application that integrates everything you need for remote cooperative gaming — no proprietary platforms, no restrictions, no limits on which games you can play.

---

## ✨ Features

### 🖥️ Host Server (Share Your Games)
- **One-click server** — Start/stop the Sunshine streaming server directly from the UI
- **Automatic game detection** — Detects games from **Steam**, **Lutris**, and **Heroic Launcher** automatically
- **Steam Big Picture mode** — Launch Steam games directly in Big Picture mode with automatic session management
- **Streaming settings** — Configure resolution, FPS, bitrate, codec (H.264/H.265/AV1), monitor selection, and GPU selection
- **Audio management** — Hybrid audio streaming with simultaneous host + remote playback using PulseAudio
- **PIN-based pairing** — Secure connection flow with PIN code authentication
- **Performance monitor** — Real-time performance metrics dashboard
- **Firewall configuration** — Automatic firewall setup for required ports
- **Secure credentials** — Masked credential fields with copy-to-clipboard support

### 📱 Guest Client (Connect to a Host)
- **Auto-discovery** — Automatically finds Sunshine hosts on the network using Avahi/mDNS
- **Manual connection** — Connect by IP address with full IPv4 and IPv6 support
- **PIN connection** — Quick connect using a short PIN code
- **Adaptive streaming** — Automatic resolution and bitrate detection based on your device
- **Moonlight integration** — Seamless connection through Moonlight-QT client

### 🌐 Private Network (Play Over the Internet)
- **Built-in VPN setup** — Create private networks using Headscale, Tailscale, or ZeroTier
- **Step-by-step wizard** — Guided setup with progress indicators
- **Domain integration** — Support for [DigitalPlat Domain](https://digitalplat.org/) for easy domain setup
- **Connection history** — Save, manage, and reconnect to previous networks
- **Share credentials** — Export and share connection details with friends

### 🌍 Internationalization
- **29 languages supported** including: English, Portuguese (BR), Spanish, German, French, Italian, Japanese, Korean, Chinese, Russian, and many more
- Automatic translation via gettext

### 🎨 Modern UI
- **GTK 4 + Libadwaita** — Modern, native Linux desktop experience
- **Dark/Light theme support** — Follows system preference or manual selection
- **Responsive sidebar navigation** — Clean, organized interface
- **Service status indicators** — Real-time status for all required services

---

## 🎯 Use Cases

Big Remote Play enables a variety of exciting scenarios:

| Scenario | Description |
|----------|-------------|
| 🎮 **Couch Co-op Online** | Play with a friend (or more!) with only **one copy of the game** running on the host |
| 📱 **Play on Mobile** | Stream your PC games to your **Android or iOS phone/tablet** |
| 💻 **Remote PC Gaming** | Play your games from **another computer** anywhere in the world |
| 📺 **Play on TV** | Stream games to your **TV** using any Moonlight-compatible device |
| 🏠 **LAN Party** | Multiple friends connect to your PC over the **local network** |
| 🌍 **Internet Gaming** | Play with friends over the **internet** using the built-in VPN |
| 🎲 **Any Game, Any Platform** | Works with **Steam, Lutris, Heroic, GOG, Epic** — no restrictions! |

---

## 📦 Installation

### Arch Linux / BigLinux (Recommended)

The package is available for Arch-based distributions:

```bash
# Clone the repository
git clone https://github.com/biglinux/big-remote-play.git
cd big-remote-play/pkgbuild

# Build and install
makepkg -si
```

### Dependencies

| Dependency | Purpose |
|-----------|---------|
| `python` | Application runtime |
| `gtk4` | GUI toolkit |
| `libadwaita` | GNOME/Adwaita widgets |
| `python-gobject` | Python GTK bindings |
| `python-cairo` | cairo graphics library bindings |
| `avahi` | Network service discovery (mDNS) |
| `curl` | HTTP requests (Sunshine API) |
| `iproute2` | Network utilities |
| `sunshine-bin` | Game stream host server |
| `moonlight-qt` | Game stream client |

#### Optional Dependencies

| Dependency | Purpose |
|-----------|---------|
| `docker` | Private network (Headscale server) |
| `tailscale` | Private network VPN client |
| `zerotier-one` | Private network VPN client |

### Manual Installation (Development)

```bash
# Clone the repository
git clone https://github.com/biglinux/big-remote-play.git
cd big-remote-play

# Run directly (development mode)
python3 usr/share/big-remote-play/main.py
```

---

## 🔧 How It Works

Big Remote Play acts as a **unified interface** that orchestrates multiple open-source technologies:

```
┌─────────────────────────────────────────────────────┐
│            Big Remote Play (GTK4)           │
├──────────────┬──────────────┬────────────────────────┤
│  Host View   │  Guest View  │  Private Network View  │
├──────────────┴──────────────┴────────────────────────┤
│                   Core Services                       │
├──────────────┬──────────────┬────────────────────────┤
│  ☀️ Sunshine  │  🌙 Moonlight │  🔒 Headscale/Tailscale/ZeroTier│
│  (Host)      │  (Client)    │  (VPN)                        │
└──────────────┴──────────────┴────────────────────────┘
```

### Quick Start Guide

#### As a Host (Sharing games):
1. Open **Big Remote Play**
2. Navigate to **Host Server**
3. Configure your streaming settings (resolution, FPS, codec)
4. Select which games to share
5. Click **Start Server**
6. Share your PIN or IP with your friends!

#### As a Guest (Connecting to play):
1. Open **Big Remote Play**
2. Navigate to **Connect to Server**
3. Either:
   - Select a **discovered host** from the list
   - Enter the host's **IP address** manually
   - Use a **PIN code** for quick connection
4. Pair with the host and start playing!

#### Over the Internet:
1. Set up a **Private Network** using the built-in wizard
2. Share the connection credentials with your friend
3. Both connect to the private network
4. Connect as Host/Guest as usual — the VPN handles the rest!

---

## 🏗️ Architecture

### Project Structure

```
big-remote-play/
├── 📁 src/
│   └── 📁 big_remote_play/                    # Python package (built as a wheel)
│       ├── app.py                             # Application entry point (Adw.Application)
│       ├── __main__.py                        # `python -m big_remote_play`
│       ├── paths.py                           # Data/locale path resolution
│       ├── 📁 ui/                             # User Interface (GTK4/libadwaita)
│       │   ├── main_window.py                 # Main window with sidebar nav
│       │   ├── host_view.py                   # Host server configuration
│       │   ├── guest_view.py                  # Guest client connection
│       │   ├── private_network_view.py        # VPN/Private network setup
│       │   ├── performance_monitor.py         # Real-time performance dashboard
│       │   ├── sunshine_preferences.py        # Sunshine advanced settings
│       │   ├── moonlight_preferences.py       # Moonlight advanced settings
│       │   ├── preferences.py                 # General app preferences
│       │   └── installer_window.py            # Dependency installer
│       ├── 📁 host/                           # Host module
│       │   └── sunshine_manager.py            # Sunshine server management
│       ├── 📁 guest/                          # Guest module
│       │   └── moonlight_client.py            # Moonlight client wrapper
│       └── 📁 utils/                          # Utility modules
│           ├── audio.py                       # PulseAudio management
│           ├── config.py                      # Configuration management (atomic)
│           ├── game_detector.py               # Game detection (Steam/Lutris/Heroic)
│           ├── i18n.py                        # Internationalization
│           ├── icons.py                       # Icon utilities
│           ├── logger.py                      # Logging system
│           ├── network.py                     # Network discovery & tools
│           ├── script_protocol.py             # Network-script marker parser
│           ├── secure_io.py                   # Owner-only secret writes
│           └── system_check.py                # System dependency checker
├── 📁 tests/                                  # pytest suite
├── 📁 usr/
│   ├── 📁 bin/
│   │   └── big-remote-play                    # Resilient launcher (survives Python upgrades)
│   └── 📁 share/
│       ├── 📁 applications/
│       │   └── big-remote-play.desktop        # Desktop entry
│       ├── 📁 big-remote-play/                # Data assets (shipped under /usr/share, not code)
│       │   ├── 📁 ui/
│       │   │   └── style.css                  # Custom GTK4 styles
│       │   ├── 📁 scripts/                    # Shell scripts (gettext-localized)
│       │   │   ├── configure_firewall.sh
│       │   │   ├── create-network_headscale.sh
│       │   │   ├── create-network_tailscale.sh
│       │   │   ├── create-network_zerotier.sh
│       │   │   └── drop_guest.sh
│       │   ├── 📁 icons/                      # App SVG icons
│       │   └── 📁 img/                        # App images
│       └── 📁 locale/                         # Compiled translations (.mo)
├── 📁 locale/                                 # Translation source files (.po/.pot)
├── pyproject.toml                             # Wheel build + ruff/pytest/pyright config
├── flake.nix                                  # Nix build
├── default.nix                                # Nix package definition
├── 📁 pkgbuild/                               # Arch Linux packaging
│   ├── PKGBUILD
│   └── pkgbuild.install
├── 📁 .github/
│   └── 📁 workflows/
│       └── translate-and-build-package.yml    # CI/CD pipeline
├── COPYING                                    # GPLv3 License
└── README.md                                  # This file
```

### Technology Stack

| Component | Technology | Description |
|-----------|-----------|-------------|
| **GUI Framework** | GTK 4 + Libadwaita | Modern GNOME desktop UI |
| **Language** | Python 3 | Application logic |
| **Streaming Host** | Sunshine | High-performance game stream server |
| **Streaming Client** | Moonlight-QT | Open-source game stream client |
| **Network Discovery** | Avahi (mDNS) | Automatic host discovery on LAN |
| **Audio** | PulseAudio | Hybrid audio routing (host + remote) |
| **VPN** | Headscale / Tailscale / ZeroTier | Private network for internet play |
| **Packaging** | PKGBUILD (Arch) | Distribution packaging |
| **CI/CD** | GitHub Actions | Automated translation & packaging |

### Key Modules

| Module | Responsibility |
|--------|---------------|
| `SunshineHost` | Start/stop/configure Sunshine server, manage apps, send PINs, API communication |
| `MoonlightClient` | Connect/disconnect Moonlight, pairing, host probing, app listing |
| `GameDetector` | Scan Steam, Lutris, and Heroic Launcher for installed games |
| `AudioManager` | PulseAudio sink management, hybrid audio (host + guest), streaming audio routing |
| `NetworkDiscovery` | Avahi-based host discovery, PIN resolution, IPv4/IPv6 support |
| `PrivateNetworkView` | VPN setup wizard (Headscale, Tailscale, ZeroTier), connection management, credential sharing |

---

## 🤝 Contributing

We welcome contributions from the community! Here's how you can help:

### 🐛 Reporting Bugs

Found a bug? Please [open an issue](https://github.com/biglinux/big-remote-play/issues) with:
- Steps to reproduce
- Expected vs actual behavior
- System info (distro, GTK version, Sunshine/Moonlight version)

### 🌐 Translations

Help us reach more people! Translation files are in the `locale/` directory. We currently support **29 languages** but always welcome improvements:

1. Fork the repository
2. Edit or create a `.po` file in `locale/`
3. Submit a Pull Request

The translation template is at `locale/big-remote-play.pot`.

### 💻 Code Contributions

1. **Fork** the repository
2. **Create a branch**: `git checkout -b feature/my-feature`
3. **Make your changes**
4. **Test** your changes locally:
   ```bash
   python3 usr/share/big-remote-play/main.py
   ```
5. **Commit**: `git commit -m 'Add my feature'`
6. **Push**: `git push origin feature/my-feature`
7. **Open a Pull Request**

### 💡 Ideas & Suggestions

Have an idea to make the project better? Open an issue tagged as **Enhancement** or start a **Discussion**!

---

## 📺 Media

- 🎬 **Demo Video**: [Watch on YouTube](https://www.youtube.com/watch?v=D2l9o_wXW5M)
- 🌐 **Website**: [biglinux.com.br](https://www.biglinux.com.br)

---

## 👥 Team

| Name | Role | Contact |
|------|------|---------|
| **Rafael Ruscher** | Lead Developer | [rruscher@gmail.com](mailto:rruscher@gmail.com) |
| **Alexasandro Pacheco Feliciano** (Pacheco) | Co-Creator & Tester | [@pachecogameroficial](https://github.com/pachecogameroficial) |
| **Alessandro e Silva Xavier** (Alessandro) | Co-Creator & Tester | [@alessandro741](https://github.com/alessandro741) |

---

## 📄 License

This project is licensed under the **GNU General Public License v3.0** — see the [COPYING](COPYING) file for details.

```
Big Remote Play
Copyright (C) 2026 BigLinux

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.
```

---

## 💙 Acknowledgements

- [Sunshine](https://github.com/LizardByte/Sunshine) — The amazing open-source game streaming host
- [Moonlight](https://moonlight-stream.org/) — The outstanding open-source game streaming client
- [BigLinux](https://www.biglinux.com.br) — The Linux distribution that inspired this project
- [Headscale](https://github.com/juanfont/headscale) — Self-hosted Tailscale control server
- [DigitalPlat](https://digitalplat.org/) — Domain services for the community
- The entire **open-source gaming community** for making this possible

---

<p align="center">
  <b>We hope you enjoy it and help us by collaborating! 🚀</b>
</p>

<p align="center">
  <i>Made with ❤️ by the community, for the community.</i>
</p>

<p align="center">
  <i>Free Software — because gaming should have no walls.</i>
</p>
