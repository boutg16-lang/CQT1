# -*- coding: utf-8 -*-
"""Direct tests for scripts/one_face.py (single-face 9:16 crop)."""

import os
import sys
from types import SimpleNamespace

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import one_face


def _frame(h=1080, w=1920, value=42):
    return np.full((h, w, 3), value, dtype=np.uint8)


class TestCropAndResizeSingleFace:
    def test_wide_frame_output_shape(self):
        out = one_face.crop_and_resize_single_face(_frame(), (100, 100, 200, 200))
        assert out.shape == (1920, 1080, 3)
        assert out.dtype == np.uint8

    def test_constant_frame_stays_constant(self):
        out = one_face.crop_and_resize_single_face(
            _frame(value=7), (100, 100, 200, 200)
        )
        assert np.all(out == 7)

    def test_tall_frame_output_shape(self):
        # Portrait source: crop is height-limited instead of width-limited.
        out = one_face.crop_and_resize_single_face(
            _frame(h=1920, w=1080), (100, 100, 200, 200)
        )
        assert out.shape == (1920, 1080, 3)

    def test_face_at_right_edge_clamps_inside_frame(self):
        out = one_face.crop_and_resize_single_face(_frame(), (1800, 900, 100, 100))
        assert out.shape == (1920, 1080, 3)

    def test_face_at_origin_clamps_to_zero(self):
        out = one_face.crop_and_resize_single_face(_frame(), (0, 0, 50, 50))
        assert out.shape == (1920, 1080, 3)

    def test_crop_follows_face_center(self):
        # Left half dark, right half bright; face on the left keeps the crop dark.
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        frame[:, 960:] = 200
        out = one_face.crop_and_resize_single_face(frame, (50, 500, 100, 100))
        # Crop width is int(1080 * 9/16) = 607 centred near x=100 -> stays left.
        assert out.mean() < 50


class TestResizeWithPadding:
    def test_wide_frame_output_shape(self):
        assert one_face.resize_with_padding(_frame()).shape == (1920, 1080, 3)

    def test_tall_frame_output_shape(self):
        assert one_face.resize_with_padding(_frame(h=1920, w=1080)).shape == (
            1920,
            1080,
            3,
        )

    def test_square_frame_output_shape(self):
        assert one_face.resize_with_padding(_frame(h=500, w=500)).shape == (
            1920,
            1080,
            3,
        )

    def test_padding_is_black_and_frame_is_centered(self):
        frame = _frame(h=100, w=200, value=9)
        out = one_face.resize_with_padding(frame)
        assert out.shape == (1920, 1080, 3)
        assert out[0, 0].tolist() == [0, 0, 0]  # top-left corner is padding
        assert out[960, 540].tolist() == [9, 9, 9]  # centre is the frame


class TestCropCenterZoom:
    def test_wide_frame_output_shape(self):
        assert one_face.crop_center_zoom(_frame()).shape == (1920, 1080, 3)

    def test_tall_frame_output_shape(self):
        assert one_face.crop_center_zoom(_frame(h=1920, w=1080)).shape == (
            1920,
            1080,
            3,
        )

    def test_constant_frame_stays_constant(self):
        out = one_face.crop_center_zoom(_frame(value=3))
        assert np.all(out == 3)

    def test_center_crop_keeps_middle_strip(self):
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        frame[:, 600:680] = 250  # bright vertical strip in the middle
        out = one_face.crop_center_zoom(frame)
        # The strip survives the centre crop (it spans ~20% of the output).
        assert out[960, 540].tolist() == [250, 250, 250]
        assert out.mean() > 40


def _fake_detection(xmin, ymin, width, height):
    bbox = SimpleNamespace(xmin=xmin, ymin=ymin, width=width, height=height)
    return SimpleNamespace(location_data=SimpleNamespace(relative_bounding_box=bbox))


def _fake_results(*, detections=None, landmarks=None, pose=None):
    face_detection = SimpleNamespace(
        process=lambda _f: SimpleNamespace(detections=detections)
    )
    face_mesh = SimpleNamespace(
        process=lambda _f: SimpleNamespace(multi_face_landmarks=landmarks)
    )
    # Bind `pose` as a default arg: the lambda must capture the parameter,
    # not the SimpleNamespace being assigned to the same name.
    pose_obj = SimpleNamespace(
        process=lambda _f, _p=pose: SimpleNamespace(pose_landmarks=_p)
    )
    return face_detection, face_mesh, pose_obj


class TestDetectFaceOrBody:
    def test_detection_path_uses_first_face(self):
        frame = _frame(h=480, w=640)
        fd, fm, pose = _fake_results(
            detections=[
                _fake_detection(0.1, 0.2, 0.25, 0.3),
                _fake_detection(0.5, 0.5, 0.1, 0.1),
            ]
        )
        result = one_face.detect_face_or_body(frame, fd, fm, pose)
        assert result == [(64, 96, 160, 144)]

    def test_face_mesh_path_from_landmarks(self):
        frame = _frame(h=100, w=200)
        lm = [SimpleNamespace(x=0.1, y=0.2), SimpleNamespace(x=0.4, y=0.6)]
        fd, fm, pose = _fake_results(landmarks=[SimpleNamespace(landmark=lm)])
        result = one_face.detect_face_or_body(frame, fd, fm, pose)
        assert result == [(20, 20, 60, 40)]

    def test_pose_fallback_when_no_face(self):
        frame = _frame(h=100, w=200)
        lm = [SimpleNamespace(x=0.0, y=0.0), SimpleNamespace(x=0.5, y=1.0)]
        fd, fm, pose = _fake_results(pose=SimpleNamespace(landmark=lm))
        result = one_face.detect_face_or_body(frame, fd, fm, pose)
        assert result == [(0, 0, 100, 100)]

    def test_nothing_detected_returns_none(self):
        frame = _frame(h=100, w=200)
        fd, fm, pose = _fake_results(detections=[], landmarks=None, pose=None)
        assert one_face.detect_face_or_body(frame, fd, fm, pose) is None

    def test_detection_and_mesh_both_reported(self):
        frame = _frame(h=100, w=200)
        lm = [SimpleNamespace(x=0.1, y=0.1), SimpleNamespace(x=0.2, y=0.2)]
        fd, fm, pose = _fake_results(
            detections=[_fake_detection(0.0, 0.0, 0.1, 0.1)],
            landmarks=[SimpleNamespace(landmark=lm)],
        )
        result = one_face.detect_face_or_body(frame, fd, fm, pose)
        assert result == [(0, 0, 20, 10), (20, 10, 20, 10)]

    def test_mediapipe_flag_is_bool(self):
        assert isinstance(one_face.MEDIAPIPE_AVAILABLE, bool)
