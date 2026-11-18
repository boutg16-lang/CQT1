# -*- coding: utf-8 -*-
"""Direct tests for scripts/checkpoint.py (crash-safe stage checkpoints).

Covers the load/save round-trip, legacy and corrupt file handling, the
mark_started / mark_done / mark_failed lifecycle, clearing, pending lists,
and the StageTracker context manager. All state lives in tmp_path
directories; no network, no media, no heavy deps. Concurrent-mark behaviour
is covered by tests/test_reliability_hardening.py and is not repeated here.
"""

import json
import os

import pytest

from scripts import checkpoint
from scripts.checkpoint import (
    STAGES,
    StageTracker,
    clear,
    is_done,
    list_pending,
    load_checkpoint,
    mark_done,
    mark_failed,
    mark_started,
    save_checkpoint,
)


def _write_checkpoint(folder, data):
    path = os.path.join(str(folder), checkpoint.CHECKPOINT_FILENAME)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)
    return path


def _read_checkpoint(folder):
    path = os.path.join(str(folder), checkpoint.CHECKPOINT_FILENAME)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# load_checkpoint
# ---------------------------------------------------------------------------


def test_load_missing_file_returns_blank_v2(tmp_path):
    data = load_checkpoint(str(tmp_path))
    assert data["version"] == 2
    assert data["stages"] == {}
    assert data["history"] == []
    assert data["active_stage"] is None
    assert data["last_error"] is None


def test_load_legacy_shape_keeps_stages_and_bumps_version(tmp_path):
    _write_checkpoint(tmp_path, {"stages": {"download": True, "transcribe": True}})
    data = load_checkpoint(str(tmp_path))
    assert data["version"] == 2
    assert data["stages"] == {"download": True, "transcribe": True}
    assert data["history"] == []


def test_load_corrupt_json_returns_blank(tmp_path):
    path = os.path.join(str(tmp_path), checkpoint.CHECKPOINT_FILENAME)
    with open(path, "w", encoding="utf-8") as f:
        f.write("{not json")
    assert load_checkpoint(str(tmp_path))["stages"] == {}


def test_load_non_dict_or_bad_stages_returns_blank(tmp_path):
    _write_checkpoint(tmp_path, ["not", "a", "dict"])
    assert load_checkpoint(str(tmp_path)) == checkpoint._blank_checkpoint()
    _write_checkpoint(tmp_path, {"stages": ["download"]})
    assert load_checkpoint(str(tmp_path))["stages"] == {}


def test_load_corrupt_version_falls_back_to_two(tmp_path):
    _write_checkpoint(tmp_path, {"version": "abc", "stages": {}})
    assert load_checkpoint(str(tmp_path))["version"] == 2
    _write_checkpoint(tmp_path, {"version": 1, "stages": {}})
    assert load_checkpoint(str(tmp_path))["version"] == 2
    _write_checkpoint(tmp_path, {"version": 5, "stages": {}})
    assert load_checkpoint(str(tmp_path))["version"] == 5


def test_load_history_must_be_a_list(tmp_path):
    _write_checkpoint(tmp_path, {"stages": {}, "history": {"not": "a list"}})
    assert load_checkpoint(str(tmp_path))["history"] == []


# ---------------------------------------------------------------------------
# save_checkpoint round-trip
# ---------------------------------------------------------------------------


def test_save_and_load_round_trip(tmp_path):
    save_checkpoint(str(tmp_path), {"download": True}, active_stage="transcribe")
    data = load_checkpoint(str(tmp_path))
    assert data["stages"] == {"download": True}
    assert data["active_stage"] == "transcribe"
    assert data["version"] == 2
    assert data["updated"]  # timestamp is set


def test_save_is_atomic_and_leaves_no_tmp_files(tmp_path):
    save_checkpoint(str(tmp_path), {"download": True})
    leftovers = [name for name in os.listdir(str(tmp_path)) if name.endswith(".tmp")]
    assert leftovers == []
    assert os.path.isfile(os.path.join(str(tmp_path), checkpoint.CHECKPOINT_FILENAME))


def test_save_merges_metadata_without_erasing_history(tmp_path):
    mark_done(str(tmp_path), "download")
    save_checkpoint(str(tmp_path), {"download": True, "transcribe": True})
    data = load_checkpoint(str(tmp_path))
    assert data["stages"]["transcribe"] is True
    assert any(event["stage"] == "download" for event in data["history"])


def test_save_caps_history_at_100_events(tmp_path):
    save_checkpoint(str(tmp_path), {}, history=[{"stage": "cut"}] * 150)
    data = load_checkpoint(str(tmp_path))
    assert len(data["history"]) == 100


# ---------------------------------------------------------------------------
# mark_* lifecycle
# ---------------------------------------------------------------------------


def test_mark_started_then_done_lifecycle(tmp_path):
    mark_started(str(tmp_path), "transcribe")
    assert is_done(str(tmp_path), "transcribe") is False
    data = load_checkpoint(str(tmp_path))
    assert data["active_stage"] == "transcribe"
    assert data["history"][-1]["status"] == "running"
    assert data["history"][-1]["attempt"] == 1

    mark_done(str(tmp_path), "transcribe")
    assert is_done(str(tmp_path), "transcribe") is True
    data = load_checkpoint(str(tmp_path))
    assert data["active_stage"] is None
    assert data["history"][-1]["status"] == "success"


def test_mark_started_twice_counts_attempts(tmp_path):
    mark_started(str(tmp_path), "cut")
    mark_started(str(tmp_path), "cut")
    attempts = [
        event["attempt"]
        for event in load_checkpoint(str(tmp_path))["history"]
        if event["status"] == "running"
    ]
    assert attempts == [1, 2]


