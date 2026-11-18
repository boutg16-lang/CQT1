# -*- coding: utf-8 -*-
"""Tests for scripts/segment_titles.py (monolith split, step 3).

The title-relevance helpers moved verbatim from
``scripts/create_viral_segments.py``: window text extraction, content
relevance, title quality scoring and recommended-title selection, plus the
``_normalized_match_text`` dependency they are layered on. Tests import from
the new module path and assert the old import path still resolves to the
same objects.
"""


from scripts import create_viral_segments as cvs
from scripts import segment_titles as st
from scripts.segment_titles import (
    _ARABIC_ORTHOGRAPHY_TABLE,
    _choose_recommended_title,
    _normalize_arabic_orthography,
    _normalized_match_text,
    _title_content_relevance,
    _title_quality_score,
    _window_text_from_transcript,
)

# ---------------------------------------------------------------------------
# Re-export identity
# ---------------------------------------------------------------------------

def test_reexports_are_the_same_objects():
    for name in (
        "_ARABIC_ORTHOGRAPHY_TABLE",
        "_normalize_arabic_orthography",
        "_normalized_match_text",
        "_window_text_from_transcript",
        "_title_content_relevance",
        "_title_quality_score",
        "_choose_recommended_title",
    ):
        assert getattr(cvs, name) is getattr(st, name)


# ---------------------------------------------------------------------------
# _normalized_match_text / _normalize_arabic_orthography
# ---------------------------------------------------------------------------

def test_normalized_match_text_unifies_arabic_variants():
    assert _normalized_match_text("الأمور") == _normalized_match_text("الامور")
    assert _normalized_match_text("مؤمن") == _normalized_match_text("مومن")
    assert _normalized_match_text("مدرسة") == _normalized_match_text("مدرسه")
    assert _normalized_match_text("بئر") == _normalized_match_text("بير")


def test_normalized_match_text_keeps_distinct_words_distinct():
    assert _normalized_match_text("على") != _normalized_match_text("علي")
    assert _normalized_match_text("مشى") != _normalized_match_text("مشي")


def test_normalized_match_text_keeps_english_byte_identical():
    assert _normalized_match_text("Hello, World!") == "hello world"
    assert _normalized_match_text("Don't stop! 12.5% done") == "dont stop 125 done"


def test_normalize_arabic_orthography_is_pure_and_safe():
    assert _normalize_arabic_orthography("") == ""
    assert _normalize_arabic_orthography(None) == ""
    assert _normalize_arabic_orthography(123) == "123"
    assert _normalize_arabic_orthography("hello") == "hello"


def test_normalize_arabic_orthography_fallback_table_without_arabic_text(monkeypatch):
    """The carried fallback table still unifies when the optional import is absent."""
    monkeypatch.setattr(st, "_arabic_text", None)
    assert _normalize_arabic_orthography("الأمور") == "الامور"
    assert _normalize_arabic_orthography("مدرسة") == "مدرسه"
    assert _normalize_arabic_orthography("على") != _normalize_arabic_orthography("علي")


def test_normalized_match_text_fallback_without_arabic_text(monkeypatch):
    monkeypatch.setattr(st, "_arabic_text", None)
    assert _normalized_match_text("الأمور") == _normalized_match_text("الامور")
    assert _normalized_match_text("Hello, World!") == "hello world"


def test_arabic_orthography_table_shape():
    assert _ARABIC_ORTHOGRAPHY_TABLE[0x0623] == 0x0627  # أ -> ا
    assert _ARABIC_ORTHOGRAPHY_TABLE[0x0640] is None  # tatweel deleted
    assert _ARABIC_ORTHOGRAPHY_TABLE[0x0629] == 0x0647  # ة -> ه


# ---------------------------------------------------------------------------
# _window_text_from_transcript
# ---------------------------------------------------------------------------

def _three_line_transcript():
    return [
        {"start": 0.0, "end": 5.0, "text": "First line."},
        {"start": 5.0, "end": 10.0, "text": "Second line."},
        {"start": 10.0, "end": 15.0, "text": "Third line!"},
    ]


