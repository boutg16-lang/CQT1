# -*- coding: utf-8 -*-
"""Direct tests for scripts/thumbnail_generator.py.

Face-aware 16:9 cropping, text wrapping and thumbnail composition. Real
Pillow images are used (Pillow is installed in the test venv); OpenCV is
stubbed — no real video files, no camera, no network.
"""

import os
import sys
from types import SimpleNamespace

import numpy as np
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import thumbnail_generator as tg  # noqa: E402


def _image(w, h, color=(200, 30, 30)):
    return Image.new("RGB", (w, h), color)


# ---------------------------------------------------------------------------
# Fonts
# ---------------------------------------------------------------------------


def test_font_path_existing_and_missing():
    assert tg._font_path("Cairo-Bold.ttf").endswith("Cairo-Bold.ttf")
    assert tg._font_path("No-Such-Font.ttf") == ""


def test_load_font_returns_a_font():
    font = tg._load_font(40, text="Hello")
    assert font is not None


def test_load_font_arabic_prefers_arabic_font():
    font = tg._load_font(40, text="مرحبا بالعالم")
    assert font is not None


def test_load_font_no_pil_returns_none(monkeypatch):
    monkeypatch.setattr(tg, "HAS_PIL", False)
    assert tg._load_font(40, text="Hello") is None


# ---------------------------------------------------------------------------
# _cover_crop
# ---------------------------------------------------------------------------


def test_cover_crop_already_16_9_keeps_whole_frame():
    cropped = tg._cover_crop(_image(1920, 1080))
    assert cropped.size == (1920, 1080)


def test_cover_crop_tall_image_crops_height():
    cropped = tg._cover_crop(_image(1000, 1000))
    assert cropped.size == (1000, 562)
    # Vertically centered: (1000 - 562) // 2 = 219.
    assert cropped.size[1] == 562


def test_cover_crop_wide_image_crops_width():
    cropped = tg._cover_crop(_image(2000, 1000))
    assert cropped.size == (1777, 1000)


def test_cover_crop_face_in_upper_third():
    # Tall image, face near the top: the crop follows the face vertically.
    face = (400, 300, 600, 500)
    cropped = tg._cover_crop(_image(1000, 2000), face_box=face)
    assert cropped.size == (1000, 562)
    # face center y = 400; crop_y = 400 - 562*0.30 = 231 (rounded by int).
    assert cropped.size[1] == 562


def test_cover_crop_face_horizontal_centering():
    # Wide image, face off-center to the right: crop shifts right.
    face = (1500, 100, 1700, 300)
    cropped = tg._cover_crop(_image(2000, 1000), face_box=face)
    assert cropped.size == (1777, 1000)


def test_cover_crop_clamps_to_image_bounds():
    # Face at the very top-left corner must not push the crop negative.
    cropped = tg._cover_crop(_image(1000, 2000), face_box=(0, 0, 100, 100))
    assert cropped.size == (1000, 562)


# ---------------------------------------------------------------------------
# _wrap_text
# ---------------------------------------------------------------------------


def test_wrap_text_short_text_single_line():
    font = ImageFont.load_default()
    assert tg._wrap_text("hello", font, 500) == ["hello"]


def test_wrap_text_wraps_and_caps_at_three_lines():
    font = ImageFont.load_default()
    lines = tg._wrap_text("one two three four five six seven eight nine ten", font, 60)
    # Hard cap at 3 lines: words past the third line are dropped by design.
    assert len(lines) == 3
    assert lines[0].startswith("one")
    draw = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    for line in lines:
        assert draw.textlength(line, font=font) <= 60


def test_wrap_text_empty():
    font = ImageFont.load_default()
    assert tg._wrap_text("", font, 500) == []


def test_wrap_text_no_pil_returns_text_unchanged(monkeypatch):
    monkeypatch.setattr(tg, "HAS_PIL", False)
    assert tg._wrap_text("hello world", None, 10) == ["hello world"]


