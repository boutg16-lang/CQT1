"""Transcript helpers moved verbatim to scripts/segment_transcript.py
(monolith split, step 6).

These tests pin the split contract: the new module owns the functions, the
old module re-exports the very same objects, and the behaviour through both
paths is identical. They fail on the pre-split tree (no such module) and
pass after the move.
"""

import pytest

import scripts.create_viral_segments as cvs
import scripts.segment_transcript as st

TSV = "start\tend\ttext\n0\t3120\tHello world\n3120\t9840\tSecond line here\n"
SRT = (
    "1\n00:00:00,000 --> 00:00:03,120\nHello world\n\n"
    "2\n00:00:03,120 --> 00:00:09,840\nSecond line here\n"
)


def test_reexports_are_the_same_objects():
    """The old import path must resolve to the new module's functions."""
    assert cvs.load_transcript is st.load_transcript
    assert cvs.preprocess_transcript_for_ai is st.preprocess_transcript_for_ai
    assert cvs._parse_tsv_transcript is st._parse_tsv_transcript
    assert cvs._parse_srt_transcript is st._parse_srt_transcript


def test_parse_tsv_via_new_module(tmp_path):
    tsv = tmp_path / "input.tsv"
    tsv.write_text(TSV, encoding="utf-8")
    segs = st._parse_tsv_transcript(str(tsv))
    assert segs == [
        {"start": 0.0, "end": 3.12, "text": "Hello world"},
        {"start": 3.12, "end": 9.84, "text": "Second line here"},
    ]


def test_parse_srt_via_new_module(tmp_path):
    srt = tmp_path / "input.srt"
    srt.write_text(SRT, encoding="utf-8")
    segs = st._parse_srt_transcript(str(srt))
    assert segs == [
        {"start": 0.0, "end": 3.12, "text": "Hello world"},
        # Trailing space: the SRT regex captures the line's trailing newline,
        # which the parser replaces with a space (pre-existing behaviour).
        {"start": 3.12, "end": 9.84, "text": "Second line here "},
    ]


def test_load_transcript_via_new_module(tmp_path):
    (tmp_path / "input.tsv").write_text(TSV, encoding="utf-8")
    segs = st.load_transcript(str(tmp_path))
    assert len(segs) == 2
    assert segs[0]["text"] == "Hello world"


def test_load_transcript_via_old_path_still_works(tmp_path):
    (tmp_path / "input.srt").write_text(SRT, encoding="utf-8")
    segs = cvs.load_transcript(str(tmp_path))
    assert len(segs) == 2
    assert segs[1]["start"] == 3.12


def test_preprocess_transcript_for_ai_via_new_module():
    segments = [
        {"start": 0.0, "end": 2.0, "text": "Hello"},
        {"start": 2.0, "end": 6.5, "text": "world"},
    ]
    out = st.preprocess_transcript_for_ai(segments)
    # Tags are emitted before the first segment and after any segment whose
    # end is >= 4 s past the previous tag.
    assert out == "(0s) Hello world (6s)"


def test_preprocess_empty_returns_empty_string():
    assert st.preprocess_transcript_for_ai([]) == ""
    assert st.preprocess_transcript_for_ai(None) == ""


def test_parse_tsv_returns_empty_on_missing_file(tmp_path):
    assert st._parse_tsv_transcript(str(tmp_path / "nope.tsv")) == []


def test_parse_srt_returns_empty_on_missing_file(tmp_path):
    assert st._parse_srt_transcript(str(tmp_path / "nope.srt")) == []


def test_load_transcript_raises_when_no_transcript(tmp_path):
    with pytest.raises(ValueError, match="Could not parse transcript"):
        st.load_transcript(str(tmp_path))
