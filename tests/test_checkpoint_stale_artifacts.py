# -*- coding: utf-8 -*-
"""Regression tests: a checkpoint must not trust stale stage outputs.

A stage marked done in checkpoint.json whose expected output artifacts
are missing, empty, or stale (deleted final/ + burned_sub/ + subs_ass/)
must re-run on resume instead of being skipped — otherwise a re-run
exits 0 claiming a successful delivery with no final clip on disk.
All state lives in tmp_path directories; no network, no media, no heavy
deps (stage outputs are dummy non-empty files; content validity is the
delivery audit's job, not the checkpoint's).
"""

import json
import os

from scripts.checkpoint import StageTracker, mark_done, stage_artifacts_valid


def _mark_done(folder, stage):
    mark_done(str(folder), stage)


def _run_tracker(folder, stage):
    """Drive one stage through StageTracker; return True if fn ran."""
    called = []
    tracker = StageTracker(str(folder))

    def _spy():
        called.append(stage)
        return stage

    tracker.run(stage, _spy)
    return bool(called)


def _write(folder, *parts, content=b"fake-media-bytes"):
    path = os.path.join(str(folder), *parts)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(content)
    return path


def _write_report(folder, name, payload):
    path = os.path.join(str(folder), name)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle)
    return path


# ---------------------------------------------------------------------------
# edit — final/*.mp4
# ---------------------------------------------------------------------------


def test_edit_done_but_final_missing_reruns(tmp_path):
    _mark_done(tmp_path, "edit")
    assert stage_artifacts_valid(str(tmp_path), "edit") is False
    assert _run_tracker(tmp_path, "edit") is True


def test_edit_done_with_final_clip_skips(tmp_path):
    _write(tmp_path, "final", "000_Test_clip.mp4")
    _mark_done(tmp_path, "edit")
    assert stage_artifacts_valid(str(tmp_path), "edit") is True
    assert _run_tracker(tmp_path, "edit") is False


def test_edit_done_with_empty_final_clip_reruns(tmp_path):
    _write(tmp_path, "final", "000_Test_clip.mp4", content=b"")
    _mark_done(tmp_path, "edit")
    assert stage_artifacts_valid(str(tmp_path), "edit") is False
    assert _run_tracker(tmp_path, "edit") is True


def test_edit_done_with_only_temp_file_reruns(tmp_path):
    _write(tmp_path, "final", "temp_000_Test_clip.mp4")
    _mark_done(tmp_path, "edit")
    assert stage_artifacts_valid(str(tmp_path), "edit") is False
    assert _run_tracker(tmp_path, "edit") is True


# ---------------------------------------------------------------------------
# subtitles — burned_sub/*.mp4
# ---------------------------------------------------------------------------


def test_subtitles_done_but_burned_sub_missing_reruns(tmp_path):
    _mark_done(tmp_path, "subtitles")
    assert stage_artifacts_valid(str(tmp_path), "subtitles") is False
    assert _run_tracker(tmp_path, "subtitles") is True


def test_subtitles_done_with_burned_clip_skips(tmp_path):
    _write(tmp_path, "burned_sub", "000_Test_clip_subtitled.mp4")
    _mark_done(tmp_path, "subtitles")
    assert stage_artifacts_valid(str(tmp_path), "subtitles") is True
    assert _run_tracker(tmp_path, "subtitles") is False


# ---------------------------------------------------------------------------
# cut — cuts/*.mp4
# ---------------------------------------------------------------------------


def test_cut_done_but_cuts_missing_reruns(tmp_path):
    _mark_done(tmp_path, "cut")
    assert stage_artifacts_valid(str(tmp_path), "cut") is False
    assert _run_tracker(tmp_path, "cut") is True


def test_cut_done_with_cut_clip_skips(tmp_path):
    _write(tmp_path, "cuts", "000_Test_clip_original_scale.mp4")
    _mark_done(tmp_path, "cut")
    assert stage_artifacts_valid(str(tmp_path), "cut") is True
    assert _run_tracker(tmp_path, "cut") is False


# ---------------------------------------------------------------------------
# audio_qc — audio_qc_report.json + the clips it measured
# ---------------------------------------------------------------------------


def test_audio_qc_done_but_report_missing_reruns(tmp_path):
    _mark_done(tmp_path, "audio_qc")
    assert stage_artifacts_valid(str(tmp_path), "audio_qc") is False
    assert _run_tracker(tmp_path, "audio_qc") is True


