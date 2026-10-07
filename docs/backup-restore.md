# Backup and restore

This document records the production contract implemented by
`utils/backup_restore.py`. The GTK window chooses files and reports progress;
the utility module owns archive validation, atomic writes and rollback.

## Storage inventory and ownership

| Data | Location | Backup | Restore behavior |
|---|---|---:|---|
| Big Remote Play settings | `$XDG_CONFIG_HOME/big-remote-play/config.json` | Yes | Replaces the backed-up application scope |
| Selected VPN and non-secret network/account metadata | Files below the Big Remote Play configuration directory | Yes | Restored; a new machine may require sign-in again |
| The Sunshine settings Big Remote Play manages (`sunshine.conf` and the other files in its folder) | `big-remote-play/sunshine/` | Yes | Separate `sunshine/` namespace; never duplicated |
| Sunshine's own data: its application list, paired devices and the certificate it serves | `$XDG_CONFIG_HOME/sunshine/` | No | Untouched; after a restore on another computer, pair devices again |
| The pin of Sunshine's certificate on this computer (`sunshine_cert.sha256`) | `big-remote-play/sunshine/` | No | Kept; a pin in an older archive is not applied |
| Moonlight configuration, identity and paired hosts | The native or Flatpak `Moonlight.conf` actually selected by Moonlight support code | Yes | Only that file, in `moonlight/Moonlight.conf` |
| Application logs | `big-remote-play/logs/` | No | Preserved during restore |
| Runtime audio state, PID/log and temporary files | App/Sunshine runtime paths | No | Preserved or regenerated |
| Sunshine password, VPN API tokens and auth keys | Secret Service/keyring | No | Never read by backup; reauthentication may be required |
| General Moonlight directory contents other than `Moonlight.conf` | Moonlight-owned directory | No | Never copied or removed |
| Caches | User cache directory | No | Never restored |

The archive itself may contain private certificates and pairing identity. It is
created mode `0600` and must still be stored as a secret.

## Versioned archive contract

New archives contain `manifest.json`, `application/`, `sunshine/` and, when
present, `moonlight/Moonlight.conf`. The manifest records the format/version,
application version, UTC creation time, components, sizes and SHA-256 hashes.
Files are added once, in stable namespace order. Links, devices, sockets and
other special files are never followed or archived.

Pre-keyring plaintext is handled defensively too: the obsolete ZeroTier token
file is excluded, legacy auth/API fields are removed from the archived JSON
copy, and obsolete Sunshine password/credential lines are removed from the
archived `sunshine.conf`. Malformed legacy files that cannot be inspected are
omitted. The live source file is never modified by creating a backup.

Creation uses a random owner-only file beside the chosen destination, flushes
it, validates the completed archive with the restore parser, atomically replaces
the destination and flushes the parent directory. A failure leaves an existing
destination untouched.

## Restore validation and transaction

Before changing settings, restore verifies that the input is a regular gzip tar
within the compressed limit, bounds entry count, individual size and total
uncompressed size, and rejects:

- absolute, non-normalized, traversal and backslash paths;
- duplicate names, unknown components and overlapping namespaces;
- symbolic/hard links, devices and other special members;
- missing/malformed manifests, unsupported versions, unexpected files and hash
  or size mismatches;
- symbolic-link or non-directory destinations.

Every affected current file is read before the first mutation. The restore then
atomically replaces files and removes obsolete files within each component in
the archive. If any operation fails, all changes already applied are rolled back
from the in-memory snapshot. Runtime/log exclusions and components absent from
the archive remain untouched. Successful restore closes the application so no
stale in-memory setting can overwrite the restored files.

Legacy archives created by earlier Big Remote Play versions remain supported.
Their old top-level directories are mapped into the new namespaces, duplicate
Sunshine files are accepted only when their bytes agree, and transient legacy
content is ignored. Ambiguous namespaces, conflicting duplicates and guessed
Moonlight locations are rejected.

## Defaults and Clear Everything

**Restore Defaults** replaces `config.json` with the complete canonical default
object, removing unknown/obsolete keys. It also clears the selected VPN choice,
removes the Big Remote Play-managed Sunshine preferences and removes only the
Moonlight streaming keys managed by this app. Moonlight pairing and identity
are preserved.

**Clear Everything** has two destructive confirmations. It removes Big Remote
Play configuration, caches, histories and its Secret Service entries. It does
not delete Moonlight's directory: only managed streaming preferences are reset,
so an external application's identity and paired hosts are not destroyed.

## Portability

Hardware-specific host/client choices can be restored to a different computer,
but their existing consumers must validate displays, encoders and devices at
use time and fall back when unavailable. Restore does not invent replacements or
silently rewrite the backup. Secret Service values are intentionally not
portable, so restored credential metadata may lead to a new sign-in prompt.