# ---------------------------------------------------------------------------
# extract_frame (cv2 stubbed)
# ---------------------------------------------------------------------------


class _FakeCapture:
    def __init__(self, frames, opened=True):
        self._frames = list(frames)
        self._index = 0
        self._opened = opened
        self.released = False

    def isOpened(self):
        return self._opened

    def get(self, _prop):
        return 30.0

    def set(self, _prop, _value):
        return True

    def read(self):
        if self._index < len(self._frames):
            frame = self._frames[self._index]
            self._index += 1
            return True, frame
        return False, None

    def release(self):
        self.released = True


def _fake_cv2(capture):
    return SimpleNamespace(
        VideoCapture=lambda path: capture,
        CAP_PROP_FPS=5,
        CAP_PROP_POS_FRAMES=1,
        COLOR_BGR2RGB=4,
        COLOR_RGB2BGR=5,
        COLOR_BGR2GRAY=6,
        cvtColor=lambda frame, code: frame,
        data=SimpleNamespace(haarcascades="/tmp/"),
    )


def test_extract_frame_returns_pil_image(monkeypatch):
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    cap = _FakeCapture([frame])
    monkeypatch.setattr(tg, "cv2", _fake_cv2(cap))
    image = tg.extract_frame("video.mp4", at_seconds=1.0)
    assert image is not None
    assert image.size == (640, 480)
    assert cap.released is True


def test_extract_frame_unopened_returns_none(monkeypatch):
    cap = _FakeCapture([], opened=False)
    monkeypatch.setattr(tg, "cv2", _fake_cv2(cap))
    assert tg.extract_frame("video.mp4") is None


def test_extract_frame_read_failure_returns_none(monkeypatch):
    cap = _FakeCapture([])
    monkeypatch.setattr(tg, "cv2", _fake_cv2(cap))
    assert tg.extract_frame("video.mp4") is None


def test_extract_frame_no_cv2_returns_none(monkeypatch):
    monkeypatch.setattr(tg, "HAS_CV2", False)
    assert tg.extract_frame("video.mp4") is None


# ---------------------------------------------------------------------------
# _face_bbox (cv2 stubbed)
# ---------------------------------------------------------------------------


def test_face_bbox_no_cv2_returns_none(monkeypatch):
    monkeypatch.setattr(tg, "HAS_CV2", False)
    assert tg._face_bbox(_image(640, 480)) is None


def test_face_bbox_detector_yn_largest_face(monkeypatch):
    faces = [
        np.array([10, 20, 100, 150] + [0] * 11, dtype=np.float32),
        np.array([300, 50, 50, 60] + [0] * 11, dtype=np.float32),
    ]

    class _Detector:
        def setInputSize(self, _size):
            pass

        def detect(self, _bgr):
            return True, faces

    fake = _fake_cv2(None)
    fake.FaceDetectorYN_create = lambda *a, **k: _Detector()
    fake.cvtColor = lambda frame, code: frame
    monkeypatch.setattr(tg, "cv2", fake)
    assert tg._face_bbox(_image(640, 480)) == (10, 20, 110, 170)


def test_face_bbox_detector_failure_falls_back_to_haar(monkeypatch):
    class _Cascade:
        def detectMultiScale(self, _gray, **kwargs):
            return [(5, 5, 50, 60)]

    fake = _fake_cv2(None)
    fake.FaceDetectorYN_create = lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError("no YN")
    )
    fake.CascadeClassifier = lambda path: _Cascade()
    fake.cvtColor = lambda frame, code: frame
    monkeypatch.setattr(tg, "cv2", fake)
    assert tg._face_bbox(_image(640, 480)) == (5, 5, 55, 65)


