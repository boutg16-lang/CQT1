# -*- coding: utf-8 -*-
"""Direct tests for scripts/jump_cuts.py.

Pure planning logic (find_filler_spans, plan_cuts) is tested directly;
ffmpeg interaction is tested with a stubbed subprocess — ffmpeg is never
run for real.
"""
import json
import subprocess
from types import SimpleNamespace

from scripts import jump_cuts


def _words(spec):
    """Build word dicts from (word, start, end) triples."""
    return [{"word": w, "start": s, "end": e} for w, s, e in spec]


# ---------------------------------------------------------------------------
# detect_silences (stubbed subprocess)
# ---------------------------------------------------------------------------

def _fake_run_factory(stdout="", stderr="", side_effect=None):
    def fake_run(cmd, **kwargs):
        if side_effect is not None:
            raise side_effect
        return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=0)
    return fake_run


def test_detect_silences_missing_file_returns_empty():
    assert jump_cuts.detect_silences("/no/such/clip.mp4") == []


def test_detect_silences_parses_ffmpeg_output(tmp_path, monkeypatch):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"fake")
    stderr = (
        "[silencedetect @ 0x1] silence_start: 1.200\n"
        "[silencedetect @ 0x1] silence_end: 2.500 | silence_duration: 1.300\n"
        "[silencedetect @ 0x1] silence_start: 5.000\n"
        "[silencedetect @ 0x1] silence_end: 5.900 | silence_duration: 0.900\n"
    )
    monkeypatch.setattr(subprocess, "run",
                        _fake_run_factory(stderr=stderr))
    assert jump_cuts.detect_silences(str(video)) == [(1.2, 2.5, 1.3), (5.0, 5.9, 0.9)]


def test_detect_silences_trailing_silence_without_end_is_skipped(tmp_path, monkeypatch):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"fake")
    stderr = ("silence_start: 1.0\nsilence_end: 2.0\nsilence_start: 4.0\n")
    monkeypatch.setattr(subprocess, "run", _fake_run_factory(stderr=stderr))
    # the trailing silence has no end and is dropped
    assert jump_cuts.detect_silences(str(video)) == [(1.0, 2.0, 1.0)]


def test_detect_silences_subprocess_failure_returns_empty(tmp_path, monkeypatch):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"fake")
    monkeypatch.setattr(subprocess, "run",
                        _fake_run_factory(side_effect=OSError("ffmpeg missing")))
    assert jump_cuts.detect_silences(str(video)) == []