def test_audio_qc_report_pointing_at_deleted_clip_reruns(tmp_path):
    clip = os.path.join(str(tmp_path), "final", "000_Test_clip.mp4")
    _write_report(
        tmp_path,
        "audio_qc_report.json",
        {"schema": 1, "status": "pass", "clips": [{"path": clip, "status": "pass"}]},
    )
    _mark_done(tmp_path, "audio_qc")
    assert stage_artifacts_valid(str(tmp_path), "audio_qc") is False
    assert _run_tracker(tmp_path, "audio_qc") is True


def test_audio_qc_report_current_with_existing_clip_skips(tmp_path):
    clip = _write(tmp_path, "final", "000_Test_clip.mp4")
    # Clip rendered before the report was written: the report is current.
    os.utime(clip, (1_700_000_000, 1_700_000_000))
    report = _write_report(
        tmp_path,
        "audio_qc_report.json",
        {"schema": 1, "status": "pass", "clips": [{"path": clip, "status": "pass"}]},
    )
    os.utime(report, (1_700_000_100, 1_700_000_100))
    _mark_done(tmp_path, "audio_qc")
    assert stage_artifacts_valid(str(tmp_path), "audio_qc") is True
    assert _run_tracker(tmp_path, "audio_qc") is False


def test_audio_qc_report_stale_when_clip_re_rendered_reruns(tmp_path):
    clip = _write(tmp_path, "final", "000_Test_clip.mp4")
    report = _write_report(
        tmp_path,
        "audio_qc_report.json",
        {"schema": 1, "status": "pass", "clips": [{"path": clip, "status": "pass"}]},
    )
    # The clip was re-rendered after the report: the report is stale.
    os.utime(report, (1_700_000_000, 1_700_000_000))
    os.utime(clip, (1_700_000_100, 1_700_000_100))
    _mark_done(tmp_path, "audio_qc")
    assert stage_artifacts_valid(str(tmp_path), "audio_qc") is False
    assert _run_tracker(tmp_path, "audio_qc") is True


def test_audio_qc_corrupt_report_reruns(tmp_path):
    _write(tmp_path, "audio_qc_report.json", content=b"{not json")
    _mark_done(tmp_path, "audio_qc")
    assert stage_artifacts_valid(str(tmp_path), "audio_qc") is False
    assert _run_tracker(tmp_path, "audio_qc") is True


# ---------------------------------------------------------------------------
# scorecard / music_check — report JSON must exist and parse
# ---------------------------------------------------------------------------


def test_scorecard_done_but_report_missing_reruns(tmp_path):
    _mark_done(tmp_path, "scorecard")
    assert stage_artifacts_valid(str(tmp_path), "scorecard") is False
    assert _run_tracker(tmp_path, "scorecard") is True


def test_scorecard_done_with_valid_report_skips(tmp_path):
    _write_report(
        tmp_path, "risk_scorecard.json", {"segments": [], "blocked": [], "summary": {}}
    )
    _mark_done(tmp_path, "scorecard")
    assert stage_artifacts_valid(str(tmp_path), "scorecard") is True
    assert _run_tracker(tmp_path, "scorecard") is False


def test_music_check_done_but_report_missing_reruns(tmp_path):
    _mark_done(tmp_path, "music_check")
    assert stage_artifacts_valid(str(tmp_path), "music_check") is False
    assert _run_tracker(tmp_path, "music_check") is True


def test_music_check_done_with_valid_report_skips(tmp_path):
    _write_report(tmp_path, "music_fingerprint.json", {"clips": [], "summary": {}})
    _mark_done(tmp_path, "music_check")
    assert stage_artifacts_valid(str(tmp_path), "music_check") is True
    assert _run_tracker(tmp_path, "music_check") is False


# ---------------------------------------------------------------------------
# Boundary: stages with no artifact rule keep marker-only behaviour.
# ---------------------------------------------------------------------------


def test_transcribe_done_without_artifacts_still_skips(tmp_path):
    # Transcribe is validated separately by main_improved (transcript
    # repair/validation); the tracker must not second-guess it.
    _mark_done(tmp_path, "transcribe")
    assert stage_artifacts_valid(str(tmp_path), "transcribe") is True
    assert _run_tracker(tmp_path, "transcribe") is False


def test_download_done_without_artifacts_still_skips(tmp_path):
    _mark_done(tmp_path, "download")
    assert stage_artifacts_valid(str(tmp_path), "download") is True
    assert _run_tracker(tmp_path, "download") is False


def test_stage_completed_by_same_tracker_skips_without_artifacts(tmp_path):
    # A stage that just ran in this process is trusted: only markers
    # loaded from a previous run are artifact-validated on resume.
    calls = []
    tracker = StageTracker(str(tmp_path))
    tracker.run("cut", lambda: calls.append(1))
    assert tracker.run("cut", lambda: calls.append(2)) is None
    assert calls == [1]
