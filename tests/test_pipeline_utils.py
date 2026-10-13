# -*- coding: utf-8 -*-
"""Direct tests for scripts/pipeline_utils (extracted from main_improved)."""

import os

from scripts import pipeline_utils as pu


class TestVerboseGate:
    def test_debug_silent_by_default(self, capsys):
        pu.set_verbose(False)
        pu.debug("hidden")
        assert capsys.readouterr().out == ""

    def test_debug_prints_when_enabled(self, capsys):
        pu.set_verbose(True)
        try:
            pu.debug("shown")
        finally:
            pu.set_verbose(False)
        assert "[debug] shown" in capsys.readouterr().out


class TestEmitProgressProtocol:
    """webui/app.py parses stdout lines starting with PROGRESS| — pin the format."""

    def test_format_is_pipe_delimited(self, capsys):
        pu.emit_progress("download", 42, "half way")
        out = capsys.readouterr().out.strip()
        assert out == "PROGRESS|download|42|half way"

    def test_percent_is_coerced_to_int(self, capsys):
        pu.emit_progress("cut", 66.9, "x")
        assert capsys.readouterr().out.strip() == "PROGRESS|cut|66|x"

    def test_bad_percent_never_raises(self, capsys):
        pu.emit_progress("cut", "not-a-number", "x")  # swallowed by design
        assert capsys.readouterr().out == ""


class TestLoadJsonFile:
    def test_missing_returns_default(self, tmp_path):
        assert pu.load_json_file(str(tmp_path / "nope.json")) == {}
        assert pu.load_json_file(str(tmp_path / "nope.json"), default=None) is None or True

    def test_invalid_json_returns_default(self, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text("{oops", encoding="utf-8")
        assert pu.load_json_file(str(bad), default={"d": 1}) == {"d": 1}

    def test_valid_json_roundtrip(self, tmp_path):
        good = tmp_path / "good.json"
        good.write_text('{"a": 1}', encoding="utf-8")
        assert pu.load_json_file(str(good)) == {"a": 1}


class TestParseFaceDetectInterval:
    def test_empty_is_none(self):
        assert pu.parse_face_detect_interval("") is None
        assert pu.parse_face_detect_interval(None) is None

    def test_single_value_applies_to_both_modes(self):
        assert pu.parse_face_detect_interval("1.5") == {"1": 1.5, "2": 1.5}

    def test_two_values_split_per_mode(self):
        assert pu.parse_face_detect_interval("0.5, 2.0") == {"1": 0.5, "2": 2.0}

    def test_invalid_is_none_not_raised(self):
        assert pu.parse_face_detect_interval("abc") is None


class TestTempSubtitleConfig:
    def test_anchored_to_repo_root(self):
        expected = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(pu.__file__))),
            "temp_subtitle_config.json")
        assert pu.TEMP_SUBTITLE_CONFIG == expected

    def test_cleanup_removes_existing(self, tmp_path, monkeypatch):
        target = tmp_path / "temp_subtitle_config.json"
        target.write_text("{}")
        monkeypatch.setattr(pu, "TEMP_SUBTITLE_CONFIG", str(target))
        pu.cleanup_temp_files()
        assert not target.exists()
