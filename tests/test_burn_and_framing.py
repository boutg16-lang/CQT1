# -*- coding: utf-8 -*-
"""Coverage-strengthening tests for two low-coverage edit/subtitle modules.

``scripts/burn_subtitles.py`` (ffmpeg filter escaping + encoder fallback) and
``scripts/one_face.py`` (single-face 9:16 framing) sat at 15% / 11% coverage
in the 2026-10-04 audit. These tests exercise the deterministic logic without
a real video: subprocess calls are stubbed and frames are synthetic arrays.
"""

import os
import subprocess
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import burn_subtitles as bs
from scripts import one_face

# ---------------------------------------------------------------------------
# burn_subtitles — ffmpeg filtergraph escaping
# ---------------------------------------------------------------------------

def test_ffmpeg_filter_value_escapes_special_characters():
    assert bs._ffmpeg_filter_value("C:\\tmp\\subs.srt") == "C\\:/tmp/subs.srt"
    assert bs._ffmpeg_filter_value("a'b:c") == "a\\'b\\:c"
    assert bs._ffmpeg_filter_value(None) == ""
    assert bs._ffmpeg_filter_value("") == ""


def test_subtitles_filter_quotes_path_and_adds_fontsdir():
    vf = bs._subtitles_filter("/tmp/subs.srt")
    assert vf.startswith("subtitles='")
    assert "/tmp/subs.srt" in vf
    if bs._fonts_dir():  # the repo ships fonts/
        assert ":fontsdir='" in vf


def test_fonts_dir_points_at_existing_directory():
    fonts = bs._fonts_dir()
    assert fonts and os.path.isdir(fonts) and fonts.endswith("fonts")


def test_detect_best_encoder_defaults_to_libx264_without_hardware(monkeypatch):
    monkeypatch.setattr(bs.subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout="", stderr=""))
    assert bs._detect_best_encoder() == ("libx264", "ultrafast")


def test_burn_video_file_uses_detected_hardware_encoder(monkeypatch):
    monkeypatch.setattr(bs, "_detect_best_encoder", lambda: ("h264_nvenc", "p1"))
    calls = []
    monkeypatch.setattr(bs.subprocess, "run",
                        lambda cmd, **kw: calls.append(cmd) or subprocess.CompletedProcess(cmd, 0))

    ok, msg = bs.burn_video_file("in.mp4", "/tmp/subs.srt", "out.mp4")

    assert ok is True and "h264_nvenc" in msg
    assert calls and calls[0][0] == "ffmpeg" and "h264_nvenc" in calls[0]


def test_burn_video_file_falls_back_to_cpu_when_hardware_fails(monkeypatch):
    monkeypatch.setattr(bs, "_detect_best_encoder", lambda: ("h264_nvenc", "p1"))

    def fake_run(cmd, **kw):
        if "h264_nvenc" in cmd:
            raise subprocess.CalledProcessError(1, cmd)
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(bs.subprocess, "run", fake_run)
    ok, msg = bs.burn_video_file("in.mp4", "/tmp/subs.srt", "out.mp4")
    assert ok is True and "CPU" in msg


def test_burn_video_file_reports_fatal_error(monkeypatch):
    monkeypatch.setattr(bs, "_detect_best_encoder", lambda: ("libx264", "ultrafast"))

    def always_fail(cmd, **kw):
        raise subprocess.CalledProcessError(1, cmd)

    monkeypatch.setattr(bs.subprocess, "run", always_fail)
    ok, msg = bs.burn_video_file("clip.mp4", "/tmp/subs.srt", "out.mp4")
    assert ok is False and "Fatal error" in msg and "clip.mp4" in msg


def test_burn_video_file_prefer_cpu_skips_hardware(monkeypatch):
    calls = []
    monkeypatch.setattr(bs.subprocess, "run",
                        lambda cmd, **kw: calls.append(cmd) or subprocess.CompletedProcess(cmd, 0))

    ok, msg = bs.burn_video_file("in.mp4", "/tmp/subs.srt", "out.mp4",
                                 prefer_hardware_acceleration=False)
    assert ok is True and "CPU" in msg
    assert calls and all("libx264" in cmd for cmd in calls)


# ---------------------------------------------------------------------------
# one_face — single-face 9:16 framing
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("shape", [(720, 1280, 3), (1080, 1920, 3), (480, 640, 3)])
def test_crop_and_resize_single_face_returns_9x16_canvas(shape):
    frame = np.zeros(shape, dtype=np.uint8)
    frame[:] = (10, 20, 30)
    face = (shape[1] // 2 - 50, shape[0] // 2 - 50, 100, 100)  # (x, y, w, h)

    out = one_face.crop_and_resize_single_face(frame, face)

    assert out.shape == (1920, 1080, 3)
    assert out.dtype == np.uint8


@pytest.mark.parametrize("shape", [(720, 1280, 3), (1080, 1920, 3)])
def test_resize_with_padding_returns_9x16_canvas(shape):
    frame = np.full(shape, 200, dtype=np.uint8)
    out = one_face.resize_with_padding(frame)
    assert out.shape == (1920, 1080, 3)


def test_crop_center_zoom_returns_9x16_canvas():
    frame = np.full((1080, 1920, 3), 42, dtype=np.uint8)
    out = one_face.crop_center_zoom(frame)
    assert out.shape == (1920, 1080, 3)


class _StubResults:
    def __init__(self, detections=None, landmarks=None, pose=None):
        self.detections = detections
        self.multi_face_landmarks = landmarks
        self.pose_landmarks = pose


class _StubDetector:
    def __init__(self, results):
        self._results = results

    def process(self, _rgb):
        return self._results


class _BoxNS:
    def __init__(self, xmin, ymin, width, height):
        self.xmin, self.ymin, self.width, self.height = xmin, ymin, width, height


class _Detection:
    def __init__(self, box):
        self.location_data = type("L", (), {"relative_bounding_box": box})()


class _Landmark:
    def __init__(self, x, y):
        self.x, self.y = x, y


def test_detect_face_or_body_returns_none_when_nothing_detected():
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    empty = _StubDetector(_StubResults())
    assert one_face.detect_face_or_body(frame, empty, empty, empty) is None


def test_detect_face_or_body_maps_face_detection_to_pixels():
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    detections = [_Detection(_BoxNS(0.25, 0.50, 0.10, 0.20))]
    detector = _StubDetector(_StubResults(detections=detections))

    result = one_face.detect_face_or_body(frame, detector, detector, detector)

    assert result == [(160, 240, 64, 96)]  # xmin*640, ymin*480, w*640, h*480


def test_detect_face_or_body_uses_pose_when_no_face():
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    pose_landmarks = [_Landmark(0.20, 0.10), _Landmark(0.60, 0.90)]
    detector = _StubDetector(_StubResults(pose=type("P", (), {
        "landmark": pose_landmarks})()))

    result = one_face.detect_face_or_body(frame, detector, detector, detector)

    assert result == [(128, 48, 256, 384)]  # x: 0.20..0.60*640, y: 0.10..0.90*480
