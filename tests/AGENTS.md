# Test-specific agent guidance

These rules supplement the repository-root `AGENTS.md`.

- Tests must not alter real VPNs, firewall rules, audio routing, packages, keyrings or user configuration.
- Patch external commands, services, settings paths, clocks and network responses at the boundary.
- Prefer a focused regression test that fails before the fix and passes after it.
- Assert observable behavior and safety properties, not private helper structure.
- Do not assert exact translated prose when exit status, state, ports, argv or filesystem effects express the real contract.
- GTK tests run under Xvfb and a session D-Bus. Cleanly close windows/workers to avoid cross-test state and segmentation faults.
- Test cancellation and stale-result rejection for asynchronous work.
- Use temporary directories and synthetic credentials/addresses. Never copy real logs or backups into fixtures.
- Keep tests deterministic and offline; document any unavoidable system dependency.
