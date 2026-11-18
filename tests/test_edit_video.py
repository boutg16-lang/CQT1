# -*- coding: utf-8 -*-
"""Direct tests for scripts/edit_video.py (core pipeline, no media).

Covers only pure/deterministic behaviour: filename/segment index parsing,
crop-box geometry and smoothing, face-count/join hold logic, mouth-ratio
math, speaker ordering, encoder selection, and ffmpeg command construction
for ``finalize_video`` (the command list is asserted — ffmpeg is never run
for real). Heavy media paths (generate_short_*, edit()) are intentionally
not tested here; they need real video + models.
"""

from types import SimpleNamespace

import numpy as np
import pytest

from scripts import edit_video as ev

# --- segment_index_from_filename ----------------------------------------------

@pytest.mark.parametrize("filename,expected", [
    ("3_clip.mp4", 3),
    ("007_segment.mp4", 7),
    ("output012.mp4", 12),
    ("output.mp4", 0),
    ("video.mp4", 0),
    ("/some/dir/12_seg.mp4", 12),
    ("0_first.mp4", 0),
])
def test_segment_index_from_filename(filename, expected):
    assert ev.segment_index_from_filename(filename) == expected


# --- geometry helpers ----------------------------------------------------------

def test_get_center_bbox_and_rect():
    assert ev.get_center_bbox([0, 0, 10, 20]) == (5.0, 10.0)
    assert ev.get_center_rect((10, 20, 30, 40)) == (25.0, 40.0)


def test_smooth_box_first_update_returns_input_box():
    smoother = ev.SmoothBox(alpha=0.5)
    assert smoother.active is False
    assert smoother.update([0, 0, 10, 10]) == [0.0, 0.0, 10.0, 10.0]
    assert smoother.active is True


def test_smooth_box_ema_moves_halfway_at_alpha_half():
    smoother = ev.SmoothBox(alpha=0.5)
    smoother.update([0, 0, 10, 10])  # center (5, 5), size 10x10
    # Next center (15, 5): EMA center (10, 5), size unchanged.
    assert smoother.update([10, 0, 20, 10]) == [5.0, 0.0, 15.0, 10.0]


def test_smooth_box_alpha_one_is_no_smoothing_and_clamped():
    assert ev.SmoothBox(alpha=5.0).alpha == 1.0
    assert ev.SmoothBox(alpha=-1.0).alpha == 0.05
    smoother = ev.SmoothBox(alpha=1.0)
    smoother.update([0, 0, 10, 10])
    assert smoother.update([100, 100, 110, 120]) == [100.0, 100.0, 110.0, 120.0]


def test_smooth_box_reset_clears_state():
    smoother = ev.SmoothBox()
    smoother.update([0, 0, 10, 10])
    smoother.reset()
    assert smoother.active is False
    assert smoother.update([20, 20, 30, 30]) == [20.0, 20.0, 30.0, 30.0]


def test_sort_by_proximity_swaps_when_cheaper():
    old = [[0, 0, 10, 10], [90, 0, 100, 10]]
    new = [[90, 0, 100, 10], [0, 0, 10, 10]]  # swapped vs old
    assert ev.sort_by_proximity(new, old, ev.get_center_bbox) == [
        [0, 0, 10, 10], [90, 0, 100, 10]]


def test_sort_by_proximity_keeps_order_when_cheaper_and_ignores_other_sizes():
    old = [[0, 0, 10, 10], [90, 0, 100, 10]]
    new = [[0, 0, 10, 10], [90, 0, 100, 10]]
    assert ev.sort_by_proximity(new, old, ev.get_center_bbox) == new
    assert ev.sort_by_proximity([old[0]], old, ev.get_center_bbox) == [old[0]]
    assert ev.sort_by_proximity(new, [], ev.get_center_bbox) == new


def test_interpolate_boxes_smoothstep_and_linear():
    assert ev.interpolate_boxes([0, 0, 10, 10], [10, 0, 20, 10], 2) == [
        [5, 0, 15, 10], [10, 0, 20, 10]]
    # Linear easing at 4 steps: t = 0.25 / 0.5 / 0.75 / 1.0.
    assert ev.interpolate_boxes([0, 0, 10, 10], [10, 0, 20, 10], 4,
                                easing="linear")[0] == [2, 0, 12, 10]
    # steps <= 0 clamps to a single frame: the end box.
    assert ev.interpolate_boxes([0, 0, 10, 10], [10, 0, 20, 10], 0) == [
        [10, 0, 20, 10]]


