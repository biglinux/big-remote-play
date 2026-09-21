from __future__ import annotations

import importlib.util
from pathlib import Path


MODULE = Path(__file__).resolve().parents[1] / "tools" / "i18n" / "check_untranslated_human_text.py"
spec = importlib.util.spec_from_file_location("check_untranslated_human_text", MODULE)
assert spec and spec.loader
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


def test_placeholder_only_display_template_is_not_human_text() -> None:
    assert not checker.human("{resolution} · {frame_rate} · {bitrate}")


def test_long_english_sentence_is_human_text() -> None:
    assert checker.human("Accounts and networks on this computer")


def test_sentence_with_placeholder_is_still_human_text() -> None:
    assert checker.human("Could not install ${pkg} on this computer.")


def test_sentence_containing_url_is_still_human_text() -> None:
    assert checker.human("Enter the domain without https:// and test from another network.")


def test_short_two_word_ui_label_is_human_text() -> None:
    assert checker.human("Frame rate")


def test_video_preset_is_not_human_text() -> None:
    assert not checker.human("1080p · 60 FPS · 20 Mbps")


def test_technical_codec_label_is_not_human_text() -> None:
    assert not checker.human("Spatial AQ")


def test_source_language_is_explicitly_excluded_from_untranslated_checks() -> None:
    assert not checker.should_check_catalog(Path("en.po"))
    assert checker.should_check_catalog(Path("uk.po"))
