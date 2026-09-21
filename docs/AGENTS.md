# Documentation instructions

These rules apply to files under `docs/`. Repository-wide safety, testing and release rules remain in the root `AGENTS.md`.

## Audience and structure

- Begin with the reader's task, required context and expected result.
- Use interface labels exactly as shown in the application.
- Explain the normal path before alternatives, edge cases or implementation details.
- Define networking, streaming and packaging terms when a non-technical reader may encounter them.
- Keep paragraphs and procedures scannable; use tables only when they improve comparison.
- State limitations directly. Do not describe configured bitrate, FPS, ping or connection state as measured performance.

## Sources of truth

- `user-guide.md` owns normal user workflows.
- `troubleshooting.md` owns diagnosis and recovery.
- `architecture.md` owns components, data and responsibility boundaries.
- `host-network-policy.md` owns settings precedence and network/audio safety behavior.
- `translations.md` owns gettext maintenance.
- `release-testing.md` owns release acceptance.
- `maintainer-guide.md` owns triage and repository maintenance.

Update the owning document and link to it. Do not maintain long, nearly identical copies in several files. Keep the English and Brazilian Portuguese user guides behaviorally aligned when normal workflows change.

## Accuracy and evidence

- Read the implementation and tests before documenting behavior.
- Prefer official upstream documentation for Sunshine, Moonlight, GTK/libadwaita and VPN contracts.
- Distinguish project behavior, upstream behavior, expected behavior and target-machine observations.
- Use dates or component versions for unstable external facts.
- Never claim a test, platform or accessibility state was verified without evidence.

## Security and privacy

- Never include real credentials, certificates, private addresses, account names, auth keys or unredacted backups.
- Screenshots and examples must use obvious simulated data.
- Warn before steps that expose ports, change firewall/audio/network state, replace files or request privileges.
- Direct private vulnerabilities to the repository security policy rather than a public issue.

## Links, screenshots and release hygiene

- Use relative links for repository files and verify them with `make release-check`.
- Add screenshots only after labels and translations are stable; include meaningful alt text.
- Avoid screenshots for information that is clearer and more maintainable as text.
- Do not commit generated reports, validation logs, contact sheets or temporary review artifacts.
