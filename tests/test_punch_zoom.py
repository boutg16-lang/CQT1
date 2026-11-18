# -*- coding: utf-8 -*-
"""Direct tests for scripts/punch_zoom.py.

plan_punches() is pure logic and tested directly; ffmpeg rendering is
tested with a stubbed subprocess — ffmpeg is never run for real.
"""
import json
import subprocess
import sys
from types import SimpleNamespace

from scripts import punch_zoom


def _words(spec):
    return [{"word": w, "start": s, "end": e} for w, s, e in spec]


# ---------------------------------------------------------------------------
# _normalize_keywords
# ---------------------------------------------------------------------------

def test_normalize_keywords_empty():
    assert punch_zoom._normalize_keywords(None) == set()
    assert punch_zoom._normalize_keywords([]) == set()
    assert punch_zoom._normalize_keywords("") == set()


def test_normalize_keywords_string_splits_on_commas_and_spaces():
    assert punch_zoom._normalize_keywords("Money, FREE  win") == {"money", "free", "win"}


def test_normalize_keywords_list_lowercased_and_stripped():
    assert punch_zoom._normalize_keywords(["Wow", " ", "SECRET", ""]) == {"wow", "secret"}


# ---------------------------------------------------------------------------
# plan_punches
# ---------------------------------------------------------------------------

def test_plan_no_words_hook_only():
    # no words at all: only the opening hook punch
    assert punch_zoom.plan_punches([]) == [(0.05, 1.05)]


def test_plan_no_words_no_hook_returns_empty():
    assert punch_zoom.plan_punches([], hook=False) == []


def test_plan_keyword_hit_gets_punch():
    words = _words([("hello", 1.0, 1.4), ("money", 2.0, 2.5), ("bye", 3.0, 3.4)])
    punches = punch_zoom.plan_punches(words, keywords={"money"}, hook=False)
    assert punches == [(2.0, 2.55)]  # punch_duration 0.55


def test_plan_keyword_string_accepted():
    words = _words([("free", 1.0, 1.3)])
    assert punch_zoom.plan_punches(words, keywords="free", hook=False) == [(1.0, 1.55)]


def test_plan_emotional_word_gets_punch():
    words = _words([("hello", 1.0, 1.4), ("wow", 2.0, 2.3)])
    assert punch_zoom.plan_punches(words, hook=False) == [(2.0, 2.55)]


def test_plan_emotional_disabled_ignores_emotional_words():
    words = _words([("wow", 2.0, 2.3)])
    assert punch_zoom.plan_punches(words, emotional=False, hook=False) == []


def test_plan_hook_prepended_when_first_punch_is_late():
    words = _words([("money", 3.0, 3.4)])
    punches = punch_zoom.plan_punches(words, keywords={"money"}, hook=True)
    assert punches[0] == (0.05, 1.05)
    assert punches[1] == (3.0, 3.55)


def test_plan_hook_not_added_when_first_punch_is_early():
    words = _words([("money", 0.1, 0.5)])
    punches = punch_zoom.plan_punches(words, keywords={"money"}, hook=True)
    # first punch starts at 0.1 <= 0.3, so no separate hook punch
    assert punches == [(0.1, 0.65)]


def test_plan_hook_merges_into_nearby_first_punch():
    # first punch at 0.4 starts within start + hook_duration (0.05 + 1.0)
    words = _words([("money", 0.4, 0.8)])
    punches = punch_zoom.plan_punches(words, keywords={"money"}, hook=True)
    assert punches == [(0.05, 1.05)]


def test_plan_close_hits_merged():
    words = _words([("wow", 1.0, 1.3), ("crazy", 1.8, 2.1)])
    # hits 0.5s apart (< 1.2s) merge into one punch window
    punches = punch_zoom.plan_punches(words, hook=False)
    assert punches == [(1.0, 1.55)]


def test_plan_distant_hits_stay_separate():
    words = _words([("wow", 1.0, 1.3), ("crazy", 3.0, 3.3)])
    punches = punch_zoom.plan_punches(words, hook=False)
    assert punches == [(1.0, 1.55), (3.0, 3.55)]


