# -*- coding: utf-8 -*-
"""Direct tests for scripts/create_viral_segments.py (pure helpers only).

Covers: model-response JSON recovery, Arabic timestamp parsing, text
similarity and Arabic orthography normalisation, window overlap and
de-duplication, fingerprint stability, boundary snapping, window
validation, the quality gate, selection scoring/diversity ranking, title
scoring, and transcript parsing. No model, network or real video is used:
g4f/llama_cpp are only imported lazily by the module and never invoked.
"""

import json

import pytest

from scripts import create_viral_segments as cvs

# ---------------------------------------------------------------------------
# clean_json_response / _extract_segments_json
# ---------------------------------------------------------------------------

def test_clean_json_response_plain_object():
    payload = {"segments": [{"start_time": 1, "end_time": 2, "title": "x"}]}
    assert cvs.clean_json_response(json.dumps(payload)) == payload


def test_clean_json_response_fenced_and_think_tags():
    fenced = '```json\n{"segments": [{"start_time": 1, "end_time": 2}]}\n```'
    assert cvs.clean_json_response(fenced)["segments"][0]["start_time"] == 1
    tagged = '<think>reasoning</think>{"segments": [{"start_time": 1, "end_time": 2}]}'
    assert cvs.clean_json_response(tagged)["segments"][0]["end_time"] == 2


def test_clean_json_response_recovers_truncated_payload():
    truncated = ('{"segments": [{"start_time": 10, "end_time": 20, "title": "A"}, '
                 '{"start_time": 30, "end_time": 40, "title": "B')
    result = cvs.clean_json_response(truncated)
    assert result["segments"] == [{"start_time": 10, "end_time": 20, "title": "A"}]


def test_clean_json_response_empty_and_unparseable():
    assert cvs.clean_json_response("") == {"segments": []}
    assert cvs.clean_json_response(123) == {"segments": []}
    assert cvs.clean_json_response("hello world") == {"segments": []}
    assert cvs.clean_json_response('{"other": 1}') == {"segments": []}


def test_clean_json_response_keeps_escaped_newlines_in_valid_json():
    payload = {"segments": [{"title": "line\nbreak", "start_time": 1, "end_time": 2}]}
    assert cvs.clean_json_response(json.dumps(payload)) == payload


# ---------------------------------------------------------------------------
# _bounded_score / _as_float
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value,expected", [
    (150, 100.0), (-5, 0.0), (42, 42.0), ("42", 42.0), ("junk", 0.0),
    (None, 0.0), (float("nan"), 0.0), (float("inf"), 0.0),
])
def test_bounded_score_clamps_and_falls_back(value, expected):
    assert cvs._bounded_score(value) == expected


def test_bounded_score_custom_default():
    assert cvs._bounded_score("junk", default=7.5) == 7.5
    assert cvs._bounded_score(float("nan"), default=7.5) == 7.5


@pytest.mark.parametrize("value,expected", [
    ("3.5", 3.5), (2, 2.0), (None, 0.0), ("x", 0.0), (float("nan"), 0.0),
])
def test_as_float(value, expected):
    assert cvs._as_float(value) == expected
    assert cvs._as_float("x", default=9.0) == 9.0


# ---------------------------------------------------------------------------
# Arabic orthography / text similarity
# ---------------------------------------------------------------------------

def test_normalize_arabic_orthography_unifies_variants():
    assert cvs._normalize_arabic_orthography("الأمور") == "الامور"
    assert cvs._normalize_arabic_orthography("مدرسةٌ") == "مدرسه"
    assert cvs._normalize_arabic_orthography("سـلام") == "سلام"
    # Alif maqsura is deliberately NOT folded into yaa.
    assert cvs._normalize_arabic_orthography("على") != cvs._normalize_arabic_orthography("علي")
    # Latin text passes through unchanged.
    assert cvs._normalize_arabic_orthography("Hello World") == "Hello World"


def test_normalized_match_text_strips_punctuation_and_lowercases():
    assert cvs._normalized_match_text("Hello, World!") == "hello world"
    assert cvs._normalized_match_text("الأمور!") == "الامور"
    assert cvs._normalized_match_text(None) == ""


