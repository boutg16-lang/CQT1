# -*- coding: utf-8 -*-
"""Direct tests for scripts/transcription_validation.py.

Covers: timestamp parsing, SRT/TSV reading, entry validation, the
conservative empty-row repair (SRT/TSV/JSON), and the SRT-preferred /
TSV-fallback validation report. Pure stdlib; no model or network involved.
"""

import json

from scripts import transcription_validation as tv

# ---------------------------------------------------------------------------
# normalize_text / parse_timestamp
# ---------------------------------------------------------------------------

def test_normalize_text_strips_invisible_chars_and_whitespace():
    assert tv.normalize_text("  hello\ufeff world\u200b  ") == "hello world"
    assert tv.normalize_text("a\u200cb\u200dc") == "abc"
    assert tv.normalize_text(None) == ""
    assert tv.normalize_text("") == ""


def test_parse_timestamp_comma_and_dot_separators():
    assert tv.parse_timestamp("00:01:02,500") == 62.5
    assert tv.parse_timestamp("00:01:02.500") == 62.5
    assert tv.parse_timestamp("01:02:03,004") == 3723.004


def test_parse_timestamp_short_millisecond_field_is_right_padded():
    # "5" ms means 500 ms, not 5 ms.
    assert tv.parse_timestamp("00:00:01,5") == 1.5
    assert tv.parse_timestamp("00:00:01,05") == 1.05


def test_parse_timestamp_invalid_inputs_return_none():
    assert tv.parse_timestamp(None) is None
    assert tv.parse_timestamp("") is None
    assert tv.parse_timestamp("not a timestamp") is None
    assert tv.parse_timestamp("12:34") is None


def test_parse_timestamp_finds_timestamp_inside_longer_text():
    assert tv.parse_timestamp("start 00:00:10,000 end") == 10.0


# ---------------------------------------------------------------------------
# read_srt_entries / read_tsv_entries
# ---------------------------------------------------------------------------