def test_detect_silences_passes_threshold_and_duration(tmp_path, monkeypatch):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"fake")
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        return SimpleNamespace(stdout="", stderr="", returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    jump_cuts.detect_silences(str(video), threshold_db=-40.0, min_duration=0.7)
    assert "-40.0dB" in seen["cmd"][6]
    assert "d=0.7" in seen["cmd"][6]


# ---------------------------------------------------------------------------
# load_words
# ---------------------------------------------------------------------------

def test_load_words_missing_file_returns_empty():
    assert jump_cuts.load_words("/no/such/subs.json") == []
    assert jump_cuts.load_words(None) == []


def test_load_words_flattens_word_timings(tmp_path):
    payload = {"segments": [
        {"text": "hello world", "start": 0.0, "end": 1.0, "words": [
            {"word": "Hello", "start": 0.0, "end": 0.4},
            {"word": "WORLD", "start": 0.4, "end": 1.0}]},
        {"text": "second", "start": 2.0, "end": 3.0, "words": []},
    ]}
    path = tmp_path / "subs.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    words = jump_cuts.load_words(str(path))
    assert words == [
        {"word": "hello", "start": 0.0, "end": 0.4},
        {"word": "world", "start": 0.4, "end": 1.0},
        {"word": "second", "start": 2.0, "end": 3.0},
    ]


def test_load_words_sorts_by_start_and_skips_bad_rows(tmp_path):
    payload = {"segments": [
        {"text": "later", "start": 5.0, "end": 6.0, "words": []},
        {"text": "bad", "start": "nope", "end": 1.0, "words": []},
        {"text": "early", "start": 1.0, "end": 2.0, "words": []},
    ]}
    path = tmp_path / "subs.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    words = jump_cuts.load_words(str(path))
    assert [w["word"] for w in words] == ["early", "later"]


def test_load_words_malformed_json_returns_empty(tmp_path):
    path = tmp_path / "subs.json"
    path.write_text("{broken", encoding="utf-8")
    assert jump_cuts.load_words(str(path)) == []


# ---------------------------------------------------------------------------
# find_filler_spans
# ---------------------------------------------------------------------------

def test_find_fillers_empty_input():
    assert jump_cuts.find_filler_spans([]) == []
    assert jump_cuts.find_filler_spans(None) == []


def test_find_fillers_single_word():
    words = _words([("hello", 0.0, 0.5), ("um", 0.5, 0.9), ("world", 0.9, 1.4)])
    assert jump_cuts.find_filler_spans(words) == [(0.5, 0.9)]


def test_find_fillers_consecutive_run_merged():
    words = _words([("um", 0.0, 0.3), ("uh", 0.3, 0.6), ("hmm", 0.6, 1.0),
                    ("ok", 1.0, 1.5)])
    assert jump_cuts.find_filler_spans(words) == [(0.0, 1.0)]


def test_find_fillers_punctuation_and_case_insensitive():
    words = _words([("Um,", 0.0, 0.3), ("UH!", 0.3, 0.6), ("go", 0.6, 1.0)])
    assert jump_cuts.find_filler_spans(words) == [(0.0, 0.6)]


def test_find_fillers_close_runs_merged():
    # runs 0.1s apart (< 0.15s) merge into one span
    words = _words([("um", 0.0, 0.3), ("go", 0.3, 0.4), ("uh", 0.4, 0.8)])
    assert jump_cuts.find_filler_spans(words) == [(0.0, 0.8)]


def test_find_fillers_far_runs_stay_separate():
    words = _words([("um", 0.0, 0.3), ("go", 0.3, 1.0), ("uh", 1.0, 1.4)])
    assert jump_cuts.find_filler_spans(words) == [(0.0, 0.3), (1.0, 1.4)]


def test_find_fillers_custom_word_list():
    words = _words([("like", 0.0, 0.4), ("you", 0.4, 0.8), ("know", 0.8, 1.2)])
    assert jump_cuts.find_filler_spans(words, fillers={"like", "know"}) == [
        (0.0, 0.4), (0.8, 1.2)]


def test_find_fillers_trailing_run_is_kept():
    words = _words([("go", 0.0, 0.5), ("um", 0.5, 0.9)])
    assert jump_cuts.find_filler_spans(words) == [(0.5, 0.9)]


# ---------------------------------------------------------------------------
# plan_cuts
# ---------------------------------------------------------------------------

def test_plan_cuts_empty_inputs():
    assert jump_cuts.plan_cuts([], []) == []
    assert jump_cuts.plan_cuts(None, None) == []


def test_plan_cuts_short_silence_dropped():
    assert jump_cuts.plan_cuts([(1.0, 1.3, 0.3)], []) == []  # < MIN_SILENCE 0.40


def test_plan_cuts_silence_at_minimum_kept():
    assert jump_cuts.plan_cuts([(0.0, 0.4, 0.4)], []) == [(0.0, 0.4)]


def test_plan_cuts_float_boundary_silence_is_dropped():
    # NOTE: 1.4 - 1.0 is 0.3999999999999999 in binary floating point, so a
    # nominally 0.40s silence starting at 1.0s falls below MIN_SILENCE and
    # is dropped. Documented here as verified behaviour.
    assert jump_cuts.plan_cuts([(1.0, 1.4, 0.4)], []) == []


def test_plan_cuts_filler_padded_and_clamped_at_zero():
    cuts = jump_cuts.plan_cuts([], [(0.05, 0.5)])
    assert cuts == [(0.0, 0.6)]  # padded by 0.10 each side, start clamped


def test_plan_cuts_short_filler_dropped():
    assert jump_cuts.plan_cuts([], [(1.0, 1.2)]) == []  # 0.2 < MIN_FILLER_SPAN 0.25


def test_plan_cuts_long_cut_clipped_to_max_cut():
    cuts = jump_cuts.plan_cuts([(1.0, 10.0, 9.0)], [])
    assert cuts == [(1.0, 5.0)]  # MAX_CUT 4.0


def test_plan_cuts_close_cuts_merged():
    # gap of 0.2s between cuts is < MIN_KEEP 0.35 -> merged
    cuts = jump_cuts.plan_cuts([(1.0, 2.0, 1.0), (2.2, 3.0, 0.8)], [])
    assert cuts == [(1.0, 3.0)]


def test_plan_cuts_distant_cuts_stay_separate():
    cuts = jump_cuts.plan_cuts([(1.0, 2.0, 1.0), (3.0, 4.0, 1.0)], [])
    assert cuts == [(1.0, 2.0), (3.0, 4.0)]


def test_plan_cuts_clipped_to_duration():
    cuts = jump_cuts.plan_cuts([(8.0, 12.0, 4.0)], [], duration=10.0)
    assert cuts == [(8.0, 10.0)]


def test_plan_cuts_overlapping_silence_and_filler_merged():
    cuts = jump_cuts.plan_cuts([(1.0, 2.0, 1.0)], [(1.8, 2.5)])
    assert cuts == [(1.0, 2.6)]


def test_plan_cuts_sorted_output():
    cuts = jump_cuts.plan_cuts([(5.0, 6.0, 1.0), (1.0, 2.0, 1.0)], [])
    assert cuts == [(1.0, 2.0), (5.0, 6.0)]


# ---------------------------------------------------------------------------
# apply_jump_cuts (stubbed subprocess)
# ---------------------------------------------------------------------------

def test_apply_no_cuts_copies_video_unchanged(tmp_path, monkeypatch):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"data")
    out = tmp_path / "out.mp4"
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("ffmpeg must not run")))
    assert jump_cuts.apply_jump_cuts(str(video), str(out), []) == 0.0
    assert out.read_bytes() == b"data"


