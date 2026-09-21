from __future__ import annotations

import importlib.util
from pathlib import Path


MODULE = Path(__file__).resolve().parents[1] / "tools" / "i18n" / "validate_catalogs.py"
spec = importlib.util.spec_from_file_location("validate_catalogs", MODULE)
assert spec and spec.loader
validator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validator)


def test_shell_variable_is_counted_once() -> None:
    assert validator._signature("${script_name}") == {"${script_name}": 1}


def test_command_metavariable_may_be_translated() -> None:
    source = "Usage: ${script_name} <IP address>"
    translated = "Utilização: ${script_name} <endereço IP>"
    assert validator._signature(source) == validator._signature(translated)


def test_supported_inline_markup_must_be_preserved() -> None:
    assert validator._signature("Use <b>{name}</b>") == {
        "{name}": 1,
        "<b>": 1,
        "</b>": 1,
    }


def test_shell_yes_no_prompt_marker_is_a_protocol_token() -> None:
    assert validator._signature("Continue? (y/N): ") == {"(y/N)": 1}
    assert validator._signature("Fortfahren? (j/N): ") == {}