def _write(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return str(path)


def test_read_srt_entries_basic_and_crlf(tmp_path):
    path = _write(tmp_path, "a.srt",
                  "1\r\n00:00:01,000 --> 00:00:03,500\r\nHello world\r\n\r\n"
                  "2\r\n00:00:04,000 --> 00:00:06,000\r\nSecond line\r\n")
    entries = tv.read_srt_entries(path)
    assert len(entries) == 2
    assert entries[0] == {"start": 1.0, "end": 3.5, "text": "Hello world"}
    assert entries[1]["start"] == 4.0 and entries[1]["text"] == "Second line"


def test_read_srt_entries_keeps_empty_text_cue_for_validator(tmp_path):
    path = _write(tmp_path, "empty.srt",
                  "1\n00:00:01,000 --> 00:00:02,000\n\n\n"
                  "2\n00:00:03,000 --> 00:00:04,000\nSpoken\n")
    entries = tv.read_srt_entries(path)
    assert len(entries) == 2
    assert entries[0]["text"] == ""
    assert entries[0]["start"] == 1.0


def test_read_srt_entries_missing_or_none_path():
    assert tv.read_srt_entries("/nonexistent/nope.srt") == []
    assert tv.read_srt_entries(None) == []
    assert tv.read_srt_entries("") == []


def test_read_srt_entries_skips_blocks_without_timing(tmp_path):
    path = _write(tmp_path, "odd.srt",
                  "just some text without timing\n\n"
                  "1\n00:00:01,000 --> 00:00:02,000\nReal cue\n")
    entries = tv.read_srt_entries(path)
    assert len(entries) == 1
    assert entries[0]["text"] == "Real cue"


def test_read_tsv_entries_basic_and_bad_rows(tmp_path):
    path = _write(tmp_path, "a.tsv",
                  "start\tend\ttext\n0.0\t1.5\tHello\n"
                  "bad\trow\there\n1.5\t3.0\tWorld\n")
    entries = tv.read_tsv_entries(path)
    assert entries == [
        {"start": 0.0, "end": 1.5, "text": "Hello"},
        {"start": 1.5, "end": 3.0, "text": "World"},
    ]


def test_read_tsv_entries_missing_path():
    assert tv.read_tsv_entries("/nonexistent/nope.tsv") == []
    assert tv.read_tsv_entries(None) == []


# ---------------------------------------------------------------------------
# validate_entries
# ---------------------------------------------------------------------------

def _entry(start, end, text="some words here"):
    return {"start": start, "end": end, "text": text}


def test_validate_entries_ok_case():
    report = tv.validate_entries([_entry(0.0, 1.0), _entry(1.0, 2.5)])
    assert report["ok"] is True
    assert report["count"] == 2
    assert report["duration"] == 2.5
    assert report["errors"] == []


def test_validate_entries_empty_and_min_entries():
    report = tv.validate_entries([])
    assert report["ok"] is False
    assert "no transcription entries found" in report["errors"]
    report = tv.validate_entries([_entry(0.0, 1.0)], min_entries=2)
    assert report["ok"] is False


def test_validate_entries_invalid_timestamps_and_non_positive_duration():
    report = tv.validate_entries([
        {"start": None, "end": 1.0, "text": "x"},
        _entry(2.0, 2.0),
        _entry(3.0, 2.5),
    ])
    assert report["ok"] is False
    assert any("invalid timestamps" in e for e in report["errors"])
    assert sum("non-positive duration" in e for e in report["errors"]) == 2


def test_validate_entries_out_of_order_and_empty_text():
    report = tv.validate_entries([
        _entry(0.0, 5.0),
        _entry(2.0, 3.0),  # starts before the previous entry ended
        _entry(6.0, 7.0, text="   "),
    ])
    assert report["ok"] is False
    assert any("out of order" in e for e in report["errors"])
    assert any("empty text" in e for e in report["errors"])


def test_validate_entries_adjacent_entries_are_not_out_of_order():
    # Touching boundaries (end == next start) are allowed.
    report = tv.validate_entries([_entry(0.0, 1.0), _entry(1.0, 2.0)])
    assert report["ok"] is True


# ---------------------------------------------------------------------------
# _format_timestamp
# ---------------------------------------------------------------------------

def test_format_timestamp_round_trip():
    assert tv._format_timestamp(62.5) == "00:01:02,500"
    assert tv._format_timestamp(0) == "00:00:00,000"
    assert tv._format_timestamp(3723.004) == "01:02:03,004"
    assert tv._format_timestamp(-5) == "00:00:00,000"


# ---------------------------------------------------------------------------
# Repair helpers
# ---------------------------------------------------------------------------

def test_repair_srt_removes_empty_cues_and_renumbers(tmp_path):
    path = _write(tmp_path, "a.srt",
                  "1\n00:00:01,000 --> 00:00:02,000\nHello\n\n"
                  "2\n00:00:03,000 --> 00:00:04,000\n\n\n"
                  "3\n00:00:05,000 --> 00:00:06,000\nWorld\n")
    assert tv._repair_srt(path) == 1
    entries = tv.read_srt_entries(path)
    assert [e["text"] for e in entries] == ["Hello", "World"]
    assert "1\n00:00:01,000 --> 00:00:02,000\nHello" in open(path, encoding="utf-8").read()


def test_repair_srt_no_empty_cues_leaves_file_untouched(tmp_path):
    path = _write(tmp_path, "a.srt", "1\n00:00:01,000 --> 00:00:02,000\nHello\n")
    before = open(path, encoding="utf-8").read()
    assert tv._repair_srt(path) == 0
    assert open(path, encoding="utf-8").read() == before


def test_repair_srt_refuses_rewrite_when_survivor_has_bad_timestamps(tmp_path):
    # The surviving cue has a malformed end timestamp; repair must not
    # rewrite the file and hide that defect from the validator.
    path = _write(tmp_path, "bad.srt",
                  "1\n00:00:01,000 --> xx\nHello\n\n"
                  "2\n00:00:03,000 --> 00:00:04,000\n\n")
    before = open(path, encoding="utf-8").read()
    assert tv._repair_srt(path) == 0
    assert open(path, encoding="utf-8").read() == before


def test_repair_tsv_removes_empty_rows_keeps_header(tmp_path):
    path = _write(tmp_path, "a.tsv",
                  "start\tend\ttext\n0.0\t1.0\tHello\n1.0\t2.0\t\n2.0\t3.0\tWorld\n")
    assert tv._repair_tsv(path) == 1
    rows = open(path, encoding="utf-8").read().splitlines()
    assert rows[0] == "start\tend\ttext"
    assert len(rows) == 3
    assert rows[-1].endswith("World")


def test_repair_tsv_without_text_column_is_noop(tmp_path):
    path = _write(tmp_path, "a.tsv", "start\tend\n0.0\t1.0\n")
    assert tv._repair_tsv(path) == 0


def test_repair_json_removes_empty_segments(tmp_path):
    path = tmp_path / "a.json"
    path.write_text(json.dumps({"segments": [
        {"text": "Hello", "start": 0},
        {"text": "   ", "start": 1},
        {"text": "", "start": 2},
        "not a dict",
    ]}, ensure_ascii=False), encoding="utf-8")
    assert tv._repair_json(str(path)) == 2
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["segments"] == [
        {"text": "Hello", "start": 0},
        "not a dict",
    ]


def test_repair_json_invalid_or_wrong_shape_is_noop(tmp_path):
    bad = _write(tmp_path, "bad.json", "{not json")
    assert tv._repair_json(bad) == 0
    wrong = _write(tmp_path, "wrong.json", json.dumps({"nope": []}))
    assert tv._repair_json(wrong) == 0


def test_repair_transcription_artifacts_combined_counts(tmp_path):
    srt = _write(tmp_path, "a.srt",
                 "1\n00:00:01,000 --> 00:00:02,000\nHello\n\n"
                 "2\n00:00:03,000 --> 00:00:04,000\n\n")
    tsv = _write(tmp_path, "a.tsv", "start\tend\ttext\n0.0\t1.0\tHello\n1.0\t2.0\t\n")
    result = tv.repair_transcription_artifacts(srt_path=srt, tsv_path=tsv,
                                               json_path="/nonexistent/x.json")
    assert result == {"changed": True, "removed": {"srt": 1, "tsv": 1, "json": 0},
                      "total_removed": 2}
    assert tv.repair_transcription_artifacts()["changed"] is False


# ---------------------------------------------------------------------------
# validate_transcription (SRT preferred, TSV fallback)
# ---------------------------------------------------------------------------

def test_validate_transcription_prefers_srt(tmp_path):
    srt = _write(tmp_path, "a.srt", "1\n00:00:01,000 --> 00:00:02,000\nHello\n")
    tsv = _write(tmp_path, "a.tsv", "start\tend\ttext\n0.0\t1.0\tOther\n")
    report = tv.validate_transcription(srt_path=srt, tsv_path=tsv)
    assert report["format"] == "srt"
    assert report["ok"] is True
    assert report["count"] == 1


def test_validate_transcription_tsv_fallback_is_valid(tmp_path):
    tsv = _write(tmp_path, "a.tsv", "start\tend\ttext\n0.0\t1.0\tHello\n")
    report = tv.validate_transcription(srt_path="/nonexistent/a.srt", tsv_path=tsv)
    assert report["format"] == "tsv"
    assert report["ok"] is True
    assert report["errors"] == []


def test_validate_transcription_both_missing_reports_unavailable(tmp_path):
    report = tv.validate_transcription(srt_path="/nonexistent/a.srt",
                                       tsv_path="/nonexistent/a.tsv")
    assert report["ok"] is False
    assert "SRT and TSV unavailable" in report["errors"]
    assert report["count"] == 0


def test_validate_transcription_bad_srt_falls_back_to_tsv(tmp_path):
    # SRT exists but has no usable entries: the TSV fallback is used.
    srt = _write(tmp_path, "a.srt", "no cues here\n")
    tsv = _write(tmp_path, "a.tsv", "start\tend\ttext\n0.0\t1.0\tHello\n")
    report = tv.validate_transcription(srt_path=srt, tsv_path=tsv)
    assert report["format"] == "tsv"
    assert report["ok"] is True
