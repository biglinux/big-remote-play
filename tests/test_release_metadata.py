"""Release metadata must not drift between user-visible and build files."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tomllib
import xml.etree.ElementTree as ET
from pathlib import Path

from big_remote_play import __version__

ROOT = Path(__file__).resolve().parents[1]


def test_application_version_is_consistent() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["version"] == __version__

    default_nix = (ROOT / "default.nix").read_text(encoding="utf-8")
    nix_match = re.search(r'^\s*version\s*=\s*"([^"]+)";', default_nix, re.MULTILINE)
    assert nix_match is not None
    assert nix_match.group(1) == __version__

    metainfo = ET.parse(ROOT / "usr/share/metainfo/br.com.biglinux.remoteplay.metainfo.xml").getroot()
    release = metainfo.find("releases/release")
    assert release is not None
    assert release.attrib.get("version") == __version__


def test_about_window_uses_canonical_version() -> None:
    app_source = (ROOT / "src/big_remote_play/app.py").read_text(encoding="utf-8")
    assert "version=__version__" in app_source
    assert 'version="26.07.18"' not in app_source


def test_source_distribution_includes_native_resources_and_license():
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert metadata["project"]["license-files"] == ["COPYING"]
    includes = metadata["tool"]["uv"]["build-backend"]["source-include"]
    for required in ("usr/**", "locale/**", "docs/**", "tests/**", "tools/**"):
        assert required in includes


def test_pkgbuild_derives_version_from_build_date() -> None:
    """The native package dates itself at build time; the checkout stays neutral.

    A literal version committed here is the failure this guards: it would pin a
    rolling package to a stale date and drift from the wheel the same tree
    produces."""
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert metadata["project"]["version"] == "0.0.0"

    pkgbuild = (ROOT / "pkgbuild/PKGBUILD").read_text(encoding="utf-8")
    pkgver = re.search(r"(?m)^pkgver=(.+)$", pkgbuild)
    pkgrel = re.search(r"(?m)^pkgrel=(.+)$", pkgbuild)
    assert pkgver is not None and pkgrel is not None
    assert not re.fullmatch(r"[\d.]+", pkgver.group(1).strip()), "pkgver must not be hand-pinned"
    assert "+%y.%m.%d" in pkgver.group(1)
    assert "+%H%M" in pkgrel.group(1)
    # The wheel takes its version from tools/release/build_version.py; the
    # package must answer to the same two inputs or one build ships two dates.
    assert "BRP_BUILD_VERSION" in pkgbuild
    assert "SOURCE_DATE_EPOCH" in pkgbuild
    # makepkg edits the pkgver= line in place when this function exists, which
    # turns the command substitution into a syntax error on the next load.
    assert re.search(r"(?m)^pkgver\(\)", pkgbuild) is None
    assert "for item in src usr locale tools" in pkgbuild


def _pkgbuild_source(recipe: Path, **env: str) -> dict[str, str]:
    """What makepkg sees after loading ``recipe`` the way it does."""
    script = 'source "$BUILDFILE" || exit 1; printf "%s\\n" "${_use_local_source}" "${_local_root:-}" "${source[*]}"'
    result = subprocess.run(
        ["bash", "-c", script],
        cwd=recipe.parent,
        env={"PATH": os.environ["PATH"], "BUILDFILE": recipe.name, **env},
        capture_output=True,
        text=True,
        check=True,
    )
    local, root, source = result.stdout.splitlines()
    return {"local": local, "root": root, "source": source}


def test_pkgbuild_in_a_checkout_packages_that_checkout() -> None:
    """`makepkg -si` in pkgbuild/ must install the code next to it.

    It used to clone upstream main instead, so a fork or an unmerged branch
    installed an older application stamped with today's version."""
    loaded = _pkgbuild_source(ROOT / "pkgbuild" / "PKGBUILD")

    assert loaded["local"] == "1"
    assert Path(loaded["root"]) == ROOT.resolve()
    assert loaded["source"] == ""


def test_pkgbuild_alone_or_forced_builds_upstream_main(tmp_path: Path) -> None:
    alone = tmp_path / "PKGBUILD"
    shutil.copy(ROOT / "pkgbuild" / "PKGBUILD", alone)

    for loaded in (_pkgbuild_source(alone), _pkgbuild_source(ROOT / "pkgbuild" / "PKGBUILD", BIGREMOTEPLAY_FROM_GIT="1")):
        assert loaded["local"] == "0"
        assert loaded["source"].startswith("git+https://github.com/biglinux/big-remote-play.git#branch=main")