def test_plan_auto_interval_rhythm_when_no_hits():
    words = _words([("hello", 0.0, 0.5), ("world", 9.5, 10.0)])
    punches = punch_zoom.plan_punches(words, hook=False, auto_interval=3.0,
                                      duration=10.0)
    assert punches == [(3.0, 3.55), (6.0, 6.55), (9.0, 9.55)]


def test_plan_auto_interval_not_used_when_hits_exist():
    words = _words([("wow", 1.0, 1.3), ("crazy", 5.0, 5.3)])
    punches = punch_zoom.plan_punches(words, hook=False, auto_interval=2.0,
                                      duration=10.0)
    # more than one punch already -> no auto rhythm
    assert punches == [(1.0, 1.55), (5.0, 5.55)]


def test_plan_punches_clipped_to_duration():
    words = _words([("wow", 9.8, 10.0)])
    punches = punch_zoom.plan_punches(words, hook=False, duration=10.0)
    assert punches == [(9.8, 10.0)]


def test_plan_punches_beyond_duration_dropped():
    words = _words([("wow", 11.0, 11.5)])
    assert punch_zoom.plan_punches(words, hook=False, duration=10.0) == []


def test_plan_custom_punch_duration():
    words = _words([("wow", 2.0, 2.3)])
    punches = punch_zoom.plan_punches(words, hook=False, punch_duration=1.0)
    assert punches == [(2.0, 3.0)]


def test_plan_punctuation_stripped_from_words():
    words = _words([("Wow!", 2.0, 2.3)])
    assert punch_zoom.plan_punches(words, hook=False) == [(2.0, 2.55)]


# ---------------------------------------------------------------------------
# apply_punch_zoom (stubbed subprocess)
# ---------------------------------------------------------------------------

def test_apply_no_punches_copies_video(tmp_path, monkeypatch):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"data")
    out = tmp_path / "out.mp4"
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("ffmpeg must not run")))
    assert punch_zoom.apply_punch_zoom(str(video), str(out), []) == 0
    assert out.read_bytes() == b"data"


def test_apply_missing_video_returns_zero(tmp_path):
    assert punch_zoom.apply_punch_zoom("/no/such.mp4", str(tmp_path / "o.mp4"),
                                       [(1.0, 2.0)]) == 0


def test_apply_invalid_punches_filtered_out(tmp_path, monkeypatch):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"data")
    out = tmp_path / "out.mp4"
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("ffmpeg must not run")))
    # all punches have end <= start -> treated as no punches -> copy
    assert punch_zoom.apply_punch_zoom(str(video), str(out),
                                       [(2.0, 2.0), (3.0, 1.0)]) == 0
    assert out.read_bytes() == b"data"


def test_apply_builds_zoompan_filter(tmp_path, monkeypatch):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"data")
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(punch_zoom, "_probe_size", lambda path: (1080, 1920))
    count = punch_zoom.apply_punch_zoom(str(video), str(tmp_path / "o.mp4"),
                                        [(1.0, 1.55), (3.0, 3.55)])
    assert count == 2
    cmd = seen["cmd"]
    assert cmd[0] == "ffmpeg"
    vf = cmd[cmd.index("-vf") + 1]
    assert "zoompan" in vf
    assert "fps=30" in vf
    assert "s=1080x1920" in vf
    # frame numbers: 1.0s -> on 30, 1.55s -> on 46 (rounded); 3.0s -> 90
    assert "between(on,30,46)" in vf
    assert "between(on,90,106)" in vf
    assert "1.180" in vf  # default zoom factor


def test_apply_odd_dimensions_made_even(tmp_path, monkeypatch):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"data")
    seen = {}
    monkeypatch.setattr(subprocess, "run",
                        lambda cmd, **k: seen.setdefault("cmd", cmd) or SimpleNamespace(returncode=0))
    monkeypatch.setattr(punch_zoom, "_probe_size", lambda path: (1081, 1919))
    punch_zoom.apply_punch_zoom(str(video), str(tmp_path / "o.mp4"), [(1.0, 1.5)])
    vf = seen["cmd"][seen["cmd"].index("-vf") + 1]
    assert "s=1080x1918" in vf


