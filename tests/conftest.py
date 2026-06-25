"""Shared fixtures. Keep the filesystem hermetic: every test that touches config
or logs runs against a temporary HOME so nothing writes to the real user dir."""

import pytest


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    """Point HOME at a temp dir so Config/Logger write under tmp_path."""
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path