def test_text_similarity_containment_empty_and_partial():
    assert cvs._text_similarity("hello world", "hello") == 1.0
    assert cvs._text_similarity("hello", "hello world") == 1.0
    assert cvs._text_similarity("", "hello") == 0.0
    assert cvs._text_similarity("hello", "") == 0.0
    partial = cvs._text_similarity("abc def", "abc xyz")
    assert 0.0 < partial < 1.0
    assert cvs._text_similarity("abc", "abc") == 1.0


# ---------------------------------------------------------------------------
# Timestamp parsing (incl. Arabic-Indic digits)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value,expected", [
    (90, 90.0),
    ("90", 90.0),
    ("90s", 90.0),
    ("12.5", 12.5),
    ("1:30", 90.0),
    ("01:02:03", 3723.0),
    ("٩:٥٠", 590.0),       # Arabic-Indic digits: 9:50
    ("1:٩٠", 150.0),       # mixed digits: 1 min + 90 s
    ("12٫5", 12.5),        # Arabic decimal separator
    (-5, 0.0),
    ("abc", 0.0),
    (None, 0.0),
])
def test_parse_segment_time(value, expected):
    assert cvs._parse_segment_time(value) == expected


def test_parse_segment_time_custom_default():
    assert cvs._parse_segment_time(None, default=7.0) == 7.0
    assert cvs._parse_segment_time("", default=7.0) == 7.0
    assert cvs._parse_segment_time("junk", default=7.0) == 7.0


def test_normalize_timestamp_text_translates_digits():
    assert cvs._normalize_timestamp_text("٩:٥٠") == "9:50"
    assert cvs._normalize_timestamp_text("۱۲٫۵") == "12.5"


# ---------------------------------------------------------------------------
# Window overlap / near-duplicates / deduplication
# ---------------------------------------------------------------------------

def test_window_overlap_ratio():
    assert cvs._window_overlap_ratio(
        {"start_time": 0, "end_time": 10}, {"start_time": 0, "end_time": 10}) == 1.0
    assert cvs._window_overlap_ratio(
        {"start_time": 0, "end_time": 10}, {"start_time": 20, "end_time": 30}) == 0.0
    assert cvs._window_overlap_ratio(
        {"start_time": 0, "end_time": 10}, {"start_time": 10, "end_time": 20}) == 0.0
    assert cvs._window_overlap_ratio(
        {"start_time": 0, "end_time": 15}, {"start_time": 10, "end_time": 25}) == pytest.approx(1 / 3)
    assert cvs._window_overlap_ratio({"start_time": 0}, {"start_time": 0, "end_time": 5}) == 0.0


def test_windows_are_near_duplicates_threshold_and_shift_rule():
    # Docstring example: 5/15 overlap is below the 0.40 floor.
    assert cvs._windows_are_near_duplicates(
        {"start_time": 0, "end_time": 15}, {"start_time": 10, "end_time": 25}) is False
    assert cvs._windows_are_near_duplicates(
        {"start_time": 0, "end_time": 10}, {"start_time": 2, "end_time": 12}) is True
    assert cvs._windows_are_near_duplicates(
        {"start_time": 0, "end_time": 10}, {"start_time": 30, "end_time": 40}) is False
    # Shift rule: 0.33 overlap but starts only 2s apart.
    assert cvs._windows_are_near_duplicates(
        {"start_time": 0, "end_time": 3}, {"start_time": 2, "end_time": 20}) is True
    assert cvs._windows_are_near_duplicates(
        {"start_time": 0, "end_time": 3}, {"start_time": 2.5, "end_time": 20}) is False
    assert cvs._windows_are_near_duplicates({}, {"start_time": 0, "end_time": 5}) is False


def test_window_duplicate_config_defaults_and_env(monkeypatch):
    for var in ("VIRALCUTTER_WINDOW_DUP_OVERLAP",
                "VIRALCUTTER_WINDOW_SHIFT_DUP_OVERLAP",
                "VIRALCUTTER_WINDOW_DUP_SHIFT"):
        monkeypatch.delenv(var, raising=False)
    assert cvs.window_duplicate_config() == {
        "overlap": 0.4, "shift_overlap": 0.3, "shift_seconds": 2.0}
    monkeypatch.setenv("VIRALCUTTER_WINDOW_DUP_OVERLAP", "0.9")
    assert cvs.window_duplicate_config()["overlap"] == 0.9
    monkeypatch.setenv("VIRALCUTTER_WINDOW_DUP_OVERLAP", "junk")
    assert cvs.window_duplicate_config()["overlap"] == 0.4