def test_window_text_joins_overlapping_lines():
    transcript = _three_line_transcript()
    # [4, 11) overlaps all three lines.
    assert _window_text_from_transcript(transcript, 4.0, 11.0) == (
        "first line second line third line")


def test_window_text_respects_half_open_bounds():
    transcript = _three_line_transcript()
    assert _window_text_from_transcript(transcript, 0.0, 5.0) == "first line"
    assert _window_text_from_transcript(transcript, 5.0, 10.0) == "second line"
    assert _window_text_from_transcript(transcript, 15.0, 20.0) == ""


def test_window_text_empty_and_unparseable():
    assert _window_text_from_transcript([], 0.0, 5.0) == ""
    assert _window_text_from_transcript(_three_line_transcript(), None, 5.0) == ""
    assert _window_text_from_transcript(_three_line_transcript(), 0.0, "soon") == ""


def test_window_text_prefers_word_level_matches():
    transcript = _three_line_transcript()
    words = [
        {"start": 1.0, "end": 2.0, "word": "hello"},
        {"start": 2.0, "end": 3.0, "word": "world"},
        {"start": 9.0, "end": 10.0, "word": "far"},
    ]
    assert _window_text_from_transcript(transcript, 0.0, 5.0, words=words) == (
        "hello world")


def test_window_text_skips_malformed_words_and_falls_back_to_lines():
    transcript = _three_line_transcript()
    words = [
        "not a dict",
        {"start": "bad", "end": 1.0, "word": "nope"},
        {"start": 1.0, "end": 2.0, "text": ""},
    ]
    # No usable word overlaps [0, 5), so the line text is used instead.
    assert _window_text_from_transcript(transcript, 0.0, 5.0, words=words) == (
        "first line")


# ---------------------------------------------------------------------------
# _title_content_relevance
# ---------------------------------------------------------------------------

_HALLUCINATION_TRANSCRIPT = [
    {"start": 0.0, "end": 5.0, "text": "the market opened quietly"},
    {"start": 5.0, "end": 10.0, "text": "then patience became the secret"},
    {"start": 10.0, "end": 15.0, "text": "nobody expected the crash"},
]

_HALLUCINATION_SEGMENT = {
    "title": "shortcut",
    "start_text": "shortcut pays",
    "end_text": "",
    "caption": "",
}


def test_title_relevance_empty_title_is_zero():
    assert _title_content_relevance("", _HALLUCINATION_SEGMENT) == 0.0
    assert _title_content_relevance("   ", _HALLUCINATION_SEGMENT) == 0.0


def test_title_relevance_legacy_path_without_window():
    assert _title_content_relevance("shortcut", _HALLUCINATION_SEGMENT) == 1.0
    assert _title_content_relevance("patience", _HALLUCINATION_SEGMENT) == 0.0
    assert _title_content_relevance("shortcut", _HALLUCINATION_SEGMENT, None) == 1.0
    assert _title_content_relevance("shortcut", _HALLUCINATION_SEGMENT, "") == 1.0


def test_title_relevance_prefers_real_window_words():
    window = _window_text_from_transcript(_HALLUCINATION_TRANSCRIPT, 2.0, 12.0)
    assert _title_content_relevance("shortcut", _HALLUCINATION_SEGMENT, window) == 0.5
    assert _title_content_relevance("patience", _HALLUCINATION_SEGMENT, window) == 1.0


def test_title_relevance_llm_only_words_get_half_weight():
    window = _window_text_from_transcript(_HALLUCINATION_TRANSCRIPT, 2.0, 12.0)
    assert _title_content_relevance(
        "shortcut pays", _HALLUCINATION_SEGMENT, window) == 0.5


