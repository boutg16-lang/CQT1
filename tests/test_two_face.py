# -*- coding: utf-8 -*-
"""Direct tests for scripts/two_face.py (multi-face 9:16 composition)."""

import os
import sys
from types import SimpleNamespace

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import two_face


def _frame(h=480, w=640, value=42):
    return np.full((h, w, 3), value, dtype=np.uint8)


class TestSafeFaceBoxes:
    def test_empty_and_none_input(self):
        assert two_face._safe_face_boxes([], (480, 640)) == []
        assert two_face._safe_face_boxes(None, (480, 640)) == []

    def test_skips_none_and_short_boxes(self):
        boxes = [None, (10, 10, 20), (10, 10, 20, 20)]
        assert two_face._safe_face_boxes(boxes, (480, 640)) == [(10, 10, 20, 20)]

    def test_floats_are_rounded(self):
        assert two_face._safe_face_boxes([(10.6, 10.4, 20.5, 20.5)], (480, 640)) == [
            (11, 10, 20, 20)
        ]  # round-half-even on .5

    def test_out_of_bounds_clamped(self):
        boxes = two_face._safe_face_boxes(
            [(-50, -20, 100, 100), (600, 450, 100, 100)], (480, 640)
        )
        assert boxes[0] == (0, 0, 100, 100)
        # x/y clamp to the last pixel; w/h clamp to the remaining frame.
        assert boxes[1] == (600, 450, 40, 30)

    def test_width_clamped_to_frame_edge(self):
        assert two_face._safe_face_boxes([(600, 10, 100, 50)], (480, 640)) == [
            (600, 10, 40, 50)
        ]

    def test_sorted_left_to_right_by_default(self):
        boxes = two_face._safe_face_boxes(
            [(300, 10, 50, 50), (50, 10, 50, 50)], (480, 640)
        )
        assert boxes == [(50, 10, 50, 50), (300, 10, 50, 50)]

    def test_preserve_order_keeps_caller_order(self):
        boxes = two_face._safe_face_boxes(
            [(300, 10, 50, 50), (50, 10, 50, 50)], (480, 640), preserve_order=True
        )
        assert boxes == [(300, 10, 50, 50), (50, 10, 50, 50)]

    def test_max_faces_caps_result(self):
        boxes = [(i * 60, 10, 50, 50) for i in range(5)]
        assert len(two_face._safe_face_boxes(boxes, (480, 640), max_faces=2)) == 2

    def test_max_faces_at_least_one(self):
        assert (
            len(two_face._safe_face_boxes([(10, 10, 20, 20)], (480, 640), max_faces=0))
            == 1
        )


class TestCropAndMaintainAr:
    def test_output_shape_matches_target(self):
        out = two_face.crop_and_maintain_ar(_frame(), (100, 100, 100, 100), 1080, 960)
        assert out.shape == (960, 1080, 3)

    def test_constant_frame_stays_constant(self):
        out = two_face.crop_and_maintain_ar(
            _frame(value=5), (100, 100, 100, 100), 540, 480
        )
        assert np.all(out == 5)

    def test_face_near_edge_clamps_window_inside_frame(self):
        out = two_face.crop_and_maintain_ar(_frame(), (630, 470, 10, 10), 1080, 960)
        assert out.shape == (960, 1080, 3)

    def test_huge_zoom_out_clamps_to_whole_frame(self):
        out = two_face.crop_and_maintain_ar(
            _frame(), (320, 240, 100, 100), 1080, 960, zoom_out_factor=20.0
        )
        assert out.shape == (960, 1080, 3)

    def test_custom_zoom_factor(self):
        out = two_face.crop_and_maintain_ar(
            _frame(), (100, 100, 100, 100), 270, 240, zoom_out_factor=1.0
        )
        assert out.shape == (240, 270, 3)