def test_deduplicate_segments_keeps_best_and_drops_temporal_dups():
    segments = [
        {"start_time": 0, "end_time": 10, "score": 50, "title": "A"},
        {"start_time": 30, "end_time": 40, "score": 70, "title": "C"},
        {"start_time": 2, "end_time": 12, "score": 90, "title": "B"},
    ]
    result = cvs.deduplicate_segments(segments)
    assert [s["title"] for s in result] == ["B", "C"]


def test_deduplicate_segments_skips_non_dicts_and_missing_windows():
    segments = [
        "not a dict",
        {"title": "no window", "score": 99},
        {"start_time": 0, "end_time": 10, "score": 10, "title": "kept"},
    ]
    assert [s["title"] for s in cvs.deduplicate_segments(segments)] == ["kept"]


def test_is_semantic_duplicate_without_text_is_false():
    # Segments without transcript_text never count as semantic duplicates,
    # in any environment.
    assert cvs._is_semantic_duplicate({"start_time": 0, "end_time": 10}, []) is False


# ---------------------------------------------------------------------------
# Fingerprints
# ---------------------------------------------------------------------------

def test_segments_manifest_fingerprint_title_independent_order_sensitive():
    a = [{"start_time": 0, "end_time": 10, "title": "One"},
         {"start_time": 20, "end_time": 30, "title": "Two"}]
    b = [{"start_time": 0, "end_time": 10, "title": "Renamed"},
         {"start_time": 20, "end_time": 30, "title": "Also renamed"}]
    c = list(reversed(a))
    d = [{"start_time": 0, "end_time": 11, "title": "One"},
         {"start_time": 20, "end_time": 30, "title": "Two"}]
    assert cvs.segments_manifest_fingerprint(a) == cvs.segments_manifest_fingerprint(b)
    assert cvs.segments_manifest_fingerprint(a) != cvs.segments_manifest_fingerprint(c)
    assert cvs.segments_manifest_fingerprint(a) != cvs.segments_manifest_fingerprint(d)
    assert cvs.segments_manifest_fingerprint(a) == cvs.segments_manifest_fingerprint(a)
    assert cvs.segments_manifest_fingerprint([]) == cvs.segments_manifest_fingerprint(["junk"])


def test_segment_window_fingerprint_stable_and_distinct():
    assert cvs._segment_window_fingerprint(0, 10) == cvs._segment_window_fingerprint(0.0, 10.0)
    assert cvs._segment_window_fingerprint(0, 10) != cvs._segment_window_fingerprint(0, 11)
    assert len(cvs._segment_window_fingerprint(0, 10)) == 16


# ---------------------------------------------------------------------------
# Boundary snapping
# ---------------------------------------------------------------------------

def test_snap_segment_boundaries_to_speech_blocks():
    transcript = [{"start": 0, "end": 5, "text": "aaa"},
                  {"start": 10, "end": 15, "text": "bbb"}]
    assert cvs.snap_segment_boundaries(2, 12, transcript) == (0.0, 15.0)
    # A window fully inside a pause is left untouched.
    assert cvs.snap_segment_boundaries(7, 8, transcript) == (7.0, 8.0)
    assert cvs.snap_segment_boundaries(2, 12, []) == (2.0, 12.0)


def test_speech_blocks_split_on_pauses():
    transcript = [{"start": 0, "end": 1, "text": "a"},
                  {"start": 1.1, "end": 2, "text": "b"},
                  {"start": 5, "end": 6, "text": "c"}]
    assert cvs._speech_blocks(transcript) == [(0.0, 2.0), (5.0, 6.0)]
    assert cvs._speech_blocks([]) == []


# ---------------------------------------------------------------------------
# _validate_segment_window
# ---------------------------------------------------------------------------