def test_mark_failed_records_error_and_clears_stage(tmp_path):
    mark_done(str(tmp_path), "edit")
    mark_failed(str(tmp_path), "edit", RuntimeError("ffmpeg exploded"))
    assert is_done(str(tmp_path), "edit") is False
    data = load_checkpoint(str(tmp_path))
    assert data["last_error"]["type"] == "RuntimeError"
    assert data["last_error"]["message"] == "ffmpeg exploded"
    assert data["last_error"]["stage"] == "edit"
    assert data["history"][-1]["status"] == "failed"


def test_unknown_stage_raises_value_error(tmp_path):
    with pytest.raises(ValueError, match="unknown checkpoint stage"):
        is_done(str(tmp_path), "nope")
    with pytest.raises(ValueError, match="unknown checkpoint stage"):
        mark_done(str(tmp_path), "nope")
    with pytest.raises(ValueError, match="unknown checkpoint stage"):
        mark_started(str(tmp_path), "nope")
    with pytest.raises(ValueError, match="unknown checkpoint stage"):
        mark_failed(str(tmp_path), "nope", RuntimeError("x"))
    with pytest.raises(ValueError, match="unknown checkpoint stage"):
        clear(str(tmp_path), "nope")


# ---------------------------------------------------------------------------
# clear / list_pending
# ---------------------------------------------------------------------------


def test_clear_one_stage(tmp_path):
    mark_done(str(tmp_path), "download")
    mark_done(str(tmp_path), "transcribe")
    clear(str(tmp_path), "download")
    assert is_done(str(tmp_path), "download") is False
    assert is_done(str(tmp_path), "transcribe") is True


def test_clear_all_stages(tmp_path):
    mark_done(str(tmp_path), "download")
    clear(str(tmp_path))
    assert load_checkpoint(str(tmp_path))["stages"] == {}


def test_list_pending_follows_canonical_order(tmp_path):
    mark_done(str(tmp_path), "done")
    mark_done(str(tmp_path), "download")
    pending = list_pending(str(tmp_path))
    assert pending == [stage for stage in STAGES if stage not in ("done", "download")]
    assert pending[0] == "transcribe"


def test_list_pending_all_when_nothing_done(tmp_path):
    assert list_pending(str(tmp_path)) == list(STAGES)


# ---------------------------------------------------------------------------
# StageTracker
# ---------------------------------------------------------------------------


def test_tracker_runs_stage_once_then_skips(tmp_path):
    calls = []

    def work():
        calls.append(1)
        return "ok"

    tracker = StageTracker(str(tmp_path))
    assert tracker.run("cut", work) == "ok"
    assert tracker.run("cut", work) is None  # second run skipped
    assert calls == [1]
    assert tracker.resume_info()["skipped"] == ["cut"]
    assert tracker.resume_info()["completed"] == ["cut"]
    assert tracker.resume_info()["pending"] == [
        stage for stage in STAGES if stage != "cut"
    ]


def test_tracker_force_reruns_completed_stage(tmp_path):
    calls = []

    def work():
        calls.append(1)
        return "ok"

    tracker = StageTracker(str(tmp_path))
    tracker.run("cut", work)
    assert tracker.run("cut", work, force=True) == "ok"
    assert len(calls) == 2


def test_tracker_records_failure_and_reraises(tmp_path):
    def boom():
        raise ValueError("kaboom")

    tracker = StageTracker(str(tmp_path))
    with pytest.raises(ValueError, match="kaboom"):
        tracker.run("edit", boom)
    assert is_done(str(tmp_path), "edit") is False
    data = load_checkpoint(str(tmp_path))
    assert data["last_error"]["type"] == "ValueError"
    assert "kaboom" in data["last_error"]["message"]


def test_tracker_disabled_just_runs_the_function(tmp_path):
    calls = []

    def work():
        calls.append(1)
        return "done"

    tracker = StageTracker(str(tmp_path), enabled=False)
    assert tracker.run("cut", work) == "done"
    assert tracker.run("cut", work) == "done"
    assert len(calls) == 2
    assert load_checkpoint(str(tmp_path))["stages"] == {}


def test_tracker_does_not_swallow_exceptions_on_exit(tmp_path):
    tracker = StageTracker(str(tmp_path))
    with pytest.raises(RuntimeError):
        with tracker:
            raise RuntimeError("propagates")


def test_tracker_resume_info_shape(tmp_path):
    tracker = StageTracker(str(tmp_path))
    tracker.run("download", lambda: None)
    info = tracker.resume_info()
    assert info["skipped"] == []
    assert info["completed"] == ["download"]
    assert info["active_stage"] is None
    assert info["last_error"] is None
    assert isinstance(info["history"], list)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def test_main_status_lists_stages(tmp_path, monkeypatch, capsys):
    import sys

    mark_done(str(tmp_path), "download")
    monkeypatch.setattr(
        sys, "argv", ["checkpoint", "--project", str(tmp_path), "--status"]
    )
    assert checkpoint.main() == 0
    out = capsys.readouterr().out
    assert "download: done" in out
    assert "transcribe: pending" in out


def test_main_mark_and_pending(tmp_path, monkeypatch, capsys):
    import sys

    monkeypatch.setattr(sys, "argv", ["checkpoint", "--project", str(tmp_path), "--mark", "cut"])
    assert checkpoint.main() == 0
    assert is_done(str(tmp_path), "cut") is True
    assert "marked 'cut' done" in capsys.readouterr().out
