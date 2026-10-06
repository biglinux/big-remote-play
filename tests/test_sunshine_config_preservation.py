"""Big Remote Play never loses the person's own Sunshine settings."""

from __future__ import annotations

import stat

from big_remote_play import paths
from big_remote_play.ui.sunshine_preferences import SunshineConfigManager, reset_sunshine_settings, web_ui_origin

OWN = "sunshine_user = player\nport = 48989\nfile_apps = apps.json\nfile_state = state.json\nmin_log_level = debug\nmy_expert_key = 7\n"


def _write(text: str) -> None:
    paths.SUNSHINE_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    paths.SUNSHINE_CONF.write_bytes(text.encode("latin-1") if isinstance(text, str) else text)


def test_an_unreadable_file_is_never_replaced_by_the_keys_one_page_knows():
    paths.SUNSHINE_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    paths.SUNSHINE_CONF.write_bytes(b"sunshine_user = player\nmy_expert_key = \xff\xfe\n")
    before = paths.SUNSHINE_CONF.read_bytes()
    manager = SunshineConfigManager()
    assert manager.load_error
    assert manager.update({"upnp": "enabled"}) is False
    assert manager.set("upnp", "enabled") is False
    assert paths.SUNSHINE_CONF.read_bytes() == before


def test_a_save_keeps_the_other_keys_and_the_previous_file():
    _write(OWN)
    assert SunshineConfigManager().update({"upnp": "enabled"})
    text = paths.SUNSHINE_CONF.read_text()
    assert "my_expert_key = 7" in text and "upnp = enabled" in text
    previous = paths.SUNSHINE_CONF.with_name("sunshine.conf.previous")
    assert previous.read_text() == OWN
    assert stat.S_IMODE(previous.stat().st_mode) == 0o600


def test_restore_defaults_keeps_the_servers_identity_and_pairings():
    _write(OWN)
    assert reset_sunshine_settings()
    kept = SunshineConfigManager().config
    assert kept == {"sunshine_user": "player", "port": "48989", "file_apps": "apps.json", "file_state": "state.json"}
    assert paths.SUNSHINE_CONF.with_name("sunshine.conf.previous").read_text() == OWN


def test_web_interface_access_is_never_widened_without_the_person_asking():
    assert web_ui_origin(False, "pc") == "pc"
    assert web_ui_origin(False, "lan") == "lan"
    assert web_ui_origin(False, "wan") == "lan"  # the switch was turned off
    assert web_ui_origin(False, "") == "lan"
    assert web_ui_origin(True, "pc") == "wan"