def test_smooth_boxes_per_slot_clamps_and_passes_extra_boxes_through():
    smoothers = [ev.SmoothBox(alpha=1.0), ev.SmoothBox(alpha=1.0)]
    boxes, count = ev.smooth_boxes_per_slot(
        [[-50, -50, 5000, 5000], [0, 0, 10, 10], [1, 1, 2, 2]],
        smoothers, alpha=1.0, prev_count=3, frame_w=1920, frame_h=1080)
    assert count == 3
    assert boxes[0] == [0.0, 0.0, 1920.0, 1080.0]
    assert boxes[2] == [1, 1, 2, 2]  # no smoother left: passed through


def test_smooth_boxes_per_slot_alpha_zero_returns_input():
    boxes, count = ev.smooth_boxes_per_slot(
        [[0, 0, 10, 10]], [ev.SmoothBox()], alpha=0.0, prev_count=1,
        frame_w=1920, frame_h=1080)
    assert (boxes, count) == ([[0, 0, 10, 10]], 1)
    assert ev.smooth_boxes_per_slot([], [], alpha=0.5, prev_count=0,
                                    frame_w=1920, frame_h=1080) == ([], 0)


def test_smooth_boxes_per_slot_resets_on_count_change():
    smoother = ev.SmoothBox(alpha=0.5)
    smoother.update([0, 0, 10, 10])
    assert smoother.active is True
    ev.smooth_boxes_per_slot([[0, 0, 10, 10], [100, 0, 110, 10]],
                             [smoother], alpha=0.5, prev_count=1,
                             frame_w=1920, frame_h=1080)
    assert smoother.active is True  # reset, then fed the first box again


# --- face-count / join hold logic ----------------------------------------------

def test_face_count_hold_rides_through_blips_up_to_grace():
    assert ev.face_count_hold(2, 1, ["a", "b"], 0, 3) == (True, 1)
    assert ev.face_count_hold(2, 1, ["a", "b"], 2, 3) == (True, 3)
    assert ev.face_count_hold(2, 1, ["a", "b"], 3, 3) == (False, 0)


def test_face_count_hold_releases_when_count_recovers_or_zero():
    assert ev.face_count_hold(2, 2, ["a", "b"], 1, 3) == (False, 0)
    assert ev.face_count_hold(2, 0, ["a", "b"], 1, 3) == (False, 1)
    assert ev.face_count_hold(1, 1, ["a"], 0, 3) == (False, 0)


def test_scaled_dead_zone_1080p_keeps_legacy_value_and_4k_doubles():
    assert ev.scaled_dead_zone(40, 1920, 1080) == 40.0
    assert ev.scaled_dead_zone(40, 640, 480) == 40.0  # small frame: factor 1.0
    assert ev.scaled_dead_zone(40, 3840, 2160) == pytest.approx(80.0, abs=0.5)
    assert ev.scaled_dead_zone(0, 1920, 1080) == 0.0
    assert ev.scaled_dead_zone(-5, 1920, 1080) == 0.0


def test_face_join_hold_needs_consecutive_want_split():
    assert ev.face_join_hold(0, True) == (False, 1)
    assert ev.face_join_hold(1, True) == (True, 2)
    assert ev.face_join_hold(5, False) == (False, 0)


# --- mouth ratio -----------------------------------------------------------------

def test_calculate_mouth_ratio_106():
    assert ev.calculate_mouth_ratio_106(None) == 0.0
    assert ev.calculate_mouth_ratio_106(np.zeros((10, 2))) == 0.0
    pts = np.zeros((106, 2))
    pts[52:72, 0] = np.linspace(0, 10, 20)  # mouth width 10
    pts[52:72, 1] = np.linspace(0, 5, 20)   # mouth height 5
    assert ev.calculate_mouth_ratio_106(pts) == pytest.approx(0.5)
    pts[52:72, 0] = 3.0  # zero width
    assert ev.calculate_mouth_ratio_106(pts) == 0.0


