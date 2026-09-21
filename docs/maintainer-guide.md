# Maintainer guide

## Sources of truth

- [README](../README.md) explains the project and sends each audience to the right guide.
- [User guide](user-guide.md) owns normal product behavior.
- [Architecture](architecture.md) owns component and data boundaries.
- [Host/network policy](host-network-policy.md) owns precedence and safety behavior.
- [Translations](translations.md) owns gettext and regional-language rules.
- [Release acceptance](release-testing.md) owns the stable-release gate.
- [AGENTS.md](../AGENTS.md) owns persistent coding-agent instructions.

Avoid copying long sections between documents. Update the source of truth and link to it.

## Repository instruction policy

`AGENTS.md` is the only checked-in agent-instruction format for this repository. Keep the root file concise and place specialized rules in the nearest nested `AGENTS.md`.

This follows the hierarchical project-guidance model documented by OpenAI Codex and supported by current GitHub Copilot tooling. Gemini CLI can be configured to include `AGENTS.md` as its context filename. Tools that use a different default should be configured outside the repository rather than creating duplicate vendor-specific files that drift.

Official references:

- [OpenAI: custom instructions with AGENTS.md](https://developers.openai.com/docs/agent-configuration/agents-md)
- [GitHub Copilot: customization and AGENTS.md](https://docs.github.com/en/copilot/reference/customization-cheat-sheet)
- [Gemini CLI: configurable context filenames](https://google-gemini.github.io/gemini-cli/docs/cli/gemini-md.html)

Review `AGENTS.md` when a repeated review correction reveals a missing repository rule. Do not turn temporary task details, machine paths or review logs into permanent instructions.

## Issue triage

1. Confirm the report includes application version, distribution, desktop session, role and network layout.
2. Remove or ask the reporter to redact credentials, certificates, tokens, backups and private addresses.
3. Separate Big Remote Play behavior from Sunshine, Moonlight, VPN-client or distribution behavior.
4. Reproduce with the narrowest fixture or command possible.
5. Label first-contribution opportunities only when the expected behavior and acceptance test are clear.
6. Redirect private security reports to the [security policy](https://github.com/biglinux/big-remote-play/security/policy).

Do not close a report as upstream until the failure is independently reproducible without Big Remote Play.

## Pull-request review

A review should answer:

- Does the change solve the reported user problem rather than only alter text or styling?
- Does it preserve existing native configuration, libraries, paired devices and network profiles?
- Are blocking calls outside the GTK main thread and are stale results rejected?
- Are command arguments explicit and validated?
- Are translated strings, accessibility semantics and compact layouts updated?
- Is there behavioral regression coverage?
- Are documentation, changelog and external-contract references updated when needed?
- Were the relevant gates actually run, with failures distinguished from unavailable tools?

Prefer small, reviewable changes. A broad cleanup should not be mixed with a behavior fix unless the cleanup is required to make the fix safe.

## Documentation maintenance

GitHub surfaces CONTRIBUTING, security policy, code of conduct, support resources and issue/PR templates as community-health files. Keep those files actionable and short. Issue forms should request information that maintainers genuinely use.

When interface labels change, update screenshots only after the implementation and translations are stable. Screenshots must use simulated/sanitized data and must never contain real accounts, addresses, credentials or certificates.

## Rolling Python upgrades

Treat a Python minor-version transition as a native-package compatibility check, not as a reason to search stale `site-packages` directories at runtime.

The BigLinux/Manjaro package owns `/usr/lib/big-remote-play` and the isolated Python launcher in `/usr/bin`. During review:

1. confirm the wheel remains tagged as pure Python (`Root-Is-Purelib: true` and no `.so`, `.pyd` or `.dylib` members);
2. confirm the package archive contains no `/usr/lib/python3.X` path;
3. run the installed launcher with a hostile `PYTHONPATH` and verify it still imports the package-owned tree;
4. verify package metadata remains discoverable from the private root;
5. after a distribution Python transition, test the unchanged package before rebuilding it, then test a normal package rebuild, upgrade and removal.

Do not weaken the private-wheel validator to accommodate a compiled extension. A native extension is tied to a Python ABI and requires a deliberate packaging strategy and rebuild policy.

## Release preparation

1. Start from a clean checkout.
2. Run `make release` with a reproducible `SOURCE_DATE_EPOCH`.
3. Extract the source distribution and repeat the required gates inside it.
4. Compare a patch-applied tree and extracted source archive against the intended commit.
5. Complete the target-machine matrix in [release acceptance](release-testing.md).
6. Update the changelog under **Unreleased**, then publish/tag through the normal project process.
7. Preserve the exact build inputs, component versions, hashes and target-machine results.

An automated pass is necessary but not sufficient. Stable means the real package, stream, audio, network, privilege and accessibility paths were accepted by maintainers.

## Repository hygiene

- Keep the root limited to durable project entry points.
- Put long-lived guides in `docs/` and GitHub community files in `.github/`.
- Never commit build outputs, caches, audit bundles, generated reports or local virtual environments.
- Keep symlinks relative and inside the source tree.
- Use one active gettext domain and exact PO/MO inventories.
- Do not rewrite history or force-push contributor branches as part of routine maintenance.