def _analysis(**overrides):
    analysis = {"text": "some spoken words here", "word_count": 10,
                "speech_coverage": 0.9}
    analysis.update(overrides)
    return analysis


def test_validate_segment_window_ok_and_rejections():
    assert cvs._validate_segment_window(0, 20, 15, _analysis()) == ([], [])
    rejected, _ = cvs._validate_segment_window(20, 10, 15, _analysis())
    assert "end_time <= start_time" in rejected
    rejected, _ = cvs._validate_segment_window(-1, 20, 15, _analysis())
    assert any("negative" in r for r in rejected)
    rejected, _ = cvs._validate_segment_window("a", "b", 15, _analysis())
    assert rejected == ["invalid timestamps: not numeric"]
    rejected, _ = cvs._validate_segment_window(0, 10, 15, _analysis())
    assert any("below minimum" in r for r in rejected)
    # Transcript-limited short windows are not rejected for duration.
    rejected, _ = cvs._validate_segment_window(
        0, 10, 15, _analysis(transcript_limited=True))
    assert rejected == []


def test_validate_segment_window_insufficient_text_and_silence():
    rejected, _ = cvs._validate_segment_window(0, 20, 15, {})
    assert "insufficient transcript text inside window" in rejected
    rejected, _ = cvs._validate_segment_window(0, 20, 15, _analysis(word_count=0))
    assert any("insufficient transcript text" in r for r in rejected)
    rejected, _ = cvs._validate_segment_window(0, 20, 15, _analysis(speech_coverage=0.02))
    assert any("excessive silence" in r for r in rejected)
    rejected, flags = cvs._validate_segment_window(0, 20, 15, _analysis(speech_coverage=0.2))
    assert rejected == []
    assert any("low speech density" in f for f in flags)


def test_validate_segment_window_strict_boundaries(monkeypatch):
    monkeypatch.delenv("VIRALCUTTER_STRICT_BOUNDARIES", raising=False)
    rejected, flags = cvs._validate_segment_window(
        0, 20, 15, _analysis(ends_mid_sentence=True))
    assert rejected == []
    assert "ends mid-sentence" in flags
    monkeypatch.setenv("VIRALCUTTER_STRICT_BOUNDARIES", "1")
    rejected, flags = cvs._validate_segment_window(
        0, 20, 15, _analysis(ends_mid_sentence=True))
    assert rejected == ["ends mid-sentence"]
    assert flags == []


# ---------------------------------------------------------------------------
# Quality gate
# ---------------------------------------------------------------------------

def _gated_segment(**overrides):
    segment = {"title": "T", "hook_strength": 80,
               "narrative_completeness": 80, "clarity_score": 80}
    segment.update(overrides)
    return segment


def test_quality_gate_config_defaults_and_env(monkeypatch):
    for var in ("VIRALCUTTER_QUALITY_GATE", "VIRALCUTTER_MIN_HOOK",
                "VIRALCUTTER_MIN_NARRATIVE", "VIRALCUTTER_MIN_CLARITY"):
        monkeypatch.delenv(var, raising=False)
    assert cvs.quality_gate_config() == {
        "enabled": True,
        "min": {"hook_strength": 20.0, "narrative_completeness": 20.0,
                "clarity_score": 20.0}}
    monkeypatch.setenv("VIRALCUTTER_QUALITY_GATE", "0")
    assert cvs.quality_gate_config()["enabled"] is False
    monkeypatch.setenv("VIRALCUTTER_QUALITY_GATE", "1")
    monkeypatch.setenv("VIRALCUTTER_MIN_HOOK", "5")
    assert cvs.quality_gate_config()["min"]["hook_strength"] == 5.0
    monkeypatch.setenv("VIRALCUTTER_MIN_HOOK", "junk")
    assert cvs.quality_gate_config()["min"]["hook_strength"] == 20.0


def test_apply_quality_gate_drops_weak_components():
    kept, dropped = cvs.apply_quality_gate([
        _gated_segment(),
        _gated_segment(title="weak hook", hook_strength=10),
        _gated_segment(title="weak all", hook_strength=5,
                       narrative_completeness=5, clarity_score=5),
    ])
    assert [s["title"] for s in kept] == ["T"]
    assert [d["title"] for d in dropped] == ["weak hook", "weak all"]
    assert dropped[0]["reasons"] == ["hook 10<20.0"]
    assert dropped[0]["index"] == 1
    assert len(dropped[1]["reasons"]) == 3