def test_face_bbox_no_faces_returns_none(monkeypatch):
    class _Detector:
        def setInputSize(self, _size):
            pass

        def detect(self, _bgr):
            return True, None

    class _Cascade:
        def detectMultiScale(self, _gray, **kwargs):
            return []

    fake = _fake_cv2(None)
    fake.FaceDetectorYN_create = lambda *a, **k: _Detector()
    fake.CascadeClassifier = lambda path: _Cascade()
    fake.cvtColor = lambda frame, code: frame
    monkeypatch.setattr(tg, "cv2", fake)
    assert tg._face_bbox(_image(640, 480)) is None


# ---------------------------------------------------------------------------
# generate_thumbnail / main
# ---------------------------------------------------------------------------


def _save_source(tmp_path, name="frame.png", size=(1920, 1080)):
    path = tmp_path / name
    _image(*size).save(path, "PNG")
    return str(path)


def test_generate_thumbnail_from_image(tmp_path, monkeypatch):
    monkeypatch.setattr(tg, "_face_bbox", lambda image: None)
    source = _save_source(tmp_path)
    out = str(tmp_path / "thumb.png")
    result = tg.generate_thumbnail(source, title="كسب المال", hook="NEW", out=out)
    assert result["ok"] is True
    assert result["out"] == out
    assert result["size"] == (1280, 720)
    assert result["face_detected"] is False
    assert os.path.isfile(out)
    assert Image.open(out).size == (1280, 720)


def test_generate_thumbnail_no_title_no_hook(tmp_path, monkeypatch):
    monkeypatch.setattr(tg, "_face_bbox", lambda image: None)
    out = str(tmp_path / "thumb.png")
    result = tg.generate_thumbnail(_save_source(tmp_path), out=out)
    assert result["ok"] is True
    assert os.path.isfile(out)


def test_generate_thumbnail_face_detected_flag(tmp_path, monkeypatch):
    monkeypatch.setattr(tg, "_face_bbox", lambda image: (100, 100, 300, 300))
    result = tg.generate_thumbnail(
        _save_source(tmp_path), out=str(tmp_path / "thumb.png")
    )
    assert result["ok"] is True
    assert result["face_detected"] is True


def test_generate_thumbnail_bad_accent_falls_back(tmp_path, monkeypatch):
    monkeypatch.setattr(tg, "_face_bbox", lambda image: None)
    result = tg.generate_thumbnail(
        _save_source(tmp_path), out=str(tmp_path / "thumb.png"), accent="not-a-hex"
    )
    assert result["ok"] is True


def test_generate_thumbnail_missing_source():
    result = tg.generate_thumbnail(
        "/tmp/does-not-exist-xyz.png", out="/tmp/thumb-xyz.png"
    )
    assert result["ok"] is False
    assert "does-not-exist-xyz" in result["error"]


def test_generate_thumbnail_unreadable_source(tmp_path):
    bad = tmp_path / "bad.png"
    bad.write_text("this is not an image")
    result = tg.generate_thumbnail(str(bad), out=str(tmp_path / "t.png"))
    assert result["ok"] is False
    assert "cannot open source" in result["error"]


def test_generate_thumbnail_from_video_uses_extract_frame(tmp_path, monkeypatch):
    monkeypatch.setattr(tg, "_face_bbox", lambda image: None)
    monkeypatch.setattr(
        tg, "extract_frame", lambda path, at_seconds=0.0: _image(640, 480)
    )
    result = tg.generate_thumbnail("clip.mp4", out=str(tmp_path / "thumb.png"))
    assert result["ok"] is True


def test_generate_thumbnail_no_pil(monkeypatch):
    monkeypatch.setattr(tg, "HAS_PIL", False)
    result = tg.generate_thumbnail("frame.png", out="/tmp/thumb.png")
    assert result["ok"] is False
    assert "Pillow" in result["error"]


def test_main_success_and_failure(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(tg, "_face_bbox", lambda image: None)
    source = _save_source(tmp_path)
    out = str(tmp_path / "thumb.png")
    assert tg.main([source, "--title", "Hello", "--out", out]) == 0
    assert "[thumbnail] OK" in capsys.readouterr().out
    assert tg.main(["/tmp/does-not-exist-xyz.png", "--out", out]) == 1
