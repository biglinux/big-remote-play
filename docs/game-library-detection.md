# Game library: finding installed games

**Share → Preferences → Picture and performance → Game Library** lists the games Moonlight offers on the other device and the games installed on this computer. You choose which ones to add. This page explains where the games are found and how they are added.

## Searching works without sharing

The search reads only this computer's files: it does not need Sunshine, its password or sharing to be on.

| Launcher | Where | Counted as installed when |
|---|---|---|
| Steam (native) | `~/.local/share/Steam` (also through `~/.steam/steam` and `~/.steam/root`) | its `appmanifest_*.acf` says *fully installed* and the game's folder exists |
| Steam (Flatpak) | `~/.var/app/com.valvesoftware.Steam/.local/share/Steam` | the same |
| Steam libraries on other disks | every path in `steamapps/libraryfolders.vdf`, including paths with spaces | the same; a disk that is not mounted is skipped |
| Lutris | `~/.local/share/lutris/pga.db` (native or Flatpak `net.lutris.Lutris`) | `installed = 1` and its folder exists (when it has one) |
| Heroic: Epic Games | `legendaryConfig/legendary/installed.json` | listed there and not DLC |
| Heroic: GOG | `gog_store/installed.json` (titles from `store_cache/gog_library.json`) | listed there; GOG Galaxy redistributables are skipped |
| Heroic: Amazon Games | `nile_config/nile/installed.json` (titles from `store_cache/nile_library.json`) | listed there |
| Heroic: added by hand | `sideload_apps/library.json` | marked installed |

Heroic is read from `~/.config/heroic` and from its Flatpak folder `~/.var/app/com.heroicgameslauncher.hgl/config/heroic`. Catalog files list games you own but did not install; they are used only for titles, never to decide what is installed.

Steam tools (Proton, Steam Linux Runtime, Steamworks redistributables) are recognised by their app id and install folder, not by words in their name, so a game called *Runtime Error* is still listed.

On the maintainer's computer the search found the 8 installed Steam games across two disks (and none of the 7 tools) and the one installed Epic game, out of 1,226 catalog entries.

## How a game is started

Each game starts through its launcher with an id checked here: digits for Steam (`steam://rungameid/<id>`) and Lutris (`lutris:rungameid/<id>`), a short token and store for Heroic (`heroic://launch?appName=<id>&runner=<store>`). The launcher is the one installed: `steam`, or `flatpak run com.valvesoftware.Steam` when only the Flatpak exists. No command is built from a name or path read from disk.

Launchers hand the game to their running window and exit at once, so the Sunshine entry runs the command *detached* (as Sunshine's own Steam example does) and the session does not end when the launcher process ends.

## Adding and removing

- **Add Selected** adds only the checked games. A game already in Moonlight is marked **Already in Moonlight** and cannot be added twice; it is recognised by its launch command even after you rename it in Sunshine.
- **Add Manually…** adds a name and a command; the command must have balanced quotation marks.
- The trash button removes a game from Moonlight only; the game stays installed. **Desktop** cannot be removed.

While sharing, changes go through Sunshine's API, so Moonlight sees them right away. When sharing is off, or Sunshine does not accept the saved password, the library file (`apps.json`, or the file set in `file_apps`) is changed directly: atomically, owner-only, keeping the environment, every other app and every other setting. Sunshine reads it the next time sharing starts. A library file that cannot be read as one is never replaced; the page says so.
