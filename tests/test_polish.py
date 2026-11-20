# -*- coding: utf-8 -*-
"""Direct tests for scripts/polish.py.

Pure helpers (stage classification, subs baseline/retiming) are tested
directly; polish_project is tested with stubbed stage modules and a
stubbed ffprobe — no real media, no network, no ffmpeg.
"""

import hashlib
import json
import subprocess
import sys
from types import SimpleNamespace

import pytest

from scripts import polish

# ---------------------------------------------------------------------------
# _video_files / _subs_json_for
# ---------------------------------------------------------------------------


def test_video_files_missing_dir_returns_empty(tmp_path):
    assert polish._video_files(str(tmp_path / "nope")) == []


def test_video_files_sorted_and_filters_temp_no_audio(tmp_path):
    final = tmp_path / "final"
    final.mkdir()
    (final / "b.mp4").write_bytes(b"x")
    (final / "a.mp4").write_bytes(b"x")
    (final / "temp_video_no_audio.mp4").write_bytes(b"x")
    (final / "notes.txt").write_text("not a video")
    assert polish._video_files(str(final)) == [
        str(final / "a.mp4"),
        str(final / "b.mp4"),
    ]


def test_subs_json_prefers_processed(tmp_path):
    subs = tmp_path / "subs"
    subs.mkdir()
    (subs / "clip.json").write_text("{}", encoding="utf-8")
    (subs / "clip_processed.json").write_text("{}", encoding="utf-8")
    assert polish._subs_json_for("final", "final/clip.mp4", str(subs)) == str(
        subs / "clip_processed.json"
    )


def test_subs_json_falls_back_to_plain_json(tmp_path):
    subs = tmp_path / "subs"
    subs.mkdir()
    (subs / "clip.json").write_text("{}", encoding="utf-8")
    assert polish._subs_json_for("final", "final/clip.mp4", str(subs)) == str(
        subs / "clip.json"
    )


def test_subs_json_none_when_missing(tmp_path):
    assert polish._subs_json_for("final", "final/clip.mp4", str(tmp_path)) is None


# ---------------------------------------------------------------------------
# _stage_outcome / _finalize_stage_lists
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name,data,expected",
    [
        ("jump_cuts", {"ok": True, "cuts": [(1.0, 2.0)]}, "applied"),
        ("jump_cuts", {"ok": True, "cuts": []}, "skipped"),
        ("jump_cuts", {"ok": False, "cuts": []}, "failed"),
        ("jump_cuts", {"ok": True, "cuts": [], "error": "boom"}, "failed"),
        ("punch_zoom", {"ok": True, "punches": 2}, "applied"),
        ("punch_zoom", {"ok": True, "punches": 0}, "skipped"),
        ("background_music", {"ok": True, "music": "song.mp3"}, "applied"),
        (
            "background_music",
            {"ok": True, "music": "song.mp3", "skipped": True},
            "skipped",
        ),
        ("background_music", {"ok": True, "music": None}, "skipped"),
        ("visual_hooks", {"ok": True, "count": 3}, "applied"),
        ("visual_hooks", {"ok": True, "count": 0}, "skipped"),
        ("broll", {"ok": True, "asset": "clip.mp4"}, "applied"),
        ("broll", {"ok": True, "asset": None}, "skipped"),
        ("auto_sfx", {"ok": True, "count": 1}, "applied"),
        ("auto_sfx", {"ok": True, "count": 0}, "skipped"),
        ("branding", {"ok": True, "watermark": True}, "applied"),
        ("branding", {"ok": True, "intro_outro": True}, "applied"),
        ("branding", {"ok": True}, "skipped"),
        ("unknown_stage", {"ok": True}, "applied"),
    ],
)
def test_stage_outcome_table(name, data, expected):
    assert polish._stage_outcome(name, data) == expected


def test_stage_outcome_non_dict_is_failed():
    assert polish._stage_outcome("jump_cuts", None) == "failed"
    assert polish._stage_outcome("jump_cuts", "ok") == "failed"
    assert polish._stage_outcome("jump_cuts", []) == "failed"


