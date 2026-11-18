# -*- coding: utf-8 -*-
"""Direct tests for scripts/cut_segments.py.

Covers: time parsing (seconds are never reinterpreted as milliseconds),
window math, scene-snap acceptance bounds, encoder detection (subprocess
mocked), ffmpeg command construction (ffmpeg never runs), the
pre-cut validation persistence, and stale-artifact cleanup.
"""

import json
import os
import subprocess
import sys
import types
from unittest import mock

import pytest

from scripts import cut_segments as cs

# ---------------------------------------------------------------------------
# _parse_time_value / _duration_to_seconds
# ---------------------------------------------------------------------------

def test_parse_time_value_numbers_pass_through_unchanged():
    # Regression: a valid start_time of 90 (seconds) must stay 90, not 0.09.
    assert cs._parse_time_value(90) == 90.0
    assert cs._parse_time_value(1500) == 1500.0
    assert cs._parse_time_value(2.5) == 2.5
    assert cs._parse_time_value(90, treat_int_as_ms=True) == 90.0


def test_parse_time_value_strings_and_clock_formats():
    assert cs._parse_time_value("90") == 90.0
    assert cs._parse_time_value("01:02:03") == 3723.0
    assert cs._parse_time_value("02:30") == 150.0
    assert cs._parse_time_value(" 01:00 ") == 60.0
    assert cs._parse_time_value("junk") == 0.0
    assert cs._parse_time_value(None) == 0.0


def test_duration_to_seconds():
    assert cs._duration_to_seconds(30) == 30.0
    assert cs._duration_to_seconds("12.5") == 12.5
    assert cs._duration_to_seconds("junk") == 0.0
    assert cs._duration_to_seconds(None) == 0.0


# ---------------------------------------------------------------------------
# segment_window_seconds / _safe_duration / _accept_scene_snap
# ---------------------------------------------------------------------------

def test_segment_window_seconds_prefers_end_over_duration():
    assert cs.segment_window_seconds(
        {"start_time": 10, "end_time": 25, "duration": 999}) == (10.0, 15.0)
    assert cs.segment_window_seconds(
        {"start": 5, "end": 20}) == (5.0, 15.0)
    assert cs.segment_window_seconds(
        {"start_time": "00:01:00", "end_time": "00:01:30"}) == (60.0, 30.0)


def test_segment_window_seconds_falls_back_to_duration():
    # end <= start: the stale duration field is used instead.
    assert cs.segment_window_seconds(
        {"start_time": 20, "end_time": 10, "duration": 8}) == (20.0, 8.0)
    assert cs.segment_window_seconds({"start_time": 3, "duration": 7}) == (3.0, 7.0)
    assert cs.segment_window_seconds({}) == (0.0, 0.0)
    assert cs.segment_window_seconds(None) == (0.0, 0.0)
    assert cs.segment_window_seconds("nope") == (0.0, 0.0)


def test_safe_duration_clamps():
    assert cs._safe_duration(-5, 10) == (0.0, 10.0)
    assert cs._safe_duration(5, 0) == (5.0, 0.1)
    assert cs._safe_duration(5, -3) == (5.0, 0.1)
    assert cs._safe_duration(1.5, 2.5) == (1.5, 2.5)


@pytest.mark.parametrize("orig,new,expected", [
    (20.0, 20.0, True),
    (20.0, 10.0, True),    # lower bound 0.5x
    (20.0, 30.0, True),    # upper bound 1.5x
    (20.0, 9.9, False),
    (20.0, 30.1, False),
    (0.0, 0.5, False),     # 1.0s floor on both bounds: [1.0, 1.0]
    (0.0, 1.0, True),
    (0.0, 1.5, False),
    (2.0, 0.9, False),     # floor is max(1.0, 0.5*2)=1.0
    (2.0, 1.0, True),
    (-1.0, 5.0, False),
    (10.0, -1.0, False),
    ("junk", 5.0, False),
    (10.0, "junk", False),
])
def test_accept_scene_snap_bounds(orig, new, expected):
    assert cs._accept_scene_snap(orig, new) is expected