def test_calculate_mouth_ratio_68_point():
    assert ev.calculate_mouth_ratio(None) == 0
    pts = np.zeros((68, 3))
    pts[60] = [0, 0, 0]
    pts[64] = [10, 0, 0]   # width 10
    pts[62] = [5, 0, 0]
    pts[66] = [5, 4, 0]    # height 4
    assert ev.calculate_mouth_ratio(pts) == pytest.approx(0.4)
    pts[64] = [0, 0, 0]    # zero width
    assert ev.calculate_mouth_ratio(pts) == 0


# --- speaker ordering / voice-face link -------------------------------------------

def test_order_faces_for_crop_passthrough_without_focus_or_selector():
    faces = [{"bbox": [0, 0, 10, 10]}, {"bbox": [20, 0, 30, 10]}]
    assert ev.order_faces_for_crop(faces, focus_active_speaker=False) == (faces, False)
    assert ev.order_faces_for_crop(faces, focus_active_speaker=True) == (faces, False)
    assert ev.order_faces_for_crop(faces[:1], focus_active_speaker=True,
                                   selector=object()) == (faces[:1], False)


class _StubSelector:
    def __init__(self):
        self.seen = None

    def reorder(self, faces, frame_index=0):
        self.seen = list(faces)
        return list(reversed(faces)), True


def test_order_faces_for_crop_sorts_by_track_id_then_uses_selector():
    selector = _StubSelector()
    faces = [{"_track_id": 2}, {"_track_id": 1}]
    result, switched = ev.order_faces_for_crop(
        faces, focus_active_speaker=True, selector=selector, frame_index=3)
    assert [f["_track_id"] for f in selector.seen] == [1, 2]
    assert switched is True
    assert result == [{"_track_id": 2}, {"_track_id": 1}]


class _StubLink:
    def __init__(self, winner, confidence):
        self.winner = winner
        self.confidence = confidence

    def current_speaker_face(self, seconds, keys):
        return self.winner, self.confidence


def test_apply_voice_face_link_boosts_winner_and_penalises_others():
    faces = [{"_track_id": 0, "activity_score": 5.0},
             {"_track_id": 1, "activity_score": 5.0}]
    ev._apply_voice_face_link(faces, _StubLink(1, 0.8), seconds=10.0)
    assert faces[1]["activity_score"] == pytest.approx(7.0)
    assert faces[0]["activity_score"] == pytest.approx(4.25)


def test_apply_voice_face_link_clamps_and_ignores_weak_evidence():
    faces = [{"_track_id": 0, "activity_score": 19.5},
             {"_track_id": 1, "activity_score": 0.2}]
    ev._apply_voice_face_link(faces, _StubLink(0, 0.9), seconds=1.0)
    assert faces[0]["activity_score"] == 20.0  # capped
    assert faces[1]["activity_score"] == 0.0   # floored
    before = [dict(f) for f in faces]
    ev._apply_voice_face_link(faces, _StubLink(None, 0.0), seconds=1.0)
    ev._apply_voice_face_link(faces, None, seconds=1.0)
    ev._apply_voice_face_link(faces[:1], _StubLink(0, 0.9), seconds=1.0)
    assert faces == before


# --- get_best_encoder (ffmpeg probing, mocked) ------------------------------------

@pytest.fixture(autouse=True)
def _reset_encoder_cache(monkeypatch):
    monkeypatch.setattr(ev, "CACHED_ENCODER", None)
    yield
    monkeypatch.setattr(ev, "CACHED_ENCODER", None)


def _fake_run_factory(stdout, calls):
    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        return SimpleNamespace(returncode=0, stdout=stdout, stderr="")
    return fake_run


def test_get_best_encoder_prefers_nvenc_when_it_works(monkeypatch):
    calls = []
    monkeypatch.setattr(ev.subprocess, "run",
                        _fake_run_factory("encoders:\n h264_nvenc\n h264_amf", calls))
    monkeypatch.setattr("scripts.ffmpeg_toolchain.encoder_works", lambda codec: True)
    assert ev.get_best_encoder() == ("h264_nvenc", "fast")
    assert ev.get_best_encoder() == ("h264_nvenc", "fast")  # cached: no 2nd probe
    assert len(calls) == 1