def test_finalize_stage_lists_classifies_requested():
    report = {
        "requested_stages": ["jump_cuts", "punch_zoom", "branding", "missing"],
        "stages": {
            "jump_cuts": {"ok": True, "cuts": [(1.0, 2.0)]},
            "punch_zoom": {"ok": True, "punches": 0},
            "branding": {"ok": False, "error": "boom"},
        },
    }
    out = polish._finalize_stage_lists(report)
    assert out["applied_stages"] == ["jump_cuts"]
    assert out["skipped_stages"] == ["punch_zoom"]
    assert out["failed_stages"] == ["branding", "missing"]


# ---------------------------------------------------------------------------
# _sha256_file / _write_json_atomic
# ---------------------------------------------------------------------------


def test_sha256_file_matches_hashlib(tmp_path):
    path = tmp_path / "data.bin"
    path.write_bytes(b"hello world")
    assert polish._sha256_file(str(path)) == hashlib.sha256(b"hello world").hexdigest()


def test_write_json_atomic_writes_valid_json_and_no_temp(tmp_path):
    path = tmp_path / "out.json"
    polish._write_json_atomic(str(path), {"a": [1, 2], "b": "x"})
    assert json.loads(path.read_text(encoding="utf-8")) == {"a": [1, 2], "b": "x"}
    assert [p for p in tmp_path.iterdir() if p.suffix == ".tmp"] == []


# ---------------------------------------------------------------------------
# prepare_subs_for_polish / _record_subs_polish_output
# ---------------------------------------------------------------------------


def _subs_payload(text="hello world"):
    return {
        "segments": [
            {
                "start": 0.0,
                "end": 2.0,
                "text": text,
                "words": [{"word": text.split()[0], "start": 0.0, "end": 1.0}],
            }
        ]
    }


def test_prepare_subs_missing_file_returns_false(tmp_path):
    assert polish.prepare_subs_for_polish(str(tmp_path / "ghost.json")) is False
    assert polish.prepare_subs_for_polish(None) is False


def test_prepare_subs_first_run_creates_baseline_and_state(tmp_path):
    subs = tmp_path / "subs.json"
    subs.write_text(json.dumps(_subs_payload()), encoding="utf-8")
    assert polish.prepare_subs_for_polish(str(subs)) is True
    baseline = tmp_path / "subs.json.pre_polish"
    state = tmp_path / "subs.json.polish_state.json"
    assert baseline.is_file()
    assert json.loads(baseline.read_text(encoding="utf-8")) == _subs_payload()
    state_data = json.loads(state.read_text(encoding="utf-8"))
    assert state_data["version"] == 1
    assert state_data["baseline_sha256"] == state_data["last_output_sha256"]
    assert state_data["baseline_sha256"] == polish._sha256_file(str(subs))


def test_prepare_subs_restores_baseline_after_polish_output(tmp_path):
    subs = tmp_path / "subs.json"
    subs.write_text(json.dumps(_subs_payload("hello world")), encoding="utf-8")
    polish.prepare_subs_for_polish(str(subs))
    # simulate polish retiming the transcript, then record it as output
    subs.write_text(json.dumps(_subs_payload("retimed text")), encoding="utf-8")
    polish._record_subs_polish_output(str(subs))
    # next run: current hash matches the recorded output, so the baseline
    # (original transcript) is restored, not overwritten
    assert polish.prepare_subs_for_polish(str(subs)) is True
    assert json.loads(subs.read_text(encoding="utf-8")) == _subs_payload("hello world")


def test_prepare_subs_external_edit_becomes_new_baseline(tmp_path):
    subs = tmp_path / "subs.json"
    subs.write_text(json.dumps(_subs_payload("v1")), encoding="utf-8")
    polish.prepare_subs_for_polish(str(subs))
    # external edit without recording: hash differs from last output
    subs.write_text(json.dumps(_subs_payload("v2 edited")), encoding="utf-8")
    assert polish.prepare_subs_for_polish(str(subs)) is True
    baseline = tmp_path / "subs.json.pre_polish"
    assert json.loads(baseline.read_text(encoding="utf-8")) == _subs_payload(
        "v2 edited"
    )
    assert json.loads(subs.read_text(encoding="utf-8")) == _subs_payload("v2 edited")


