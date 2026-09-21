"""Shared fixtures. Keep the filesystem hermetic: every test that touches config
or logs runs against a temporary HOME so nothing writes to the real user dir."""

import os

# Assertions compare the untranslated source strings, so no catalog may be
# selected. Set before big_remote_play.utils.i18n binds the domain at import;
# otherwise a developer running under pt_BR gets the translated text.
# LANGUAGE alone: it outranks LC_MESSAGES in gettext and leaves the process
# encoding as it is (LC_ALL=C would make read_text() decode sources as ASCII).
os.environ["LANGUAGE"] = "C"

# xvfb-run exports DISPLAY but leaves the caller's WAYLAND_DISPLAY in place, and
# GTK4 prefers Wayland when both exist: the UI tests then present their windows
# on the developer's own screen instead of the headless X server, flashing dozens
# of windows over whatever they were doing. Pin the run to the display it was
# given, before any gi import creates a GdkDisplay.
if os.environ.get("DISPLAY"):
    os.environ.pop("WAYLAND_DISPLAY", None)
    os.environ.setdefault("GDK_BACKEND", "x11")

import pytest  # noqa: E402


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    """Point HOME at a temp dir so Config/Logger write under tmp_path.

    XDG_CONFIG_HOME is cleared so the config dir always resolves under the fake
    HOME even when the host developer has it set."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    return tmp_path
