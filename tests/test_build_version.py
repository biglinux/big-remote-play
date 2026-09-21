from __future__ import annotations
import importlib.util
from pathlib import Path
import pytest

_PATH = Path(__file__).resolve().parents[1] / "tools/release/build_version.py"
_SPEC = importlib.util.spec_from_file_location("brp_build_version", _PATH)
assert _SPEC is not None and _SPEC.loader is not None
_MOD = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MOD)


def test_source_date_epoch_controls_build_version() -> None:
    assert _MOD.build_version({"SOURCE_DATE_EPOCH": "1789776000"}) == "26.09.19"


def test_explicit_build_version_override() -> None:
    assert _MOD.build_version({"BRP_BUILD_VERSION": "26.12.31"}) == "26.12.31"


@pytest.mark.parametrize("value", ["today", "26.9.19", "26-09-19", "-1"])
def test_invalid_explicit_version_is_rejected(value: str) -> None:
    with pytest.raises(ValueError):
        _MOD.build_version({"BRP_BUILD_VERSION": value})


def test_empty_override_falls_back_to_reproducible_epoch() -> None:
    assert _MOD.build_version({"BRP_BUILD_VERSION": "", "SOURCE_DATE_EPOCH": "1789776000"}) == "26.09.19"


def test_invalid_source_date_epoch_is_rejected() -> None:
    with pytest.raises(ValueError):
        _MOD.build_version({"SOURCE_DATE_EPOCH": "not-an-integer"})
    with pytest.raises(ValueError):
        _MOD.build_version({"SOURCE_DATE_EPOCH": "-1"})
