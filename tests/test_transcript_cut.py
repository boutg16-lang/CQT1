# -*- coding: utf-8 -*-
"""Tests for the transcript click-to-cut path (round 2 item 8)."""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from webui import transcript_cut as tc


def _project(tmp_path, segments, words=None):
    project = str(tmp_path / "proj")
    os.makedirs(project, exist_ok=True)
    tsv_lines = ["start\tend\ttext"]
    for seg in segments:
        tsv_lines.append(
            "{}\t{}\t{}".format(
                int(float(seg["start"]) * 1000),
                int(float(seg["end"]) * 1000),
                seg["text"],
            )
        )
    with open(os.path.join(project, "input.tsv"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(tsv_lines) + "\n")
    if words is not None:
        payload = {
            "segments": [
                {"words": [{"word": w["word"], "start": w["start"], "end": w["end"]} for w in words]}
            ]
        }
        with open(os.path.join(project, "input.json"), "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False)
    return project


SEGMENTS = [
    {"start": 0.0, "end": 4.0, "text": "لما رفضوا الدفع انفجر الموقف كله."},
    {"start": 4.0, "end": 9.0, "text": "وقف الجميع وسألني عن السر الحقيقي في القضية."},
]


def test_sentence_rows_use_whisper_timestamps(tmp_path):
    project = _project(tmp_path, SEGMENTS)
    rows = tc.sentence_rows(project)
    assert rows, "TSV transcript must produce sentence rows"
    first = rows[0]
    assert abs(first["start_time"] - 0.0) < 0.5
    assert abs(first["end_time"] - 4.0) < 0.5
    assert "انفجر" in first["text"]
    second = rows[1]
    assert abs(second["start_time"] - 4.0) < 0.5


def test_word_timings_sharpen_bounds(tmp_path):
    words = [
        {"word": "وقف", "start": 4.0, "end": 4.4},
        {"word": "الجميع", "start": 4.4, "end": 5.0},
        {"word": "وسألني", "start": 5.0, "end": 5.6},
        {"word": "عن", "start": 5.6, "end": 5.9},
        {"word": "السر", "start": 5.9, "end": 6.4},
        {"word": "الحقيقي", "start": 6.4, "end": 7.0},
        {"word": "في", "start": 7.0, "end": 7.2},
        {"word": "القضية", "start": 7.2, "end": 8.0},
    ]
    project = _project(tmp_path, SEGMENTS, words=words)
    rows = tc.sentence_rows(project)
    last = rows[-1]
    assert abs(last["start_time"] - 4.0) < 0.3
    assert abs(last["end_time"] - 8.0) < 0.5


def test_no_transcript_reports_honest_empty(tmp_path):
    project = str(tmp_path / "empty")
    os.makedirs(project, exist_ok=True)
    assert tc.sentence_rows(project) == []
    proposal, reasons = tc.append_manual_cut(project, 1.0, 3.0)
    assert proposal is None and reasons


def test_append_manual_cut_validates_and_persists(tmp_path):
    project = _project(tmp_path, SEGMENTS)
    tc._seed_segments(project)
    proposal, reasons = tc.append_manual_cut(project, 0.0, 4.0)
    assert proposal is None, "a 4s window must fail the duration validation"
    proposal, reasons = tc.append_manual_cut(project, 0.0, 8.0, query="")
    assert proposal is not None, reasons
    assert proposal["origin"] == "manual_cut"
    assert proposal["start_time"] == 0.0 and proposal["end_time"] == 8.0
    assert proposal["title"], "a manual cut must carry a title"
    stored = tc._load_payload(project)["segments"]
    assert len(stored) == 1 and stored[0]["origin"] == "manual_cut"
    assert stored[0]["candidate_rank"] == 1


def test_manual_cut_with_query_uses_query_title(tmp_path):
    project = _project(tmp_path, SEGMENTS)
    tc._seed_segments(project)
    proposal, reasons = tc.append_manual_cut(
        project, 0.0, 8.0, query="لماذا رفضوا الدفع"
    )
    assert proposal is not None, reasons
    assert "رفضوا" in proposal["title"] or "الدفع" in proposal["title"]
