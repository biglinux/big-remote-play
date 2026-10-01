"""Visible text must go through gettext; the checker must notice when it does not."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "tools/i18n/check_hardcoded_text.py"
spec = importlib.util.spec_from_file_location("check_hardcoded_text", MODULE)
assert spec and spec.loader
checker = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = checker  # dataclasses resolve their module by name
spec.loader.exec_module(checker)


def found(tmp_path: Path, source: str) -> list[str]:
    path = tmp_path / "view.py"
    path.write_text(source, encoding="utf-8")
    return [finding.text for finding in checker.findings_in(path)]


def test_the_application_has_no_hardcoded_visible_text():
    assert [str(finding) for finding in checker.findings()] == []


@pytest.mark.parametrize(
    "source",
    [
        'Gtk.Button(label="Connect")',
        'row.set_subtitle("Waiting for the other computer")',
        'Adw.Toast(title=f"Connected to {name}")',
        'dialog.add_response("ok", "OK")',
        'Gtk.StringList.new(["Desktop", "Custom"])',
        'button.update_property([Gtk.AccessibleProperty.LABEL], ["Copy address"])',
        'Gtk.Label(label="Ready" if ok else _("Error"))',
        'self.show_error_dialog(_("Error"), f"Script not found: {path}")',
        'model.append(f"No games found on {platform}")',
    ],
)
def test_untranslated_visible_text_is_reported(tmp_path, source):
    assert found(tmp_path, source)


@pytest.mark.parametrize(
    "source",
    [
        'Gtk.Button(label=_("Connect"))',
        'row.set_title(_("Connected to {name}").format(name=name))',
        'row.set_title(ngettext("{count} device", "{count} devices", n).format(count=n))',
        'page.set_title(pgettext("navigation", "Share"))',
        'dialog.add_response("cancel", _("Cancel"))',
        'Adw.ActionRow(title="Sunshine", subtitle="Big Remote Play")',
        'Gtk.Label(label="1920 × 1080 · 60 FPS · 20 Mbps")',
        'Gtk.Label(label="{} ms".format(latency))',
        'Gtk.Label(label="https://login.tailscale.com")',
        'Gtk.StringList.new(["720p", "1080p", "4K"])',
        'logger.info("Sunshine started")',
    ],
)
def test_translated_and_technical_text_is_accepted(tmp_path, source):
    assert found(tmp_path, source) == []


def test_the_checked_in_catalogs_match_the_sources(tmp_path):
    """New or changed messages must reach locale/ before they ship."""
    if missing := [tool for tool in ("xgettext", "msgcat", "msgmerge", "msgattrib", "msgfmt") if shutil.which(tool) is None]:
        pytest.skip("GNU gettext tools unavailable: " + ", ".join(missing))
    copy = tmp_path / "project"
    for part in ("src", "locale", "tools", "build-aux", "usr/share/big-remote-play/scripts"):
        shutil.copytree(ROOT / part, copy / part, ignore=shutil.ignore_patterns("__pycache__"))
    for part in ("usr/share/applications", "usr/share/metainfo"):
        shutil.copytree(ROOT / part, copy / part)
    subprocess.run([sys.executable, "tools/i18n/update_catalogs.py", "--pot-only"], cwd=copy, check=True, capture_output=True, text=True)

    def messages(path: Path) -> set[tuple[str | None, str]]:
        sys.path.insert(0, str(ROOT / "tools/i18n"))
        from validate_catalogs import _parse_po

        return {(entry.get("ctx"), entry["id"]) for entry in _parse_po(path) if entry.get("id")}

    extracted, committed = messages(copy / "locale/big-remote-play.pot"), messages(ROOT / "locale/big-remote-play.pot")
    assert extracted - committed == set(), "run tools/i18n/update_catalogs.py and translate the new messages"
    assert committed - extracted == set(), "run tools/i18n/update_catalogs.py to drop messages no longer used"


def test_the_catalog_validator_accepts_the_repository():
    if shutil.which("msgfmt") is None:
        pytest.skip("msgfmt unavailable")
    completed = subprocess.run([sys.executable, "tools/i18n/validate_catalogs.py"], cwd=ROOT, capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr
