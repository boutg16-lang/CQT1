# -*- coding: utf-8 -*-
"""Direct tests for scripts/media_validation.py.

Covers: ffprobe probing (subprocess mocked — ffprobe is never run),
per-file validation (duration/size/streams/aspect), rendered-output
discovery, and project-level validation. No real media is needed.
"""

import json
import os
from unittest import mock

import pytest

from scripts import media_validation as mv


def _ffprobe_payload(**overrides):
    payload = {
        "format": {"duration": "12.5", "size": "123456"},
        "streams": [
            {"index": 0, "codec_type": "video", "codec_name": "h264",
             "width": 1080, "height": 1920, "r_frame_rate": "30/1"},
            {"index": 1, "codec_type": "audio", "codec_name": "aac"},
        ],
    }
    payload.update(overrides)
    return payload


def _completed(payload, returncode=0, stderr=""):
    return mock.Mock(returncode=returncode,
                     stdout=json.dumps(payload) if payload is not None else "",
                     stderr=stderr)


def _media_file(tmp_path, name="clip.mp4", size=2048):
    path = tmp_path / name
    path.write_bytes(b"\x00" * size)
    return str(path)


# ---------------------------------------------------------------------------
# _aspect_ratio_value
# ---------------------------------------------------------------------------

def test_aspect_ratio_value_forms():
    assert mv._aspect_ratio_value("16:9") == pytest.approx(16 / 9)
    assert mv._aspect_ratio_value("9:16") == pytest.approx(9 / 16)
    assert mv._aspect_ratio_value(1.777) == pytest.approx(1.777)
    assert mv._aspect_ratio_value(2) == 2.0
    assert mv._aspect_ratio_value(None) is None
    assert mv._aspect_ratio_value("") is None
    assert mv._aspect_ratio_value("not-a-ratio") is None
    assert mv._aspect_ratio_value("16:0") is None
    assert mv._aspect_ratio_value("16") is None


# ---------------------------------------------------------------------------
# probe_media
# ---------------------------------------------------------------------------

def test_probe_media_missing_file(tmp_path):
    report = mv.probe_media(str(tmp_path / "nope.mp4"))
    assert report == {"ok": False, "path": os.path.abspath(str(tmp_path / "nope.mp4")),
                      "error": "file not found"}


def test_probe_media_success_parses_streams(tmp_path):
    path = _media_file(tmp_path)
    with mock.patch("scripts.media_validation.subprocess.run",
                    return_value=_completed(_ffprobe_payload())) as run:
        report = mv.probe_media(path)
    assert report["ok"] is True
    assert report["duration"] == 12.5
    assert report["size"] == 123456
    assert report["video"]["codec_name"] == "h264"
    assert report["audio"]["codec_name"] == "aac"
    assert len(report["streams"]) == 2
    cmd = run.call_args.args[0]
    # No explicit binary: shutil.which resolves ffprobe on this machine.
    assert cmd[0].endswith("ffprobe")
    assert "-show_entries" in cmd
    assert cmd[-1] == os.path.abspath(path)


def test_probe_media_uses_explicit_ffprobe_binary(tmp_path):
    path = _media_file(tmp_path)
    with mock.patch("scripts.media_validation.subprocess.run",
                    return_value=_completed(_ffprobe_payload())) as run:
        mv.probe_media(path, ffprobe="/usr/bin/ffprobe")
    assert run.call_args.args[0][0] == "/usr/bin/ffprobe"


def test_probe_media_ffprobe_failure_reports_stderr(tmp_path):
    path = _media_file(tmp_path)
    with mock.patch("scripts.media_validation.subprocess.run",
                    return_value=_completed(None, returncode=1,
                                            stderr="moov atom not found")):
        report = mv.probe_media(path)
    assert report["ok"] is False
    assert report["error"] == "moov atom not found"


def test_probe_media_invalid_json_and_os_errors(tmp_path):
    path = _media_file(tmp_path)
    with mock.patch("scripts.media_validation.subprocess.run",
                    return_value=mock.Mock(returncode=0, stdout="{broken", stderr="")):
        report = mv.probe_media(path)
    assert report["ok"] is False
    assert report["error"].startswith("invalid ffprobe JSON")
    with mock.patch("scripts.media_validation.subprocess.run",
                    side_effect=OSError("no such binary")):
        report = mv.probe_media(path)
    assert report["ok"] is False
    assert "no such binary" in report["error"]
    import subprocess
    with mock.patch("scripts.media_validation.subprocess.run",
                    side_effect=subprocess.TimeoutExpired(cmd="ffprobe", timeout=30)):
        report = mv.probe_media(path)
    assert report["ok"] is False


def test_probe_media_size_na_falls_back_to_filesystem(tmp_path):
    path = _media_file(tmp_path, size=4096)
    payload = _ffprobe_payload(format={"duration": "5", "size": "N/A"})
    with mock.patch("scripts.media_validation.subprocess.run",
                    return_value=_completed(payload)):
        report = mv.probe_media(path)
    assert report["ok"] is True
    assert report["size"] == 4096


def test_probe_media_no_video_stream_is_still_ok(tmp_path):
    path = _media_file(tmp_path)
    payload = _ffprobe_payload(streams=[
        {"index": 0, "codec_type": "audio", "codec_name": "aac"}])
    with mock.patch("scripts.media_validation.subprocess.run",
                    return_value=_completed(payload)):
        report = mv.probe_media(path)
    assert report["ok"] is True
    assert report["video"] is None
    assert report["audio"] is not None


# ---------------------------------------------------------------------------
# validate_media_file (probe mocked at the module boundary)
# ---------------------------------------------------------------------------

def _probe_result(**overrides):
    result = {"ok": True, "path": "/x/clip.mp4", "duration": 12.5,
              "size": 123456,
              "video": {"codec_type": "video", "width": 1080, "height": 1920},
              "audio": {"codec_type": "audio"}, "streams": []}
    result.update(overrides)
    return result


def test_validate_media_file_ok_and_error_paths(monkeypatch):
    monkeypatch.setattr(mv, "probe_media", lambda path: _probe_result())
    report = mv.validate_media_file("/x/clip.mp4")
    assert report["ok"] is True
    assert report["errors"] == []


@pytest.mark.parametrize("override,expected", [
    ({"size": 0}, "empty file"),
    ({"video": None}, "missing video stream"),
    ({"duration": 0.01}, "duration too short"),
    ({"audio": None}, "missing audio stream"),
])
def test_validate_media_file_single_failures(monkeypatch, override, expected):
    monkeypatch.setattr(mv, "probe_media", lambda path: _probe_result(**override))
    require_audio = expected == "missing audio stream"
    report = mv.validate_media_file("/x/clip.mp4", require_audio=require_audio)
    assert report["ok"] is False
    assert expected in report["errors"]


def test_validate_media_file_aspect_ratio(monkeypatch):
    monkeypatch.setattr(mv, "probe_media", lambda path: _probe_result())
    # 1080x1920 is 9:16 = 0.5625; expecting 16:9 must fail.
    report = mv.validate_media_file("/x/clip.mp4", expected_aspect="16:9")
    assert report["ok"] is False
    assert any("unexpected aspect ratio" in e for e in report["errors"])
    assert "0.562" in report["errors"][0] and "1.778" in report["errors"][0]
    # Within tolerance it passes; a numeric expectation also works.
    assert mv.validate_media_file("/x/clip.mp4",
                                  expected_aspect="9:16")["ok"] is True
    assert mv.validate_media_file("/x/clip.mp4",
                                  expected_aspect=0.5625)["ok"] is True
    # No video dimensions -> aspect is not checked.
    monkeypatch.setattr(mv, "probe_media",
                        lambda path: _probe_result(video={"codec_type": "video"}))
    assert mv.validate_media_file("/x/clip.mp4",
                                  expected_aspect="16:9")["ok"] is True


def test_validate_media_file_propagates_probe_failure(monkeypatch):
    monkeypatch.setattr(mv, "probe_media",
                        lambda path: {"ok": False, "path": path,
                                      "error": "file not found"})
    report = mv.validate_media_file("/x/nope.mp4")
    assert report == {"ok": False, "path": "/x/nope.mp4", "error": "file not found"}


# ---------------------------------------------------------------------------
# output_files / validate_project_outputs
# ---------------------------------------------------------------------------

def _project(tmp_path, layout):
    """Create files from {relative_dir: [filenames]} under tmp_path."""
    for directory, names in layout.items():
        folder = tmp_path / directory
        folder.mkdir(parents=True, exist_ok=True)
        for name in names:
            (folder / name).write_bytes(b"\x00" * 64)
    return str(tmp_path)


def test_output_files_prefers_first_populated_dir(tmp_path):
    project = _project(tmp_path / "one", {
        "burned_sub": ["a.mp4"],
        "final": ["b.mp4", "c.mp4"],
    })
    assert mv.output_files(project) == [os.path.join(project, "burned_sub", "a.mp4")]
    project = _project(tmp_path / "two", {"final": ["b.mp4", "a.mp4"]})
    assert mv.output_files(project) == [
        os.path.join(project, "final", "a.mp4"),
        os.path.join(project, "final", "b.mp4")]


def test_output_files_ignores_temp_preview_and_non_media(tmp_path):
    project = _project(tmp_path, {"cuts": [
        "temp_x.mp4", "preview_y.mp4", "notes.txt", "clip.MOV", "sub.srt"]})
    assert mv.output_files(project) == [os.path.join(project, "cuts", "clip.MOV")]


def test_output_files_empty_and_missing_project(tmp_path):
    assert mv.output_files(str(tmp_path)) == []
    assert mv.output_files("/nonexistent/project") == []


def test_output_files_walks_nested_directories(tmp_path):
    project = _project(tmp_path, {"final": []})
    nested = tmp_path / "final" / "nested"
    nested.mkdir()
    (nested / "deep.mp4").write_bytes(b"\x00" * 64)
    assert mv.output_files(project) == [str(nested / "deep.mp4")]


def test_validate_project_outputs_no_outputs(tmp_path):
    report = mv.validate_project_outputs(str(tmp_path))
    assert report == {"ok": False, "outputs": [], "errors": ["no rendered media found"]}
    report = mv.validate_project_outputs(str(tmp_path), require_outputs=False)
    assert report["ok"] is True


def test_validate_project_outputs_aggregates_per_file_errors(tmp_path, monkeypatch):
    project = _project(tmp_path, {"final": ["good.mp4", "bad.mp4"]})

    def fake_validate(path, **kwargs):
        if path.endswith("bad.mp4"):
            return {"ok": False, "path": path, "errors": ["duration too short"]}
        return {"ok": True, "path": path, "errors": []}

    monkeypatch.setattr(mv, "validate_media_file", fake_validate)
    report = mv.validate_project_outputs(project)
    assert report["ok"] is False
    assert len(report["outputs"]) == 2
    assert report["errors"] == ["bad.mp4: duration too short"]
    assert mv.validate_project_outputs(project)["errors"][0].startswith("bad.mp4")
