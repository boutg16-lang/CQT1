# -*- coding: utf-8 -*-
"""Regression tests for the 8.1 cut -> validate -> publish chain."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import cut_segments, segment_validator, upload_gate
from webui import segments_review


def _segment(title, start, end, **extra):
    payload = {
        "title": title,
        "recommended_title": title,
        "start_time": start,
        "end_time": end,
        "duration": (end - start) if isinstance(start, (int, float)) else 0,
        "transcript_text": "نص المقطع {}".format(title),
    }
    payload.update(extra)
    return payload


def test_cut_keeps_original_index_after_a_fatal_drop(tmp_path, monkeypatch):
    project = tmp_path / "proj"
    project.mkdir()
    (project / "input.mp4").write_bytes(b"stub")
    good = _segment("good", 0, 5)
    bad = _segment("bad", 10, 15)
    tail = _segment("tail", 20, 25)
    payload = {"segments": [good, bad, tail], "source_meta": {"keep": True}}
    (project / "viral_segments.txt").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    calls = []

    def fake_process(index, segment, *args, **kwargs):
        calls.append(index)
        return {"ok": True, "index": index, "output": "x.mp4"}

    monkeypatch.setattr(cut_segments, "_process_segment", fake_process)
    monkeypatch.setattr(cut_segments, "_detect_best_encoder",
                        lambda: ("libx264", "ultrafast", []))
    monkeypatch.setattr(cut_segments, "probe_media", lambda _path: {"ok": False})

    def fake_validate(segment, **kwargs):
        if segment.get("title") == "bad":
            return {"ok": False, "errors": [{"code": "out_of_media_bounds",
                                             "message": "outside"}],
                    "warnings": [], "checks": {}, "duration": 5.0}
        return {"ok": True, "errors": [], "warnings": [], "checks": {},
                "duration": 5.0}

    monkeypatch.setattr(segment_validator, "validate_final_segment", fake_validate)

    cut_segments.cut(payload, str(project), skip_video=True)

    # The refused middle clip must not renumber the ones after it.
    assert calls == [0, 2]

    saved = json.loads((project / "viral_segments.txt").read_text(encoding="utf-8"))
    assert saved["source_meta"] == {"keep": True}
    assert saved["segments"][1]["export_blocked"] is True
    assert saved["segments"][1]["cut_status"] == "refused"


def test_cut_duration_comes_from_the_validated_window():
    segment = _segment("stale", 10, 50)
    segment["duration"] = 0  # stale / hand-edited
    start, duration = cut_segments.segment_window_seconds(segment)
    assert (start, duration) == (10.0, 40.0)


def test_seconds_are_not_reinterpreted_as_milliseconds():
    assert cut_segments._parse_time_value(90) == 90.0
    assert cut_segments._duration_to_seconds(1500) == 1500.0
    start, duration = cut_segments.segment_window_seconds(_segment("long", 300, 1800))
    assert (start, duration) == (300.0, 1500.0)


def test_validator_accepts_clock_strings():
    segment = _segment("clock", "01:30", "01:45")
    report = segment_validator.validate_final_segment(segment, require_title=False)
    assert report["ok"] is True
    assert report["checks"]["start_time_numeric"] is True


def test_under_min_alone_does_not_excuse_a_short_clip():
    segment = _segment("short", 10, 12, under_min=True, transcript_limited=False)
    report = segment_validator.validate_final_segment(segment, min_duration=15,
                                                     require_title=False)
    assert report["ok"] is False
    assert any(item["code"] == "duration_below_min" for item in report["errors"])


def test_review_flags_are_recomputed_and_approvable():
    segment = _segment("fixed", 0, 20, requires_review=True,
                       publish_blocked_reason="manual_review_required")
    payload = {"segments": [segment]}
    annotated = segment_validator.annotate_segment_validation(
        payload, require_title=False)
    assert annotated["segments"][0]["requires_review"] is False

    blocked = _segment("still-bad", 0, 20, export_blocked=True)
    approved = _segment("approved", 0, 20, requires_review=True,
                        export_blocked=True, manual_review_approved=True)
    annotated = segment_validator.annotate_segment_validation(
        {"segments": [blocked, approved]}, require_title=False)
    assert annotated["segments"][1]["requires_review"] is False
    assert annotated["segments"][1]["export_blocked"] is False


def test_blocklist_of_the_wrong_shape_does_not_crash(tmp_path):
    (tmp_path / "publish_blocklist.json").write_text(
        json.dumps({"blocked": {"0": {"index": 0, "axes": [1]}}}),
        encoding="utf-8")
    # Never raises: a dict-shaped "blocked" and a list-shaped "axes" are
    # tolerated, and the entry still produces a block verdict.
    reasons = upload_gate._blocklist_reasons(str(tmp_path), 0)
    assert reasons and reasons[0]["source"] == "publish_blocklist"


def test_review_enforcement_scans_all_segments_when_index_is_none(tmp_path):
    payload = {"segments": [
        _segment("ok", 0, 5),
        _segment("blocked", 10, 15, export_blocked=True),
    ]}
    (tmp_path / "viral_segments.txt").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    reasons = upload_gate._segment_review_reasons(str(tmp_path), None)
    assert reasons and "clip #1" in reasons[0]["detail"]


def test_selection_change_clears_rendered_outputs(tmp_path):
    segments_review._write_payload(str(tmp_path), {"segments": [
        _segment("a", 0, 5), _segment("b", 10, 15)]})
    for folder in ("cuts", "final", "final_polished"):
        path = tmp_path / folder
        path.mkdir()
        (path / "000_x.mp4").write_bytes(b"x")

    rows = segments_review.rows_from_segments(
        segments_review.load_segments(str(tmp_path)))
    rows[1][0] = False
    segments_review.apply_selection(str(tmp_path), rows)

    for folder in ("cuts", "final", "final_polished"):
        assert not (tmp_path / folder).exists(), folder


def test_edited_table_title_is_persisted_or_rejected(tmp_path):
    window = "اختيار مهارة واحدة وإتقانها أفضل من تشتيت الجهد"
    payload = {"segments": [dict(_segment("topic", 0, 10),
                                 recommended_title="اختيار مهارة واحدة وإتقانها",
                                 transcript_text=window)]}
    segments_review._write_payload(str(tmp_path), payload)
    rows = segments_review.rows_from_segments(
        segments_review.load_segments(str(tmp_path)))

    rows[0][1] = "ربحت 5000 دولار"
    changed, errors = segments_review.apply_title_edits(str(tmp_path), rows)
    assert changed == 0 and errors
    assert segments_review.load_segments(str(tmp_path))[0]["recommended_title"] == \
        "اختيار مهارة واحدة وإتقانها"

    rows[0][1] = "إتقان مهارة واحدة"
    changed, errors = segments_review.apply_title_edits(str(tmp_path), rows)
    assert changed == 1 and errors == []
    saved = segments_review.load_segments(str(tmp_path))[0]
    assert saved["title"] == saved["recommended_title"] == "إتقان مهارة واحدة"