def test_apply_missing_video_returns_zero(tmp_path):
    assert jump_cuts.apply_jump_cuts("/no/such.mp4", str(tmp_path / "o.mp4"),
                                     [(1.0, 2.0)]) == 0.0


def test_apply_builds_select_filter_with_audio(tmp_path, monkeypatch):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"data")
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(jump_cuts, "_has_audio_stream", lambda path: True)
    removed = jump_cuts.apply_jump_cuts(str(video), str(tmp_path / "o.mp4"),
                                        [(1.0, 2.0), (4.0, 4.5)])
    assert removed == 1.5
    cmd = seen["cmd"]
    assert cmd[0] == "ffmpeg"
    assert "between(t,1.000,2.000)" in cmd[cmd.index("-filter_complex") + 1]
    assert "-map" in cmd and "[a]" in cmd


def test_apply_without_audio_uses_video_only_filter(tmp_path, monkeypatch):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"data")
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(jump_cuts, "_has_audio_stream", lambda path: False)
    jump_cuts.apply_jump_cuts(str(video), str(tmp_path / "o.mp4"),
                              [(1.0, 2.0)], audio=True)
    fc = seen["cmd"][seen["cmd"].index("-filter_complex") + 1]
    assert "aselect" not in fc
    assert "[a]" not in seen["cmd"]


def test_apply_audio_flag_false_skips_audio(tmp_path, monkeypatch):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"data")
    seen = {}
    monkeypatch.setattr(subprocess, "run",
                        lambda cmd, **k: seen.setdefault("cmd", cmd) or SimpleNamespace(returncode=0))
    monkeypatch.setattr(jump_cuts, "_has_audio_stream", lambda path: True)
    jump_cuts.apply_jump_cuts(str(video), str(tmp_path / "o.mp4"),
                              [(1.0, 2.0)], audio=False)
    assert "[a]" not in seen["cmd"]


# ---------------------------------------------------------------------------
# process_file and main (internals stubbed)
# ---------------------------------------------------------------------------

def test_process_file_happy_path(tmp_path, monkeypatch):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"data")
    monkeypatch.setattr(jump_cuts, "_probe_duration", lambda path: 10.0)
    monkeypatch.setattr(jump_cuts, "detect_silences",
                        lambda path, t, m: [(1.0, 2.0, 1.0)])
    monkeypatch.setattr(jump_cuts, "load_words", lambda path: [])
    monkeypatch.setattr(jump_cuts, "apply_jump_cuts",
                        lambda v, o, cuts: 1.0)
    report = jump_cuts.process_file(str(video))
    assert report["ok"] is True
    assert report["cuts"] == [(1.0, 2.0)]
    assert report["removed"] == 1.0
    assert report["silences"] == 1
    assert report["fillers"] == 0


def test_process_file_failure_reports_error_and_copies(tmp_path, monkeypatch):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"data")
    out = tmp_path / "out.mp4"
    monkeypatch.setattr(jump_cuts, "_probe_duration",
                        lambda path: (_ for _ in ()).throw(RuntimeError("probe boom")))
    report = jump_cuts.process_file(str(video), out_path=str(out))
    assert report["ok"] is False
    assert "probe boom" in report["error"]
    assert out.read_bytes() == b"data"  # fail-safe copy


def test_main_returns_exit_code(monkeypatch, capsys):
    import sys
    monkeypatch.setattr(jump_cuts, "process_file",
                        lambda *a, **k: {"video": "clip.mp4", "ok": True,
                                         "cuts": [], "removed": 0.0})
    monkeypatch.setattr(sys, "argv", ["jump_cuts", "--video", "clip.mp4", "--json"])
    assert jump_cuts.main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True


def test_main_failure_exit_code(monkeypatch, capsys):
    import sys
    monkeypatch.setattr(jump_cuts, "process_file",
                        lambda *a, **k: {"video": "clip.mp4", "ok": False,
                                         "cuts": [], "removed": 0.0})
    monkeypatch.setattr(sys, "argv", ["jump_cuts", "--video", "clip.mp4"])
    assert jump_cuts.main() == 1
