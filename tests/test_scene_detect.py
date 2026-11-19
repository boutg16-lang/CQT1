# -*- coding: utf-8 -*-
"""Direct tests for scripts/scene_detect.py.

Pure helpers (snap_to_scene, scene_boundary_frames) are tested directly;
the detectors are tested with stubbed engines — no real video, no cv2
frames, no PySceneDetect.
"""
import json

from scripts import scene_detect

SCENES = [(0.0, 2.0), (2.0, 5.5), (5.5, 9.0)]


# ---------------------------------------------------------------------------
# snap_to_scene
# ---------------------------------------------------------------------------

def test_snap_no_scenes_returns_window_unchanged():
    assert scene_detect.snap_to_scene(1.0, 4.0, []) == (1.0, 4.0)
    assert scene_detect.snap_to_scene(1.0, 4.0, None) == (1.0, 4.0)


def test_snap_start_to_nearest_scene_start():
    # start snaps FORWARD to a scene start: window starts at 1.9, the
    # scene at 2.0 opens just after (within tolerance 2.0)
    assert scene_detect.snap_to_scene(1.9, 8.0, SCENES) == (2.0, 8.0)


def test_snap_end_to_nearest_scene_end():
    # end snaps BACKWARD to a scene end: window ends at 5.6, the scene
    # ending at 5.5 closes just before (within tolerance). Start is
    # already on the 0.0 scene boundary so it does not move.
    assert scene_detect.snap_to_scene(0.0, 5.6, SCENES) == (0.0, 5.5)


def test_snap_both_edges():
    assert scene_detect.snap_to_scene(1.9, 5.6, SCENES) == (2.0, 5.5)


def test_snap_start_does_not_snap_backwards_to_past_scene_start():
    # 2.0 is a scene start in the past relative to 2.3; snapping only
    # looks forward, so the window is unchanged.
    assert scene_detect.snap_to_scene(2.3, 8.0, SCENES) == (2.3, 8.0)


def test_snap_outside_tolerance_unchanged():
    # tolerance 0.5: scene starts/ends are all further away
    assert scene_detect.snap_to_scene(3.4, 7.4, SCENES, tolerance=0.5) == (3.4, 7.4)


def test_snap_never_collapses_window_below_one_second():
    # start snaps to 5.5 and end snaps to 5.5 -> collapse -> raw window back
    scenes = [(0.0, 5.5), (5.5, 6.0)]
    assert scene_detect.snap_to_scene(5.4, 5.6, scenes) == (5.4, 5.6)


def test_snap_clamps_negative_start_and_tiny_window():
    assert scene_detect.snap_to_scene(-3.0, 2.0, SCENES) == (0.0, 2.0)
    # end must be at least start + 0.1
    assert scene_detect.snap_to_scene(4.0, 4.0, SCENES) == (4.0, 4.1)


def test_snap_prefers_closest_boundary():
    scenes = [(0.0, 3.0), (3.0, 3.4), (3.4, 9.0)]
    # start 3.2 is closer to 3.4 than to 3.0
    assert scene_detect.snap_to_scene(3.2, 8.0, scenes) == (3.4, 8.0)


# ---------------------------------------------------------------------------
# scene_boundary_frames
# ---------------------------------------------------------------------------

def test_boundary_frames_skips_opening_scene():
    frames = scene_detect.scene_boundary_frames(SCENES, fps=30.0)
    # scene starts at 2.0s -> 60, 5.5s -> 165; 0.0s is not a boundary
    assert frames == {60, 165}


def test_boundary_frames_fps_guard():
    scenes = [(2.0, 4.0)]
    assert scene_detect.scene_boundary_frames(scenes, fps=0) == {60}
    assert scene_detect.scene_boundary_frames(scenes, fps=-5) == {60}
    assert scene_detect.scene_boundary_frames(scenes, fps=300) == {60}
    assert scene_detect.scene_boundary_frames(scenes, fps=None) == {60}


def test_boundary_frames_respects_total_frames():
    scenes = [(2.0, 4.0), (9.0, 10.0)]
    assert scene_detect.scene_boundary_frames(scenes, fps=30.0, total_frames=100) == {60}
    # frame 270 >= total 100 is dropped


def test_boundary_frames_skips_bad_starts():
    scenes = [("bad", 4.0), (None, 5.0), (0.04, 1.0), (2.0, 4.0)]
    assert scene_detect.scene_boundary_frames(scenes, fps=30.0) == {60}


def test_boundary_frames_empty_input():
    assert scene_detect.scene_boundary_frames([], fps=30.0) == set()
    assert scene_detect.scene_boundary_frames(None, fps=30.0) == set()