# ---------------------------------------------------------------------------
# _has_stored_transcript
# ---------------------------------------------------------------------------

def test_has_stored_transcript_sources():
    assert cs._has_stored_transcript({"transcript_text": "hello"}) is True
    assert cs._has_stored_transcript(
        {"window_analysis": {"text": "hello"}}) is True
    assert cs._has_stored_transcript({"text": "hello"}) is True
    assert cs._has_stored_transcript({"transcript_text": "   "}) is False
    assert cs._has_stored_transcript({}) is False
    assert cs._has_stored_transcript(None) is False
    assert cs._has_stored_transcript("text") is False


# ---------------------------------------------------------------------------
# _detect_best_encoder (subprocess mocked)
# ---------------------------------------------------------------------------

def _stub_toolchain(monkeypatch, working):
    """Fake scripts.ffmpeg_toolchain whose encoder_works answers from a set."""
    module = types.ModuleType("scripts.ffmpeg_toolchain")
    module.encoder_works = lambda codec: codec in working
    monkeypatch.setitem(sys.modules, "scripts.ffmpeg_toolchain", module)


def _encoder_list(*codecs):
    return mock.Mock(stdout="\n".join(codecs), returncode=0)


def test_detect_best_encoder_prefers_nvenc(monkeypatch):
    _stub_toolchain(monkeypatch, {"h264_nvenc"})
    with mock.patch.object(cs.subprocess, "run",
                           return_value=_encoder_list("h264_nvenc", "h264_amf")):
        assert cs._detect_best_encoder() == (
            "h264_nvenc", "p1", ["-b:v", "5M"])


def test_detect_best_encoder_skips_listed_but_broken_encoder(monkeypatch):
    _stub_toolchain(monkeypatch, set())  # listed, but the probe encode fails
    with mock.patch.object(cs.subprocess, "run",
                           return_value=_encoder_list("h264_nvenc")):
        assert cs._detect_best_encoder()[0] == "libx264"


def test_detect_best_encoder_falls_back_to_cpu(monkeypatch):
    _stub_toolchain(monkeypatch, {"h264_amf"})
    with mock.patch.object(cs.subprocess, "run",
                           return_value=_encoder_list("h264_amf")):
        assert cs._detect_best_encoder() == (
            "h264_amf", "quality", ["-b:v", "5M"])
    with mock.patch.object(cs.subprocess, "run",
                           side_effect=OSError("no ffmpeg")):
        assert cs._detect_best_encoder() == (
            "libx264", "ultrafast", ["-crf", "23"])


# ---------------------------------------------------------------------------
# _process_segment (ffmpeg mocked; the command list is asserted, never run)
# ---------------------------------------------------------------------------

def _segment(**overrides):
    segment = {"start_time": 5.0, "end_time": 20.0, "title": "Hello World!"}
    segment.update(overrides)
    return segment


@pytest.fixture
def _cut_env(tmp_path, monkeypatch):
    cuts = tmp_path / "cuts"
    subs = tmp_path / "subs"
    cuts.mkdir()
    subs.mkdir()
    monkeypatch.setattr(cs, "validate_media_file",
                        lambda path, **kw: {"ok": True})
    calls = []
    monkeypatch.setattr(cs.cut_json, "cut_json_transcript",
                        lambda *args: calls.append(args))
    return {"cuts": str(cuts), "subs": str(subs), "json_calls": calls,
            "project": str(tmp_path)}


def _fake_ffmpeg_success(calls):
    """subprocess.run replacement that materialises the temp output file."""
    def run(cmd, **kwargs):
        calls.append(cmd)
        output = cmd[-1]
        with open(output, "wb") as fh:
            fh.write(b"\x00" * 2048)
        return mock.Mock(returncode=0, stdout="", stderr="")
    return run


