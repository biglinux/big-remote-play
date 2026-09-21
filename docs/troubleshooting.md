# Troubleshooting

## No computer found

First start sharing on the game PC. Check that both PCs are reachable on the same home or private network. Search again. Across a VPN, use the game PC's private IP address; broadcast discovery is not universally supported. A search code does not create network connectivity.

## The browser sign-in for the private network did not open, or the PC is not really connected

The sign-in page opens in your own browser and is brought to the front; if a
browser window was never raised, check that a default browser is set. Closing
the page without finishing the sign-in leaves the PC authenticated but
disconnected — the program reports "Sign-in was not completed in the browser"
and Tailscale's own status reads `Stopped`, not `Running`. Choose Connect again
and finish the sign-in. The password prompt appears at most once per PC, to
start the `tailscaled` service and to authorize your user to control it;
connecting afterwards needs no password.

## Pairing does not finish

The connecting PC displays Moonlight's four-digit code. Enter it on the game PC under Share, not into the search-code field. Keep the connecting window open while approving. If approval fails, check the administrative credentials or complete pairing through Sunshine's official web interface. Cancellation must end the pending attempt before retrying.

## Incorrect resolution, frame rate or sound

Set resolution and frame rate on the **connecting** PC under Image. Share's bitrate limit is a ceiling, not the requested video resolution. Its automatic mode leaves the request to the client. Check the selected display and audio output on the game PC.

A configured value in the monitor is not a measured frame rate. Network ping is not end-to-end gaming latency. Test wired networking and the actual encoder/decoder before attributing low FPS to the interface.

## Unable to save credentials

Unlock or start the desktop's Secret Service keyring. The app does not intentionally fall back to writing these passwords into ordinary settings. Do not post passwords, tokens or the unredacted keyring error context publicly.

## Wrong colors or missing symbols

Install the native hicolor icon theme and keep the supplied relative symbolic links intact. Do not replace its directory index with a stripped custom index. Test the default Adwaita theme before diagnosing a third-party theme override. Reload the application after replacing resource files.

## Settings are not saved

Check file ownership and permissions. An unreadable or malformed Moonlight/Sunshine file must be repaired or restored deliberately, not overwritten as though empty. Do not launch the app using sudo. If Moonlight is running, its own preferences may be written when it closes; configure between sessions where possible.

## Backup and rollback

Back up before testing a new build. Backups may contain native private keys. Keyring secrets are separate. Restore checks archive paths and sizes and writes to the actual configuration locations, including Flatpak Moonlight, but does not provide a transaction spanning every file. Preserve the archive and check remaining files if restore is interrupted.

If both legacy and current Big Remote Play config directories exist, the migration preserves conflicting old files rather than deleting them. Keep those files until a maintainer or administrator confirms which version is needed.