class TestCropAndResizeMultiFaces:
    def test_no_faces_returns_black_canvas(self):
        out = two_face.crop_and_resize_multi_faces(_frame(), [])
        assert out.shape == (1920, 1080, 3)
        assert np.all(out == 0)

    def test_single_face_uses_single_crop(self):
        out = two_face.crop_and_resize_multi_faces(
            _frame(value=9), [(100, 100, 100, 100)]
        )
        assert out.shape == (1920, 1080, 3)
        assert np.all(out == 9)

    def test_two_faces_vertical_stack(self):
        out = two_face.crop_and_resize_multi_faces(
            _frame(), [(100, 100, 100, 100), (400, 100, 100, 100)]
        )
        assert out.shape == (1920, 1080, 3)

    def test_two_faces_backward_compat_wrapper(self):
        out = two_face.crop_and_resize_two_faces(
            _frame(), [(100, 100, 100, 100), (400, 100, 100, 100)]
        )
        assert out.shape == (1920, 1080, 3)

    def test_three_faces_use_grid(self):
        boxes = [(100, 100, 80, 80), (300, 100, 80, 80), (500, 100, 80, 80)]
        out = two_face.crop_and_resize_multi_faces(_frame(), boxes)
        assert out.shape == (1920, 1080, 3)

    def test_four_faces_use_grid(self):
        boxes = [(i * 100, 100, 80, 80) for i in range(4)]
        out = two_face.crop_and_resize_multi_faces(_frame(), boxes)
        assert out.shape == (1920, 1080, 3)

    def test_horizontal_layout(self):
        boxes = [(100, 100, 80, 80), (400, 100, 80, 80)]
        out = two_face.crop_and_resize_multi_faces(_frame(), boxes, layout="horizontal")
        assert out.shape == (1920, 1080, 3)

    def test_speaker_layout(self):
        boxes = [(100, 100, 200, 200), (400, 100, 80, 80), (500, 300, 80, 80)]
        out = two_face.crop_and_resize_multi_faces(_frame(), boxes, layout="speaker")
        assert out.shape == (1920, 1080, 3)

    def test_unknown_layout_falls_back_to_grid(self):
        boxes = [(100, 100, 80, 80), (400, 100, 80, 80)]
        out = two_face.crop_and_resize_multi_faces(_frame(), boxes, layout="bogus")
        assert out.shape == (1920, 1080, 3)

    def test_max_faces_limits_composition(self):
        boxes = [(i * 60, 100, 50, 50) for i in range(4)]
        out = two_face.crop_and_resize_multi_faces(_frame(), boxes, max_faces=2)
        assert out.shape == (1920, 1080, 3)

    def test_custom_target_size(self):
        out = two_face.crop_and_resize_multi_faces(
            _frame(), [(100, 100, 100, 100)], target_w=540, target_h=960
        )
        assert out.shape == (960, 540, 3)


def _fake_detection(xmin, ymin, width, height):
    bbox = SimpleNamespace(xmin=xmin, ymin=ymin, width=width, height=height)
    return SimpleNamespace(location_data=SimpleNamespace(relative_bounding_box=bbox))


def _fake_landmarks(points):
    return SimpleNamespace(landmark=[SimpleNamespace(x=x, y=y) for x, y in points])


class TestDetectFaceOrBodyTwoFaces:
    def _run(self, frame, *, detections=None, meshes=None, pose=None, max_faces=2):
        fd = SimpleNamespace(process=lambda _f: SimpleNamespace(detections=detections))
        fm = SimpleNamespace(
            process=lambda _f: SimpleNamespace(multi_face_landmarks=meshes)
        )
        pose_obj = SimpleNamespace(
            process=lambda _f: SimpleNamespace(pose_landmarks=pose)
        )
        return two_face.detect_face_or_body_two_faces(
            frame, fd, fm, pose_obj, max_faces=max_faces
        )

    def test_detection_returns_two_faces(self):
        frame = _frame(h=480, w=640)
        result = self._run(
            frame,
            detections=[
                _fake_detection(0.1, 0.1, 0.2, 0.2),
                _fake_detection(0.6, 0.1, 0.2, 0.2),
            ],
        )
        assert result == [(64, 48, 128, 96), (384, 48, 128, 96)]

    def test_detection_capped_at_max_faces(self):
        frame = _frame(h=480, w=640)
        result = self._run(
            frame,
            detections=[_fake_detection(0.1 * i, 0.1, 0.1, 0.1) for i in range(4)],
            max_faces=2,
        )
        assert len(result) == 2

    def test_mesh_fills_in_when_detection_short(self):
        frame = _frame(h=100, w=200)
        meshes = [
            _fake_landmarks([(0.1, 0.1), (0.2, 0.2)]),
            _fake_landmarks([(0.6, 0.1), (0.7, 0.2)]),
        ]
        result = self._run(
            frame, detections=[_fake_detection(0.0, 0.0, 0.1, 0.1)], meshes=meshes
        )
        assert result == [(20, 10, 20, 10), (120, 10, 20, 10)]

    def test_detection_preferred_when_enough_faces(self):
        frame = _frame(h=100, w=200)
        meshes = [
            _fake_landmarks([(0.1, 0.1), (0.2, 0.2)]),
            _fake_landmarks([(0.6, 0.1), (0.7, 0.2)]),
        ]
        result = self._run(
            frame,
            detections=[
                _fake_detection(0.0, 0.0, 0.1, 0.1),
                _fake_detection(0.5, 0.0, 0.1, 0.1),
            ],
            meshes=meshes,
        )
        assert result == [(0, 0, 20, 10), (100, 0, 20, 10)]

    def test_pose_fallback_when_no_face(self):
        frame = _frame(h=100, w=200)
        pose = SimpleNamespace(
            landmark=[SimpleNamespace(x=0.0, y=0.0), SimpleNamespace(x=0.5, y=1.0)]
        )
        assert self._run(frame, pose=pose) == [(0, 0, 100, 100)]

    def test_nothing_detected_returns_none(self):
        frame = _frame(h=100, w=200)
        assert self._run(frame, detections=[], meshes=None, pose=None) is None

    def test_mediapipe_flag_is_bool(self):
        assert isinstance(two_face.MEDIAPIPE_AVAILABLE, bool)
