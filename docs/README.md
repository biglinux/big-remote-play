# Documentation

This directory is the maintained documentation set for Big Remote Play. Start with the document that matches the task instead of reading every file.

## Users

- [User guide](user-guide.md) — complete Share, Connect and internet-play walkthrough.
- [Troubleshooting](troubleshooting.md) — discovery, pairing, sound, credentials, icons, settings and recovery.
- [Host and network policy](host-network-policy.md) — ownership and precedence of host, client, audio, VPN and direct-access settings.
- [Router, NAT and firewall](router.md) — CGNAT, IPv6, UPnP and port forwarding, with the private-network alternative first.
- [Cloudflare](cloudflare.md) — what DNS, Tunnel and Access can and cannot do for internet play.
- [VPS and Headscale](vps-headscale.md) — running your own Headscale server.

## Contributors

- [Contributing](../CONTRIBUTING.md) — contribution workflow and pull-request expectations.
- [Development](development.md) — environment, commands, targeted tests and debugging.
- [Architecture](architecture.md) — component boundaries, data ownership and external integrations.
- [Private-network architecture](private-network-architecture.md) — providers, capabilities, state model and upstream contracts.
- [Private-network security](private-network-security.md) — credentials, privileges, legacy-file migration and validation.
- [Video quality](video-quality.md) — the Sunshine → Moonlight pipeline, HDR screens, scaling, codecs and measured results.
- [Game Window](game-window.md) — sharing one game window through a private screen: design, safety rules, limits and measurements.
- [Connection status](connection-status.md) — who is connected now, quality thresholds, the real route and the internet-page measurements.
- [Audio architecture](audio-architecture.md) — what Sunshine records, the bridges Big Remote Play owns, recovery and Steam coexistence.
- [Translations](translations.md) — gettext workflow and regional catalog requirements.
- [UX simplification audit](ux-simplification-audit.md) — what each screen shows first, what moved to secondary places, and why.
- [Guided setup](guided-setup-redesign.md) — the questions, the ready step and the hand-over.
- [Installing what a task needs](dependency-installer.md) — detection, Pamac/PolicyKit installation and the Sunshine detection fix.
- [Pairing requests](pairing-ux.md) — what Sunshine can tell, the request dialog and its safety rules.
- [Connection history](connection-history-fix.md) — the stored sessions of Share, the 7-day chart and the live latency chart.
- [Game Window audio](window-audio-fix.md) — investigation of corrupted sound when sharing one game window.
- [Iconography](iconography.md) — native, symbolic and project icon rules.
- [Gamer theme design](gamer-theme-design.md) — provider ownership, compatibility and visual tokens.
- [Backup and restore](backup-restore.md) — storage inventory, archive format, validation, rollback and data ownership.
- [Service status cards](service-status-cards.md) — stable Share/Connect service cards and their state contract.

## Maintainers and coding agents

- [AGENTS.md](../AGENTS.md) — canonical repository instructions for coding agents and concise operational rules for maintainers.
- [Maintainer guide](maintainer-guide.md) — issue triage, documentation ownership, repository hygiene and release preparation.
- [Release acceptance](release-testing.md) — automated gates and mandatory target-machine checks.
- [Private-network test matrix](private-network-test-matrix.md) — what is unit tested, simulated or verified on real services.
- [Audio testing](audio-testing.md) — measured Sunshine/PipeWire behavior, automated coverage and pending hardware tests.

## Documentation rules

- Keep user-facing explanations task-oriented and avoid assuming networking expertise.
- Put one source of truth in the most specific document; link to it instead of copying long sections.
- Update documentation and tests in the same change when behavior, labels, defaults or external contracts change.
- Use real interface labels and distinguish configured values from measured values.
- Do not commit session logs, audit reports, generated bundles or screenshots containing private information.
- Keep the repository root small. Durable guides belong here; temporary review evidence belongs outside the source tree.