# ---------------------------------------------------------------------------
# find_scene_cuts — engine selection
# ---------------------------------------------------------------------------

def test_find_cuts_missing_file_returns_empty():
    assert scene_detect.find_scene_cuts("/no/such/video.mp4") == []
    assert scene_detect.find_scene_cuts("") == []


def test_find_cuts_no_engine_returns_empty(tmp_path, monkeypatch):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"fake")
    monkeypatch.setattr(scene_detect, "HAS_SCENEDETECT", False)
    monkeypatch.setattr(scene_detect, "HAS_CV2", False)
    assert scene_detect.find_scene_cuts(str(video)) == []


def test_find_cuts_prefers_pyscenedetect(tmp_path, monkeypatch):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"fake")
    monkeypatch.setattr(scene_detect, "HAS_SCENEDETECT", True)
    monkeypatch.setattr(scene_detect, "_scenes_with_pyscenedetect",
                        lambda path, threshold: [(0.0, 3.0)])
    monkeypatch.setattr(scene_detect, "_scenes_with_opencv",
                        lambda path, threshold: (_ for _ in ()).throw(AssertionError("must not run")))
    assert scene_detect.find_scene_cuts(str(video)) == [(0.0, 3.0)]


def test_find_cuts_pyscenedetect_failure_falls_back_to_opencv(tmp_path, monkeypatch):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"fake")
    monkeypatch.setattr(scene_detect, "HAS_SCENEDETECT", True)
    monkeypatch.setattr(scene_detect, "HAS_CV2", True)

    def boom(path, threshold):
        raise RuntimeError("engine blew up")

    monkeypatch.setattr(scene_detect, "_scenes_with_pyscenedetect", boom)
    monkeypatch.setattr(scene_detect, "_scenes_with_opencv",
                        lambda path, threshold: [(0.0, 2.5)])
    assert scene_detect.find_scene_cuts(str(video)) == [(0.0, 2.5)]


def test_find_cuts_opencv_failure_returns_empty(tmp_path, monkeypatch):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"fake")
    monkeypatch.setattr(scene_detect, "HAS_SCENEDETECT", False)
    monkeypatch.setattr(scene_detect, "HAS_CV2", True)
    monkeypatch.setattr(scene_detect, "_scenes_with_opencv",
                        lambda path, threshold: (_ for _ in ()).throw(RuntimeError("cv2 fail")))
    assert scene_detect.find_scene_cuts(str(video)) == []


# ---------------------------------------------------------------------------
# _scenes_with_opencv — stubbed cv2
# ---------------------------------------------------------------------------

class _FakeCap:
    """Minimal VideoCapture stub: fps, total frames, N readable frames."""

    def __init__(self, fps=30.0, total=120, reads=120, opened=True):
        self._fps = fps
        self._total = total
        self._reads = reads
        self._opened = opened
        self.released = False

    def isOpened(self):
        return self._opened

    def get(self, prop):
        if prop == 5:  # CAP_PROP_FPS
            return self._fps
        if prop == 1:  # CAP_PROP_FRAME_COUNT
            return self._total
        return 0.0

    def read(self):
        if self._reads > 0:
            self._reads -= 1
            return True, "frame"
        return False, None

    def release(self):
        self.released = True


class _FakeCv2:
    CAP_PROP_FPS = 5
    CAP_PROP_FRAME_COUNT = 1
    COLOR_BGR2HSV = 40
    HISTCMP_BHATTACHARYYA = 3

    def __init__(self, diffs, cap=None):
        self._diffs = list(diffs)
        self._cap = cap if cap is not None else _FakeCap()
        self.caps = []

    def VideoCapture(self, path):
        self.caps.append(self._cap)
        return self._cap

    def resize(self, frame, size):
        return "small"

    def cvtColor(self, small, code):
        return "hsv"

    def calcHist(self, images, channels, mask, hist_size, ranges):
        return "hist"

    def normalize(self, hist, dst):
        return None

    def compareHist(self, prev, hist, method):
        return self._diffs.pop(0) if self._diffs else 0.0


def _install_fake_cv2(monkeypatch, diffs, **cap_kwargs):
    fake = _FakeCv2(diffs, cap=_FakeCap(**cap_kwargs))
    monkeypatch.setattr(scene_detect, "cv2", fake)
    return fake