def test_prepare_subs_malformed_state_aborts(tmp_path):
    # NOTE: a malformed state file is NOT tolerated — json.load raises
    # ValueError, caught by the outer handler, and the function returns
    # False without touching the transcript. Verified behaviour.
    subs = tmp_path / "subs.json"
    subs.write_text(json.dumps(_subs_payload()), encoding="utf-8")
    (tmp_path / "subs.json.polish_state.json").write_text("{broken", encoding="utf-8")
    assert polish.prepare_subs_for_polish(str(subs)) is False
    assert not (tmp_path / "subs.json.pre_polish").exists()


def test_prepare_subs_non_dict_state_is_reset(tmp_path):
    subs = tmp_path / "subs.json"
    subs.write_text(json.dumps(_subs_payload()), encoding="utf-8")
    (tmp_path / "subs.json.polish_state.json").write_text(
        json.dumps(["not", "a", "dict"]), encoding="utf-8"
    )
    assert polish.prepare_subs_for_polish(str(subs)) is True


# ---------------------------------------------------------------------------
# retime_subs
# ---------------------------------------------------------------------------


def test_retime_missing_or_malformed_returns_none(tmp_path):
    assert polish.retime_subs(str(tmp_path / "ghost.json"), [(1.0, 2.0)]) is None
    bad = tmp_path / "bad.json"
    bad.write_text("{broken", encoding="utf-8")
    assert polish.retime_subs(str(bad), [(1.0, 2.0)]) is None