def test_process_segment_builds_ffmpeg_command(_cut_env, monkeypatch):
    calls = []
    monkeypatch.setattr(cs.subprocess, "run", _fake_ffmpeg_success(calls))
    result = cs._process_segment(
        0, _segment(), _cut_env["project"], "/in/input.mp4",
        _cut_env["cuts"], _cut_env["subs"], False,
        "libx264", "ultrafast", ["-crf", "23"])
    assert result["ok"] is True
    assert result["output"] == "000_Hello_World_original_scale.mp4"
    assert result["json"] == "000_Hello_World_processed.json"
    assert len(calls) == 1
    cmd = calls[0]
    assert cmd[0] == "ffmpeg"
    assert cmd[cmd.index("-ss") + 1] == "5.000"
    assert cmd[cmd.index("-t") + 1] == "15.000"
    assert cmd[cmd.index("-c:v") + 1] == "libx264"
    assert cmd[cmd.index("-preset") + 1] == "ultrafast"
    assert "-crf" in cmd and "23" in cmd
    assert cmd[cmd.index("-c:a") + 1] == "aac"
    assert cmd[-1].endswith("_original_scale.mp4.tmp.mp4")
    # The temp file was promoted and the JSON cut was requested.
    assert os.path.isfile(os.path.join(
        _cut_env["cuts"], "000_Hello_World_original_scale.mp4"))
    assert _cut_env["json_calls"]
    start, end = _cut_env["json_calls"][0][2], _cut_env["json_calls"][0][3]
    assert (start, end) == (5.0, 20.0)


def test_process_segment_rejects_non_positive_duration(_cut_env, monkeypatch):
    run = mock.Mock()
    monkeypatch.setattr(cs.subprocess, "run", run)
    result = cs._process_segment(
        0, _segment(start_time=20.0, end_time=10.0), _cut_env["project"],
        "/in/input.mp4", _cut_env["cuts"], _cut_env["subs"], False,
        "libx264", "ultrafast", [])
    assert result["ok"] is False
    assert "non-positive duration" in result["error"]
    run.assert_not_called()


def test_process_segment_ffmpeg_failure_reports_error(_cut_env, monkeypatch):
    def failing(cmd, **kwargs):
        raise subprocess.CalledProcessError(1, cmd, stderr="boom")
    monkeypatch.setattr(cs.subprocess, "run", failing)
    result = cs._process_segment(
        0, _segment(), _cut_env["project"], "/in/input.mp4",
        _cut_env["cuts"], _cut_env["subs"], False,
        "libx264", "ultrafast", [])
    assert result["ok"] is False
    assert result["output"] == "000_Hello_World_original_scale.mp4"
    # The JSON cut is still attempted so the failure is diagnosable.
    assert _cut_env["json_calls"]


def test_process_segment_media_validation_failure(_cut_env, monkeypatch):
    monkeypatch.setattr(cs, "validate_media_file",
                        lambda path, **kw: {"ok": False,
                                            "errors": ["duration too short"]})
    monkeypatch.setattr(cs.subprocess, "run", _fake_ffmpeg_success([]))
    result = cs._process_segment(
        0, _segment(), _cut_env["project"], "/in/input.mp4",
        _cut_env["cuts"], _cut_env["subs"], False,
        "libx264", "ultrafast", [])
    assert result["ok"] is False
    assert "duration too short" in result["error"]


def test_process_segment_skip_video_uses_existing_file(_cut_env, monkeypatch):
    existing = os.path.join(_cut_env["cuts"], "000_Hello_World_original_scale.mp4")
    with open(existing, "wb") as fh:
        fh.write(b"\x00" * 2048)
    run = mock.Mock()
    monkeypatch.setattr(cs.subprocess, "run", run)
    result = cs._process_segment(
        0, _segment(), _cut_env["project"], "/in/input.mp4",
        _cut_env["cuts"], _cut_env["subs"], True,
        "libx264", "ultrafast", [])
    assert result["ok"] is True
    run.assert_not_called()


def test_process_segment_skip_video_missing_file_fails(_cut_env, monkeypatch):
    monkeypatch.setattr(cs.subprocess, "run", mock.Mock())
    result = cs._process_segment(
        0, _segment(), _cut_env["project"], "/in/input.mp4",
        _cut_env["cuts"], _cut_env["subs"], True,
        "libx264", "ultrafast", [])
    assert result["ok"] is False
    assert "missing or empty" in result["error"]