def test_apply_quality_gate_never_gates_missing_data():
    kept, dropped = cvs.apply_quality_gate([
        _gated_segment(title="missing", quality_missing=True, hook_strength=1),
        {"title": "no scores"},
        "not a dict",
    ])
    assert len(kept) == 3
    assert dropped == []
    kept, dropped = cvs.apply_quality_gate(
        [_gated_segment(title="bad hook", hook_strength="junk")])
    assert kept == []
    assert "hook_strength invalid (not numeric)" in dropped[0]["reasons"]
    kept, dropped = cvs.apply_quality_gate(
        [_gated_segment(title="weak", hook_strength=1)],
        config={"enabled": False, "min": {}})
    assert len(kept) == 1 and dropped == []


# ---------------------------------------------------------------------------
# Selection scoring / diversity ranking
# ---------------------------------------------------------------------------

def test_selection_score_all_max_and_all_missing():
    full = {"score": 100, "hook_strength": 100, "narrative_completeness": 100,
            "clarity_score": 100, "novelty_score": 100, "title_quality_score": 100}
    value, parts = cvs._selection_score(full)
    assert value == 100.0
    assert parts == {"virality": 100.0, "hook": 100.0, "completeness": 100.0,
                     "clarity": 100.0, "novelty": 100.0, "title": 100.0}
    value, parts = cvs._selection_score({})
    assert value == 45.0  # virality 0 + editorial neutral 50
    assert parts["virality"] == 0.0 and parts["hook"] == 50.0


def test_selection_score_virality_weight_is_capped():
    weights = {"virality": 0.5, "hook": 0.2, "completeness": 0.2,
               "clarity": 0.15, "novelty": 0.15, "title": 0.2}
    value, _ = cvs._selection_score({"score": 100}, weights=weights)
    assert value == 55.0  # 0.10*100 + 0.90*50, not 0.5*100 + ...


def test_rank_segments_with_diversity_orders_by_score():
    segments = [
        {"start_time": 0, "end_time": 10, "score": 50, "title": "A"},
        {"start_time": 30, "end_time": 40, "score": 70, "title": "C"},
        {"start_time": 2, "end_time": 12, "score": 90, "title": "B"},
    ]
    assert [s["title"] for s in cvs._rank_segments_with_diversity(segments)] == [
        "B", "C", "A"]
    assert [s["title"] for s in cvs._rank_segments_with_diversity(segments, limit=1)] == ["B"]
    assert cvs._rank_segments_with_diversity([]) == []
    assert cvs._rank_segments_with_diversity(["junk", None]) == []


# ---------------------------------------------------------------------------
# Title scoring / selection
# ---------------------------------------------------------------------------

def test_title_quality_score_basics():
    assert cvs._title_quality_score("") == 0.0
    assert cvs._title_quality_score(None) == 0.0
    # Ideal length (18-72 chars) adds 20 over the 55 base.
    assert cvs._title_quality_score("A well sized title here ok?") == 75.0 + 5.0 + 3.0
    # Over-long titles lose points; all-caps loses 20.
    assert cvs._title_quality_score("x" * 120) == 40.0
    assert cvs._title_quality_score("SHOUTING TITLE HERE OK") == 55.0 + 20.0 - 20.0
    # Repeated words are penalised as keyword stuffing (75 base-length
    # bonus minus 10 for the doubled words).
    assert cvs._title_quality_score("حرب حرب الكوكايين تطيح تطيح") == 65.0


def test_title_content_relevance():
    segment = {"start_text": "cats and dogs", "end_text": "", "caption": ""}
    assert cvs._title_content_relevance("cats dogs", segment) == 1.0
    assert cvs._title_content_relevance("cats birds", segment) == 0.5
    assert cvs._title_content_relevance("birds fish", segment) == 0.0
    assert cvs._title_content_relevance("", segment) == 0.0
    # Window text counts at full weight, LLM-only words at half weight:
    # "cats" is in the window (1.0), "dogs" only in the LLM text (0.5).
    assert cvs._title_content_relevance(
        "cats dogs", segment, window_text="cats") == 0.75