def test_retime_shifts_timestamps_through_cuts(tmp_path):
    subs = tmp_path / "subs.json"
    subs.write_text(
        json.dumps(
            {
                "segments": [
                    {
                        "start": 0.0,
                        "end": 6.0,
                        "text": "a b c",
                        "words": [
                            {"word": "a", "start": 0.5, "end": 1.0},
                            {"word": "b", "start": 2.5, "end": 3.0},  # inside the cut
                            {"word": "c", "start": 5.0, "end": 5.5},
                        ],
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    data = polish.retime_subs(str(subs), [(2.0, 4.0)])
    assert data is not None
    seg = data["segments"][0]
    assert seg["start"] == 0.0
    assert seg["end"] == 4.0  # 6.0 - 2.0s removed
    assert [(w["word"], w["start"], w["end"]) for w in seg["words"]] == [
        ("a", 0.5, 1.0),
        ("c", 3.0, 3.5),
    ]  # "b" fell inside the cut


def test_retime_intro_shifts_everything_forward(tmp_path):
    subs = tmp_path / "subs.json"
    subs.write_text(
        json.dumps(
            {
                "segments": [
                    {"start": 1.0, "end": 2.0, "text": "hi", "words": []},
                ]
            }
        ),
        encoding="utf-8",
    )
    data = polish.retime_subs(str(subs), [], intro_duration=1.5)
    assert data["segments"][0]["start"] == 2.5
    assert data["segments"][0]["end"] == 3.5


def test_retime_overlapping_cuts_are_merged(tmp_path):
    subs = tmp_path / "subs.json"
    subs.write_text(
        json.dumps(
            {
                "segments": [
                    {"start": 0.0, "end": 10.0, "text": "x", "words": []},
                ]
            }
        ),
        encoding="utf-8",
    )
    data = polish.retime_subs(str(subs), [(1.0, 3.0), (2.0, 4.0)])
    assert data["segments"][0]["end"] == 7.0  # merged cut removes 3.0s


def test_retime_segment_fully_inside_cut_is_dropped(tmp_path):
    subs = tmp_path / "subs.json"
    subs.write_text(
        json.dumps(
            {
                "segments": [
                    {"start": 2.1, "end": 2.9, "text": "gone", "words": []},
                    {"start": 5.0, "end": 6.0, "text": "kept", "words": []},
                ]
            }
        ),
        encoding="utf-8",
    )
    data = polish.retime_subs(str(subs), [(2.0, 4.0)])
    assert [s["text"] for s in data["segments"]] == ["kept"]
    assert data["segments"][0]["start"] == 3.0  # 5.0 - 2.0 removed


def test_retime_writes_atomically_to_disk(tmp_path):
    subs = tmp_path / "subs.json"
    subs.write_text(
        json.dumps(
            {
                "segments": [
                    {"start": 0.0, "end": 5.0, "text": "x", "words": []},
                ]
            }
        ),
        encoding="utf-8",
    )
    polish.retime_subs(str(subs), [(1.0, 2.0)])
    saved = json.loads(subs.read_text(encoding="utf-8"))
    assert saved["segments"][0]["end"] == 4.0
    assert [p for p in tmp_path.iterdir() if p.suffix == ".tmp"] == []


def test_retime_skips_malformed_segments(tmp_path):
    subs = tmp_path / "subs.json"
    subs.write_text(
        json.dumps(
            {
                "segments": [
                    {"text": "no times"},
                    {"start": "bad", "end": 2.0, "text": "bad"},
                    {
                        "start": 0.0,
                        "end": 1.0,
                        "text": "ok",
                        "words": [
                            {"word": "ok", "start": "bad", "end": 1.0},
                            {"word": "ok2", "start": 0.0, "end": 0.5},
                        ],
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    data = polish.retime_subs(str(subs), [])
    assert len(data["segments"]) == 1
    assert len(data["segments"][0]["words"]) == 1


# ---------------------------------------------------------------------------
# _probe_duration
# ---------------------------------------------------------------------------


def test_probe_duration_success(monkeypatch):
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: SimpleNamespace(stdout="12.5\n")
    )
    assert polish._probe_duration("clip.mp4") == 12.5


def test_probe_duration_failure_returns_zero(monkeypatch):
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(OSError("no ffprobe"))
    )
    assert polish._probe_duration("clip.mp4") == 0.0
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: SimpleNamespace(stdout="not-a-number\n")
    )
    assert polish._probe_duration("clip.mp4") == 0.0


# ---------------------------------------------------------------------------
# polish_project — stubbed stages
# ---------------------------------------------------------------------------


def _make_project(tmp_path, with_subs=True):
    final = tmp_path / "final"
    final.mkdir()
    (final / "clip.mp4").write_bytes(b"video-data")
    if with_subs:
        subs = tmp_path / "subs"
        subs.mkdir()
        (subs / "clip_processed.json").write_text(
            json.dumps(_subs_payload()), encoding="utf-8"
        )
    return tmp_path


def _write_file(path):
    with open(path, "wb") as fh:
        fh.write(b"staged")


def _stub_stages(monkeypatch, jump_ok=True):
    def jump_process(current, subs_json=None, out_path=None, **kwargs):
        _write_file(out_path)
        if jump_ok:
            return {"ok": True, "cuts": [(1.0, 2.0)], "removed": 1.0}
        return {"ok": False, "cuts": [], "removed": 0.0, "error": "jump boom"}

    def punch_process(current, subs_json=None, out_path=None, **kwargs):
        _write_file(out_path)
        return {"ok": True, "punches": [(0.05, 1.05)], "count": 1}

    def music_apply(current, music, out_path, music_volume=0.15, **kwargs):
        _write_file(out_path)
        return {"ok": True, "music": music, "skipped": None}

    def hooks_apply(current, out_path, hooks, accent=None, **kwargs):
        _write_file(out_path)
        return {"ok": True, "count": len(hooks)}

    def sfx_apply(current, out_path, events, sfx_dir=None, volume=0.22, **kwargs):
        _write_file(out_path)
        return {"ok": True, "count": 0, "events": [], "missing_assets": []}

    def branding_process(current, out_path=None, **kwargs):
        _write_file(out_path)
        return {"ok": True, "watermark": True, "intro_outro": False, "degraded": False}

    monkeypatch.setattr(polish.jump_cuts, "process_file", jump_process)
    monkeypatch.setattr(polish.punch_zoom, "process_file", punch_process)
    monkeypatch.setattr(
        polish.background_music, "find_music_file", lambda music_path, folder: None
    )
    monkeypatch.setattr(polish.background_music, "apply_background_music", music_apply)
    monkeypatch.setattr(polish.visual_hooks, "load_words", lambda path: [])
    monkeypatch.setattr(
        polish.visual_hooks, "plan_visual_hooks", lambda words, max_hooks=8: []
    )
    monkeypatch.setattr(polish.visual_hooks, "apply_visual_hooks", hooks_apply)
    monkeypatch.setattr(polish.auto_sfx, "plan_sfx", lambda words: [])
    monkeypatch.setattr(polish.auto_sfx, "apply_auto_sfx", sfx_apply)
    monkeypatch.setattr(polish.branding, "process_file", branding_process)
    monkeypatch.setattr(polish, "_probe_duration", lambda path: 10.0)


def test_polish_project_empty_final_folder(tmp_path):
    reports = polish.polish_project(str(tmp_path), verbose=False)
    assert reports == []
    summary = json.loads((tmp_path / "polish_report.json").read_text(encoding="utf-8"))
    assert summary["summary"]["total"] == 0


def test_polish_project_all_stages_enhanced(tmp_path, monkeypatch):
    project = _make_project(tmp_path)
    _stub_stages(monkeypatch)
    reports = polish.polish_project(str(project), verbose=False)
    assert len(reports) == 1
    report = reports[0]
    assert report["ok"] is True
    assert report["quality_status"] == "enhanced"
    assert report["media_validated"] is True
    assert report["applied_stages"] == ["jump_cuts", "punch_zoom", "branding"]
    assert report["skipped_stages"] == [
        "background_music",
        "visual_hooks",
        "broll",
        "auto_sfx",
    ]
    assert report["failed_stages"] == []
    assert report["retimed_subs"] is True
    assert (project / "final_polished" / "clip.mp4").is_file()
    summary = json.loads((tmp_path / "polish_report.json").read_text(encoding="utf-8"))
    assert summary["summary"]["enhanced"] == 1


def test_polish_project_failed_stage_degrades_to_partial(tmp_path, monkeypatch):
    project = _make_project(tmp_path)
    _stub_stages(monkeypatch, jump_ok=False)
    reports = polish.polish_project(str(project), verbose=False)
    report = reports[0]
    assert report["ok"] is False
    assert report["degraded"] is True
    assert report["quality_status"] == "partial"
    assert "jump_cuts" in report["failed_stages"]
    assert report["output"] is not None
    assert (project / "final_polished" / "clip.mp4").is_file()


def test_polish_project_unreadable_output_uses_fallback(tmp_path, monkeypatch):
    project = _make_project(tmp_path)
    _stub_stages(monkeypatch)
    monkeypatch.setattr(polish, "_probe_duration", lambda path: 0.0)
    reports = polish.polish_project(str(project), verbose=False)
    report = reports[0]
    assert report["fallback_used"] is True
    assert report["quality_status"] == "failed"
    assert (project / "final_polished" / "clip.mp4").is_file()


def test_polish_project_without_subs_still_runs(tmp_path, monkeypatch):
    project = _make_project(tmp_path, with_subs=False)
    _stub_stages(monkeypatch)
    reports = polish.polish_project(str(project), verbose=False)
    assert len(reports) == 1
    assert reports[0]["ok"] is True
    assert "retimed_subs" not in reports[0]


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def test_main_success_exit_code(monkeypatch, capsys):
    monkeypatch.setattr(
        polish, "polish_project", lambda *a, **k: [{"ok": True}, {"ok": True}]
    )
    monkeypatch.setattr(sys, "argv", ["polish", "--project", "proj"])
    assert polish.main() == 0
    assert "2/2 clips ok" in capsys.readouterr().out


def test_main_no_clips_is_failure(monkeypatch, capsys):
    monkeypatch.setattr(polish, "polish_project", lambda *a, **k: [])
    monkeypatch.setattr(sys, "argv", ["polish", "--project", "proj"])
    assert polish.main() == 1
    assert "0/0 clips ok" in capsys.readouterr().out


def test_main_partial_failure_exit_code(monkeypatch):
    monkeypatch.setattr(
        polish, "polish_project", lambda *a, **k: [{"ok": True}, {"ok": False}]
    )
    monkeypatch.setattr(sys, "argv", ["polish", "--project", "proj"])
    assert polish.main() == 1