def test_process_segment_title_is_sanitised_for_filename(_cut_env, monkeypatch):
    monkeypatch.setattr(cs.subprocess, "run", _fake_ffmpeg_success([]))
    result = cs._process_segment(
        0, _segment(title="Weird:/Title*?<>|  with  spaces"), _cut_env["project"],
        "/in/input.mp4", _cut_env["cuts"], _cut_env["subs"], False,
        "libx264", "ultrafast", [])
    assert result["ok"] is True
    assert result["output"].startswith("000_WeirdTitle__with__spaces")
    assert "/" not in result["output"] and ":" not in result["output"]


# ---------------------------------------------------------------------------
# _persist_cut_validation / _clear_downstream_artifacts / cut() guards
# ---------------------------------------------------------------------------

def test_persist_cut_validation_merges_in_original_order(tmp_path):
    payload = {"segments": [
        {"title": "a", "final_validation": {"ok": True}},
        {"title": "b", "final_validation": {"ok": True}},
    ]}
    path = tmp_path / "viral_segments.txt"
    path.write_text(json.dumps(payload), encoding="utf-8")
    allowed = [(0, {"title": "a", "cut_status": "allowed"})]
    refused = [(1, {"title": "b", "cut_status": "refused"})]
    cs._persist_cut_validation(str(tmp_path), payload["segments"], allowed, refused)
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert [s["title"] for s in saved["segments"]] == ["a", "b"]
    assert saved["segments"][1]["cut_status"] == "refused"


def test_persist_cut_validation_noops_safely(tmp_path):
    # Missing file: nothing to persist.
    cs._persist_cut_validation(str(tmp_path), [], [], [])
    assert not (tmp_path / "viral_segments.txt").exists()
    # Length mismatch: the on-disk list does not match memory; leave it alone.
    path = tmp_path / "viral_segments.txt"
    path.write_text(json.dumps({"segments": [{"title": "a"}]}), encoding="utf-8")
    cs._persist_cut_validation(str(tmp_path), [{"title": "a"}, {"title": "b"}],
                               [(0, {"title": "a"})], [(1, {"title": "b"})])
    assert json.loads(path.read_text(encoding="utf-8"))["segments"] == [{"title": "a"}]
    # Malformed JSON: also left alone, no exception.
    path.write_text("{broken", encoding="utf-8")
    cs._persist_cut_validation(str(tmp_path), [], [], [])
    assert path.read_text(encoding="utf-8") == "{broken"


def test_clear_downstream_artifacts_removes_only_media_and_reports(tmp_path):
    for folder in ("final", "final_polished", "burned_sub"):
        (tmp_path / folder).mkdir()
        (tmp_path / folder / "clip.mp4").write_bytes(b"\x00")
        (tmp_path / folder / "keep.txt").write_text("keep")
    (tmp_path / "polish_report.json").write_text("{}")
    (tmp_path / "other.json").write_text("{}")
    removed = cs._clear_downstream_artifacts(str(tmp_path))
    assert len(removed) == 4  # 3 clips + 1 report
    assert (tmp_path / "final" / "keep.txt").exists()
    assert (tmp_path / "other.json").exists()
    assert not (tmp_path / "polish_report.json").exists()
    assert cs._clear_downstream_artifacts(str(tmp_path)) == []


def test_cut_missing_input_raises_file_not_found(tmp_path):
    with pytest.raises(FileNotFoundError):
        cs.cut({"segments": [_segment()]}, project_folder=str(tmp_path))


def test_cut_empty_segment_list_raises_value_error(tmp_path, monkeypatch):
    (tmp_path / "input.mp4").write_bytes(b"\x00" * 64)
    monkeypatch.setattr(cs.subprocess, "run",
                        mock.Mock(return_value=mock.Mock(stdout="", stderr="")))
    monkeypatch.setattr(cs, "segment_validator", None)
    with pytest.raises(ValueError, match="No safe segments"):
        cs.cut({"segments": []}, project_folder=str(tmp_path))