def test_title_relevance_window_score_is_capped_at_one():
    window = _window_text_from_transcript(_HALLUCINATION_TRANSCRIPT, 2.0, 12.0)
    assert _title_content_relevance(
        "patience crash", _HALLUCINATION_SEGMENT, window) == 1.0
    assert _title_content_relevance(
        "notspoken", _HALLUCINATION_SEGMENT, window) == 0.0


def test_title_relevance_no_content_is_zero():
    assert _title_content_relevance("anything", {}) == 0.0
    assert _title_content_relevance("anything", {}, "some window words") == 0.0


# ---------------------------------------------------------------------------
# _title_quality_score
# ---------------------------------------------------------------------------

def test_title_quality_empty_is_zero():
    assert _title_quality_score("") == 0.0
    assert _title_quality_score(None) == 0.0
    assert _title_quality_score("   ") == 0.0


def test_title_quality_ideal_length_and_question_bonus():
    # 38 chars (in 18..72), ends with ؟, 6 unique words -> 55 + 20 + 5 + 3.
    assert _title_quality_score("كيف تطيح حرب الكوكايين بصناع المحتوى؟") == 83.0


def test_title_quality_short_title_is_neutral():
    assert _title_quality_score("Hi") == 55.0


def test_title_quality_long_title_is_penalised():
    assert _title_quality_score("x" * 120) == 40.0


def test_title_quality_all_caps_is_penalised():
    assert _title_quality_score("HELLO WORLD THIS IS A TEST TITLE HERE") == 55.0


def test_title_quality_word_salad_is_penalised():
    stuffed = "حرب حرب الكوكايين تطيح تطيح بصناع المحتوى"
    clean = "كيف تطيح حرب الكوكايين بصناع المحتوى؟"
    assert _title_quality_score(stuffed) == 65.0
    assert _title_quality_score(stuffed) < _title_quality_score(clean)


def test_title_quality_repeated_question_word_is_penalised():
    # 4 repeats: word-salad gate (needs >= 4 words) fires: 55 + 20 + 5 - 30.
    assert _title_quality_score("لماذا لماذا لماذا لماذا؟") == 50.0
    # 3 repeats stay under the gate: no salad penalty.
    assert _title_quality_score("لماذا لماذا لماذا؟") == 80.0


def test_title_quality_clamps_at_zero():
    assert _title_quality_score("AAAA " * 14) == 0.0


# ---------------------------------------------------------------------------
# _choose_recommended_title
# ---------------------------------------------------------------------------

def test_choose_recommended_title_empty_falls_back():
    assert _choose_recommended_title({}) == "Viral Segment"
    assert _choose_recommended_title({"title": "  ", "alt_titles": []}) == (
        "Viral Segment")


def test_choose_recommended_title_deduplicates_case_insensitively():
    segment = {"title": "Hello", "alt_titles": ["hello", "HELLO", "World"]}
    assert _choose_recommended_title(segment) == "Hello"


def test_choose_recommended_title_picks_strongest():
    segment = {
        "title": "حرب حرب الكوكايين تطيح تطيح بصناع المحتوى",
        "alt_titles": ["كيف تطيح حرب الكوكايين بصناع المحتوى؟"],
    }
    assert _choose_recommended_title(segment) == (
        "كيف تطيح حرب الكوكايين بصناع المحتوى؟")


def test_choose_recommended_title_accepts_string_alt_titles():
    segment = {"title": "A", "alt_titles": "Better title here"}
    assert _choose_recommended_title(segment) == "Better title here"


def test_choose_recommended_title_uses_window_text_when_given():
    segment = dict(_HALLUCINATION_SEGMENT)
    segment["alt_titles"] = ["patience"]
    assert _choose_recommended_title(segment) == "shortcut"
    window = _window_text_from_transcript(_HALLUCINATION_TRANSCRIPT, 2.0, 12.0)
    assert _choose_recommended_title(segment, window_text=window) == "patience"


def test_choose_recommended_title_keeps_first_on_tie():
    segment = {"title": "Alpha", "alt_titles": ["Beta"]}
    assert _choose_recommended_title(segment) == "Alpha"