def test_get_best_encoder_falls_back_to_amf_when_nvenc_fails_probe(monkeypatch):
    monkeypatch.setattr(ev.subprocess, "run",
                        _fake_run_factory("h264_nvenc h264_amf", []))
    monkeypatch.setattr("scripts.ffmpeg_toolchain.encoder_works",
                        lambda codec: codec == "h264_amf")
    assert ev.get_best_encoder() == ("h264_amf", "speed")


def test_get_best_encoder_cpu_fallback_without_ffmpeg(monkeypatch):
    def boom(cmd, **kwargs):
        raise FileNotFoundError("no ffmpeg")
    monkeypatch.setattr(ev.subprocess, "run", boom)
    assert ev.get_best_encoder() == ("libx264", "ultrafast")


# --- finalize_video (ffmpeg command construction, mocked) --------------------------

def _setup_finalize(tmp_path):
    project_folder = tmp_path / "proj"
    cuts = project_folder / "cuts"
    cuts.mkdir(parents=True)
    audio = cuts / "output-audio-0.aac"
    audio.write_bytes(b"fake-audio" * 100)
    return project_folder, tmp_path / "final", audio


def _fake_finalize_run(calls):
    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        if cmd and str(cmd[-1]).endswith(".tmp.mp4"):
            import os
            os.makedirs(os.path.dirname(cmd[-1]), exist_ok=True)
            with open(cmd[-1], "wb") as handle:
                handle.write(b"fake-muxed")
        return SimpleNamespace(returncode=0, stdout="", stderr="")
    return fake_run


def test_finalize_video_builds_mux_command_and_validates(tmp_path, monkeypatch):
    project_folder, final_folder, audio = _setup_finalize(tmp_path)
    final_folder.mkdir()
    calls = []
    monkeypatch.setattr(ev.subprocess, "run", _fake_finalize_run(calls))
    monkeypatch.setattr(ev, "get_best_encoder", lambda: ("libx264", "ultrafast"))
    monkeypatch.setattr(ev, "validate_media_file",
                        lambda path, require_audio=True: {"ok": True})
    output = tmp_path / "clip.mp4"
    output.write_bytes(b"fake-video" * 100)

    assert ev.finalize_video(str(tmp_path / "in.mp4"), str(output), 0,
                             30.0, str(project_folder), str(final_folder)) is True
    assert (final_folder / "final-output000_processed.mp4").is_file()
    assert not audio.exists()  # consumed on success
    extract, mux = calls
    assert extract[:3] == ["ffmpeg", "-y", "-hide_banner"]
    assert "-vn" in extract and "-acodec" in extract
    assert mux[-1].endswith(".tmp.mp4")
    assert "-shortest" in mux and "-c:a" in mux and "192k" in mux
    assert "-c:v" in mux and "libx264" in mux


def test_finalize_video_returns_false_when_audio_extraction_fails(tmp_path, monkeypatch):
    project_folder, final_folder, _audio = _setup_finalize(tmp_path)
    final_folder.mkdir()
    calls = []
    monkeypatch.setattr(ev.subprocess, "run",
                        lambda cmd, **kw: calls.append(list(cmd)) or SimpleNamespace(
                            returncode=1, stdout="", stderr="boom"))
    monkeypatch.setattr(ev, "get_best_encoder", lambda: ("libx264", "ultrafast"))
    assert ev.finalize_video("in.mp4", "clip.mp4", 0, 30.0,
                             str(project_folder), str(final_folder)) is False
    assert len(calls) == 1  # mux never attempted


def test_finalize_video_returns_false_when_audio_file_missing(tmp_path, monkeypatch):
    project_folder, final_folder, audio = _setup_finalize(tmp_path)
    final_folder.mkdir()
    audio.unlink()
    monkeypatch.setattr(ev.subprocess, "run",
                        lambda cmd, **kw: SimpleNamespace(
                            returncode=0, stdout="", stderr=""))
    monkeypatch.setattr(ev, "get_best_encoder", lambda: ("libx264", "ultrafast"))
    assert ev.finalize_video("in.mp4", "clip.mp4", 0, 30.0,
                             str(project_folder), str(final_folder)) is False