def test_choose_recommended_title_picks_best_and_dedupes():
    segment = {"title": "cats dogs", "alt_titles": ["cats dogs", "birds fish"],
               "start_text": "cats and dogs", "end_text": "", "caption": ""}
    assert cvs._choose_recommended_title(segment) == "cats dogs"
    assert cvs._choose_recommended_title({}) == "Viral Segment"
    assert cvs._choose_recommended_title({"title": "  "}) == "Viral Segment"
    segment = {"title": "short", "alt_titles": "A well sized title here ok?"}
    assert cvs._choose_recommended_title(segment) == "A well sized title here ok?"


# ---------------------------------------------------------------------------
# Transcript parsing / preprocessing
# ---------------------------------------------------------------------------

def test_parse_tsv_transcript_ms_to_seconds(tmp_path):
    path = tmp_path / "input.tsv"
    path.write_text("start\tend\ttext\n0\t1500\tHello\n1500\t3000\tWorld\nbad\trow\there\n",
                    encoding="utf-8")
    segments = cvs._parse_tsv_transcript(str(path))
    assert segments == [{"start": 0.0, "end": 1.5, "text": "Hello"},
                        {"start": 1.5, "end": 3.0, "text": "World"}]
    assert cvs._parse_tsv_transcript(str(tmp_path / "missing.tsv")) == []


def test_parse_srt_transcript(tmp_path):
    path = tmp_path / "input.srt"
    path.write_text("1\n00:00:01,000 --> 00:00:02,500\nHello world\n\n"
                    "2\n00:00:03,000 --> 00:00:04,000\nSecond line\n\n",
                    encoding="utf-8")
    segments = cvs._parse_srt_transcript(str(path))
    assert segments == [{"start": 1.0, "end": 2.5, "text": "Hello world"},
                        {"start": 3.0, "end": 4.0, "text": "Second line"}]
    assert cvs._parse_srt_transcript(str(tmp_path / "missing.srt")) == []


def test_load_transcript_prefers_tsv_then_srt_then_any_file(tmp_path):
    (tmp_path / "input.tsv").write_text(
        "start\tend\ttext\n0\t1000\tFrom TSV\n", encoding="utf-8")
    (tmp_path / "input.srt").write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nFrom SRT\n\n", encoding="utf-8")
    assert cvs.load_transcript(str(tmp_path))[0]["text"] == "From TSV"
    (tmp_path / "input.tsv").unlink()
    assert cvs.load_transcript(str(tmp_path))[0]["text"] == "From SRT"
    (tmp_path / "input.srt").unlink()
    (tmp_path / "video_name.tsv").write_text(
        "start\tend\ttext\n0\t1000\tLegacy name\n", encoding="utf-8")
    assert cvs.load_transcript(str(tmp_path))[0]["text"] == "Legacy name"


def test_load_transcript_raises_when_nothing_found(tmp_path):
    with pytest.raises(ValueError, match="Could not parse transcript"):
        cvs.load_transcript(str(tmp_path))


def test_preprocess_transcript_for_ai_time_tags():
    assert cvs.preprocess_transcript_for_ai([]) == ""
    text = cvs.preprocess_transcript_for_ai([
        {"start": 0, "end": 1, "text": "hello"},
        {"start": 65, "end": 66, "text": "world"},
    ])
    assert text.startswith("(0s) hello")
    assert "(66s)" in text and "world" in text


def test_window_text_from_transcript():
    transcript = [{"start": 0, "end": 5, "text": "alpha beta"},
                  {"start": 10, "end": 15, "text": "gamma delta"}]
    assert cvs._window_text_from_transcript(transcript, 0, 6) == "alpha beta"
    assert cvs._window_text_from_transcript(transcript, 4, 11) == "alpha beta gamma delta"
    assert cvs._window_text_from_transcript(transcript, 20, 30) == ""
    assert cvs._window_text_from_transcript([], 0, 5) == ""
    assert cvs._window_text_from_transcript(transcript, "x", "y") == ""
