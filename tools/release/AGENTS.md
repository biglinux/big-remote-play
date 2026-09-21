# Release-tool guidance

These rules supplement the repository-root `AGENTS.md`.

- `tools/release/build_version.py` is the single build-date version implementation.
- Keep checkout placeholders at `0.0.0`; never write a date into source as a manual release step.
- `BRP_BUILD_VERSION` is an explicit trusted-pipeline override. Otherwise use `SOURCE_DATE_EPOCH`, then current UTC date.
- Build stamping must occur in a temporary/copy context and restore the checkout on success or failure.
- Wheel and sdist names may use PEP 440 normalization; native package metadata keeps `YY.MM.DD`.
- Reproducible builds with the same epoch must be byte-identical.
- The sdist is a contributor-facing source release: include durable docs and agent/community guidance, but exclude caches, reports, local paths and compatibility backups.
- Add tests for every new stamp target, archive filter or version precedence rule.
- Never turn a missing gate into a pass. Emit machine-readable status and preserve the failing command/output.