def test_apply_probe_failure_defaults_to_1080x1920(tmp_path, monkeypatch):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"data")
    seen = {}
    monkeypatch.setattr(subprocess, "run",
                        lambda cmd, **k: seen.setdefault("cmd", cmd) or SimpleNamespace(returncode=0))
    monkeypatch.setattr(punch_zoom, "_probe_size", lambda path: None)
    punch_zoom.apply_punch_zoom(str(video), str(tmp_path / "o.mp4"), [(1.0, 1.5)])
    vf = seen["cmd"][seen["cmd"].index("-vf") + 1]
    assert "s=1080x1920" in vf


def test_apply_custom_zoom_factor(tmp_path, monkeypatch):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"data")
    seen = {}
    monkeypatch.setattr(subprocess, "run",
                        lambda cmd, **k: seen.setdefault("cmd", cmd) or SimpleNamespace(returncode=0))
    monkeypatch.setattr(punch_zoom, "_probe_size", lambda path: (1080, 1920))
    punch_zoom.apply_punch_zoom(str(video), str(tmp_path / "o.mp4"),
                                [(1.0, 1.5)], zoom=1.5)
    vf = seen["cmd"][seen["cmd"].index("-vf") + 1]
    assert "1.500" in vf


# ---------------------------------------------------------------------------
# process_file and main (internals stubbed)
# ---------------------------------------------------------------------------

def test_process_file_happy_path(tmp_path, monkeypatch):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"data")
    monkeypatch.setattr(punch_zoom, "load_words",
                        lambda path: _words([("wow", 1.0, 1.3)]))
    monkeypatch.setattr(punch_zoom, "apply_punch_zoom",
                        lambda v, o, punches, zoom=1.18: len(punches))
    report = punch_zoom.process_file(str(video), subs_json="subs.json")
    assert report["ok"] is True
    # hook punch merges into the nearby wow punch (1.0 < 0.05 + 1.0),
    # then the end is clipped to the clip duration (last word ends at 1.3)
    assert report["count"] == 1
    assert report["punches"] == [(0.05, 1.3)]


def test_process_file_no_subs_still_hooks(tmp_path, monkeypatch):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"data")
    monkeypatch.setattr(punch_zoom, "apply_punch_zoom",
                        lambda v, o, punches, zoom=1.18: len(punches))
    report = punch_zoom.process_file(str(video))
    assert report["ok"] is True
    assert report["punches"] == [(0.05, 1.05)]


def test_process_file_failure_reports_error_and_copies(tmp_path, monkeypatch):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"data")
    out = tmp_path / "out.mp4"
    monkeypatch.setattr(punch_zoom, "apply_punch_zoom",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("ffmpeg boom")))
    report = punch_zoom.process_file(str(video), out_path=str(out))
    assert report["ok"] is False
    assert "ffmpeg boom" in report["error"]
    assert out.read_bytes() == b"data"


def test_main_json_output(monkeypatch, capsys):
    monkeypatch.setattr(punch_zoom, "process_file",
                        lambda *a, **k: {"video": "clip.mp4", "ok": True,
                                         "punches": [(0.05, 1.05)], "count": 1})
    monkeypatch.setattr(sys, "argv",
                        ["punch_zoom", "--video", "clip.mp4", "--json"])
    assert punch_zoom.main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["count"] == 1


def test_main_text_output_and_failure_code(monkeypatch, capsys):
    monkeypatch.setattr(punch_zoom, "process_file",
                        lambda *a, **k: {"video": "clip.mp4", "ok": False,
                                         "punches": [], "count": 0,
                                         "error": "boom"})
    monkeypatch.setattr(sys, "argv", ["punch_zoom", "--video", "clip.mp4"])
    assert punch_zoom.main() == 1
    assert "0 punch" in capsys.readouterr().out
