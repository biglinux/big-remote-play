"""One installation at a time, visible from every page that needs one.

Reported on 2026-11-17: Headscale → Install Headscale → Install and continue
failed (PolicyKit authentication failed for `pamac install headscale`); the page
then only said "Another installation is running", had no technical details and
never offered the button again.
"""

from __future__ import annotations

import pytest

pytest.importorskip("gi")

from big_remote_play.ui import dependency_installer as installer  # noqa: E402
from big_remote_play.utils import dependencies  # noqa: E402


def _states(installed: bool):
    return [dependencies.ComponentState("headscale_server", installed, "")]


@pytest.fixture
def fake(monkeypatch):
    seen = {"installed": False}
    monkeypatch.setattr(dependencies, "_AUDIT", lambda ids: [dependencies.ComponentState(i, seen["installed"], "") for i in ids])
    monkeypatch.setattr(installer.Worker, "submit", lambda self, work, done, failed=None, **_k: done(work()))
    monkeypatch.setattr(installer, "_SESSION", installer._InstallSession())
    return seen


def test_a_page_built_during_an_installation_shows_its_details_and_continues_after(fake):
    owner = installer.ComponentChecklist(["headscale_server"])
    assert installer._INSTALLING.acquire(blocking=False)
    try:
        owner._owns_session = True
        installer._SESSION.begin()
        installer._SESSION.set_summary("Packages: headscale\nInstalled with: Pamac")
        installer._SESSION.add_line("Authentication failed")
        # The page is rebuilt while the first one waits.
        other = installer.ComponentChecklist(["headscale_server"])
        assert other.details.get_visible()
        buffer = other.log.get_buffer()
        assert "Authentication failed" in buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False)
        assert not other.button.get_sensitive()
    finally:
        installer._INSTALLING.release()
    installer._SESSION.finish("failed")
    # The button comes back without leaving the page, and the reason is said.
    assert other.button.get_visible() and other.button.get_sensitive()
    assert other.status.get_visible()
    owner.close()
    other.close()


def test_what_another_page_installed_is_seen_here(fake):
    page = installer.ComponentChecklist(["headscale_server"])
    assert not page.ready
    fake["installed"] = True
    installer._SESSION.begin()
    installer._SESSION.finish("ok")
    assert page.ready
    page.close()
