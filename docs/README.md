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
- [Audio architecture](audio-architecture.md) — what Sunshine records, the bridges Big Remote Play owns, recovery and Steam coexistence.
- [Translations](translations.md) — gettext workflow and regional catalog requirements.
- [Iconography](iconography.md) — native, symbolic and project icon rules.

## Maintainers and coding agents

- [AGENTS.md](../AGENTS.md) — canonical repository instructions for coding agents and concise operational rules for maintainers.
- [Maintainer guide](maintainer-guide.md) — issue triage, documentation ownership, repository hygiene and release preparation.
- [Release acceptance](release-testing.md) — automated gates and mandatory target-machine checks.
- [Private-network test matrix](private-network-test-matrix.md) — what is unit tested, simulated or verified on real services.
- [Private-network change record](private-network-audit.md) — problems found in the private-network integration and how they were resolved.
- [Audio testing](audio-testing.md) — measured Sunshine/PipeWire behavior, automated coverage and pending hardware tests.

## Documentation rules

- Keep user-facing explanations task-oriented and avoid assuming networking expertise.
- Put one source of truth in the most specific document; link to it instead of copying long sections.
- Update documentation and tests in the same change when behavior, labels, defaults or external contracts change.
- Use real interface labels and distinguish configured values from measured values.
- Do not commit session logs, audit reports, generated bundles or screenshots containing private information.
- Keep the repository root small. Durable guides belong here; temporary review evidence belongs outside the source tree.