def test_opencv_fallback_detects_scene_cut(monkeypatch):
    # fps 30 -> sample every 15 frames; diffs: low, HIGH at frame 60, low...
    # cut at 60/30 = 2.0s; tail scene 2.0 -> 4.0s (total 120 frames).
    diffs = [0.1, 0.1, 0.1, 0.9, 0.1, 0.1, 0.1]
    _install_fake_cv2(monkeypatch, diffs, fps=30.0, total=120, reads=120)
    scenes = scene_detect._scenes_with_opencv("clip.mp4", 27.0)
    assert scenes == [(0.0, 2.0), (2.0, 4.0)]


def test_opencv_fallback_no_cut_returns_single_scene(monkeypatch):
    _install_fake_cv2(monkeypatch, [0.05] * 8, fps=30.0, total=120, reads=120)
    assert scene_detect._scenes_with_opencv("clip.mp4", 27.0) == [(0.0, 4.0)]


def test_opencv_fallback_short_scenes_are_dropped(monkeypatch):
    # cut at 0.5s (frame 15): scene 0.0-0.5 is < MIN_SCENE_SECONDS, dropped;
    # tail 0.5 -> 1.0s is also < 1.5s, dropped.
    _install_fake_cv2(monkeypatch, [0.9] + [0.05] * 7, fps=30.0, total=30, reads=30)
    assert scene_detect._scenes_with_opencv("clip.mp4", 27.0) == []


def test_opencv_fallback_unopened_capture_returns_empty(monkeypatch):
    fake = _FakeCv2([], cap=_FakeCap(opened=False))
    monkeypatch.setattr(scene_detect, "cv2", fake)
    assert scene_detect._scenes_with_opencv("clip.mp4", 27.0) == []


def test_opencv_fallback_broken_fps_guarded_to_30(monkeypatch):
    _install_fake_cv2(monkeypatch, [0.05] * 8, fps=0.0, total=120, reads=120)
    assert scene_detect._scenes_with_opencv("clip.mp4", 27.0) == [(0.0, 4.0)]
    _install_fake_cv2(monkeypatch, [0.05] * 8, fps=300.0, total=120, reads=120)
    assert scene_detect._scenes_with_opencv("clip.mp4", 27.0) == [(0.0, 4.0)]


def test_opencv_fallback_releases_capture(monkeypatch):
    fake = _install_fake_cv2(monkeypatch, [0.05] * 8, fps=30.0, total=120, reads=120)
    scene_detect._scenes_with_opencv("clip.mp4", 27.0)
    assert fake._cap.released is True


def test_opencv_fallback_threshold_scales(monkeypatch):
    # diff 0.5 beats threshold 27 (0.27) but not threshold 60 (0.60)
    _install_fake_cv2(monkeypatch, [0.1, 0.1, 0.1, 0.5, 0.1, 0.1, 0.1],
                      fps=30.0, total=120, reads=120)
    assert scene_detect._scenes_with_opencv("clip.mp4", 27.0) == [(0.0, 2.0), (2.0, 4.0)]
    _install_fake_cv2(monkeypatch, [0.1, 0.1, 0.1, 0.5, 0.1, 0.1, 0.1],
                      fps=30.0, total=120, reads=120)
    assert scene_detect._scenes_with_opencv("clip.mp4", 60.0) == [(0.0, 4.0)]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def test_main_list_prints_scenes(monkeypatch, capsys):
    monkeypatch.setattr(scene_detect, "find_scene_cuts",
                        lambda path, threshold=27.0: [(1.0, 3.0), (3.0, 5.5)])
    assert scene_detect.main(["clip.mp4", "--list"]) == 0
    out = capsys.readouterr().out
    assert "1.00 --> 3.00" in out
    assert "3.00 --> 5.50" in out


def test_main_json_writes_payload(tmp_path, monkeypatch):
    out_path = tmp_path / "scenes.json"
    monkeypatch.setattr(scene_detect, "find_scene_cuts",
                        lambda path, threshold=27.0: [(0.0, 2.0)])
    assert scene_detect.main(["clip.mp4", "--json", str(out_path)]) == 0
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert payload["source"] == "clip.mp4"
    assert payload["scenes"] == [{"start": 0.0, "end": 2.0}]


def test_main_default_prints_json_array(monkeypatch, capsys):
    monkeypatch.setattr(scene_detect, "find_scene_cuts",
                        lambda path, threshold=27.0: [(0.0, 2.0)])
    assert scene_detect.main(["clip.mp4"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == [{"start": 0.0, "end": 2.0}]


def test_main_threshold_is_forwarded(monkeypatch):
    seen = {}

    def fake_find(path, threshold=27.0):
        seen["threshold"] = threshold
        return []

    monkeypatch.setattr(scene_detect, "find_scene_cuts", fake_find)
    scene_detect.main(["clip.mp4", "--threshold", "15"])
    assert seen["threshold"] == 15.0
