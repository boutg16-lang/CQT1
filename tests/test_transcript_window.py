# -*- coding: utf-8 -*-
"""Direct tests for scripts/transcript_window.py — transcript-window analysis.

Covers pre/post-roll config, WhisperX word-timing loading, sentence-unit
splitting, window analysis, word-level edge snapping, connector/dangling
repairs, boundary refinement and semantic-duplication guards. Pure stdlib;
the only I/O is ``load_word_timings`` reading ``input.json`` from a tmp dir.
No network, no models, no media.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import transcript_window as tw

SEGMENTS = [
    {"start": 0.0, "end": 4.0, "text": "النجاح يحتاج إلى صبر واستمرارية."},
    {"start": 5.0, "end": 9.0, "text": "لكن الطريق طويل."},
]

WORDS = [
    {"start": 0.0, "end": 0.5, "word": "النجاح"},
    {"start": 0.5, "end": 1.0, "word": "يحتاج"},
    {"start": 1.0, "end": 1.6, "word": "صبر"},
]


# ---------------------------------------------------------------------------
# pre_post_roll_config
# ---------------------------------------------------------------------------

def test_pre_post_roll_defaults_disabled(monkeypatch):
    monkeypatch.delenv("VIRALCUTTER_PRE_ROLL", raising=False)
    monkeypatch.delenv("VIRALCUTTER_POST_ROLL", raising=False)
    assert tw.pre_post_roll_config() == {"pre_roll": 0.0, "post_roll": 0.0}


def test_pre_post_roll_valid_values(monkeypatch):
    monkeypatch.setenv("VIRALCUTTER_PRE_ROLL", "0.3")
    monkeypatch.setenv("VIRALCUTTER_POST_ROLL", "0.5")
    assert tw.pre_post_roll_config() == {"pre_roll": 0.3, "post_roll": 0.5}


def test_pre_post_roll_clamped_to_documented_bounds(monkeypatch):
    monkeypatch.setenv("VIRALCUTTER_PRE_ROLL", "0.9")   # max 0.50
    monkeypatch.setenv("VIRALCUTTER_POST_ROLL", "0.1")  # min 0.30 when enabled
    assert tw.pre_post_roll_config() == {"pre_roll": 0.5, "post_roll": 0.3}


def test_pre_post_roll_invalid_and_non_positive(monkeypatch):
    monkeypatch.setenv("VIRALCUTTER_PRE_ROLL", "abc")
    monkeypatch.setenv("VIRALCUTTER_POST_ROLL", "-1")
    assert tw.pre_post_roll_config() == {"pre_roll": 0.0, "post_roll": 0.0}


# ---------------------------------------------------------------------------
# load_word_timings
# ---------------------------------------------------------------------------

def test_load_word_timings_missing_folder_and_none(tmp_path):
    assert tw.load_word_timings(tmp_path) == []
    assert tw.load_word_timings("") == []
    assert tw.load_word_timings(None) == []


def test_load_word_timings_valid_and_malformed_filtered(tmp_path):
    data = {"segments": [{"words": [
        {"start": 0.0, "end": 0.5, "word": "hi"},
        {"start": "bad", "end": 1, "word": "x"},   # non-numeric start
        {"start": 2, "end": 1, "word": "y"},       # end before start
        {"start": 1, "end": 2, "word": ""},        # empty word
        {"start": 1.0, "end": 1.5, "text": "ok"},  # "text" key also works
    ]}]}
    (tmp_path / "input.json").write_text(json.dumps(data), encoding="utf-8")
    words = tw.load_word_timings(tmp_path)
    assert words == [
        {"start": 0.0, "end": 0.5, "word": "hi"},
        {"start": 1.0, "end": 1.5, "word": "ok"},
    ]


def test_load_word_timings_sorted_by_start(tmp_path):
    data = {"segments": [{"words": [
        {"start": 2.0, "end": 2.5, "word": "b"},
        {"start": 0.0, "end": 0.5, "word": "a"},
    ]}]}
    (tmp_path / "input.json").write_text(json.dumps(data), encoding="utf-8")
    assert [w["word"] for w in tw.load_word_timings(tmp_path)] == ["a", "b"]


def test_load_word_timings_bad_json_and_non_dict_root(tmp_path):
    (tmp_path / "input.json").write_text("not json", encoding="utf-8")
    assert tw.load_word_timings(tmp_path) == []
    (tmp_path / "input.json").write_text("[1, 2]", encoding="utf-8")
    assert tw.load_word_timings(tmp_path) == []
    (tmp_path / "input.json").write_text(
        json.dumps({"segments": ["not-a-dict"]}), encoding="utf-8")
    assert tw.load_word_timings(tmp_path) == []


# ---------------------------------------------------------------------------
# split_sentence_units
# ---------------------------------------------------------------------------

def test_split_sentence_units_empty_and_malformed():
    assert tw.split_sentence_units([]) == []
    assert tw.split_sentence_units(None) == []
    assert tw.split_sentence_units([{"start": "x", "text": "hi"}, "nope"]) == []


def test_split_sentence_units_punctuation_split_inside_one_block():
    units = tw.split_sentence_units([
        {"start": 0.0, "end": 6.0,
         "text": "النجاح يحتاج صبر. لكن الطريق طويل."}])
    assert len(units) == 2
    assert units[0]["text"] == "النجاح يحتاج صبر."
    assert units[1]["text"] == "لكن الطريق طويل."
    assert units[0]["start"] == 0.0
    assert units[1]["end"] == 6.0


def test_split_sentence_units_decimal_point_is_not_a_boundary():
    units = tw.split_sentence_units([
        {"start": 0.0, "end": 4.0, "text": "النسبة 3.5 بالمئة من السوق."}])
    assert len(units) == 1


def test_split_sentence_units_pause_gap_splits_unpunctuated_lines():
    units = tw.split_sentence_units([
        {"start": 0.0, "end": 1.0, "text": "أهلا بكم"},
        {"start": 2.0, "end": 3.0, "text": "مرحبا بالجميع"},
    ])
    assert [u["text"] for u in units] == ["أهلا بكم", "مرحبا بالجميع"]


def test_split_sentence_units_word_timings_are_authoritative():
    units = tw.split_sentence_units(
        [{"start": 0.0, "end": 3.0, "text": "النجاح يحتاج صبر. لكن الطريق"}],
        words=[
            {"start": 0.0, "end": 0.4, "word": "النجاح"},
            {"start": 0.5, "end": 0.9, "word": "يحتاج"},
            {"start": 1.0, "end": 1.4, "word": "صبر."},
            {"start": 2.0, "end": 2.4, "word": "لكن"},
            {"start": 2.5, "end": 2.9, "word": "الطريق"},
        ])
    assert len(units) == 2
    assert units[0]["text"] == "النجاح يحتاج صبر."
    assert units[1]["text"] == "لكن الطريق"


# ---------------------------------------------------------------------------
# analyze_window
# ---------------------------------------------------------------------------

def test_analyze_window_complete_clip():
    result = tw.analyze_window(SEGMENTS, 0.0, 4.0)
    assert result["text"] == "النجاح يحتاج إلى صبر واستمرارية."
    assert result["first_sentence"] == result["last_sentence"] == result["text"]
    assert result["word_count"] == 5
    assert result["unique_ratio"] == 1.0
    assert result["speech_coverage"] == 1.0
    assert result["silence_ratio"] == 0.0
    assert result["leading_silence"] == 0.0
    assert result["trailing_silence"] == 0.0
    assert result["complete"] is True
    assert result["after_text"] == "لكن الطريق طويل."


def test_analyze_window_cut_inside_a_line_is_mid_sentence():
    result = tw.analyze_window(SEGMENTS, 1.0, 4.0)
    assert result["starts_mid_sentence"] is True
    assert result["complete"] is False


def test_analyze_window_connector_start():
    result = tw.analyze_window(SEGMENTS, 5.0, 9.0)
    assert result["starts_with_connector"] is True
    assert result["before_text"] == "النجاح يحتاج إلى صبر واستمرارية."


def test_analyze_window_dangling_end_and_unresolved_opener():
    segments = [
        {"start": 0.0, "end": 4.0, "text": "ذهب إلى السوق في الصباح."},
        {"start": 4.0, "end": 8.0, "text": "ثم عاد إلى البيت مع"},
    ]
    result = tw.analyze_window(segments, 4.0, 8.0)
    assert result["ends_incomplete"] is True
    assert result["complete"] is False

    opener = tw.analyze_window(
        [{"start": 0.0, "end": 3.0, "text": "هذا هو السر."}], 0.0, 3.0)
    assert opener["opens_with_unresolved_reference"] is True


def test_analyze_window_silence_and_coverage():
    segments = [{"start": 2.0, "end": 3.0, "text": "مرحبا"}]
    result = tw.analyze_window(segments, 0.0, 5.0)
    assert result["leading_silence"] == 2.0
    assert result["trailing_silence"] == 2.0
    assert result["speech_coverage"] == 0.2
    assert result["silence_ratio"] == 0.8


def test_analyze_window_word_timings_drive_text():
    segments = [{"start": 0.0, "end": 4.0,
                 "text": "النجاح يحتاج صبر واستمرارية."}]
    result = tw.analyze_window(segments, 0.0, 1.2, words=WORDS)
    assert result["text"] == "النجاح يحتاج صبر"
    assert result["word_count"] == 3


def test_analyze_window_malformed_input_never_raises():
    empty = tw.analyze_window([], 0, 1)
    assert empty["word_count"] == 0 and empty["complete"] is False
    assert tw.analyze_window(SEGMENTS, 5, 5)["text"] == ""
    assert tw.analyze_window(SEGMENTS, "x", "y")["text"] == ""
    assert tw.analyze_window(None, 0, 1)["text"] == ""
    assert tw.analyze_window([{"start": "bad", "text": "x"}], 0, 1)["text"] == ""


# ---------------------------------------------------------------------------
# snap_edges_to_words
# ---------------------------------------------------------------------------

def test_snap_edges_to_words_snaps_inside_word():
    assert tw.snap_edges_to_words(1.1, 3.9, [
        {"start": 1.0, "end": 1.5, "word": "a"},
        {"start": 3.5, "end": 4.0, "word": "b"},
    ]) == (1.0, 4.0)


def test_snap_edges_to_words_gap_untouched_and_no_words():
    words = [
        {"start": 1.0, "end": 1.5, "word": "a"},
        {"start": 3.5, "end": 4.0, "word": "b"},
    ]
    assert tw.snap_edges_to_words(2.0, 3.0, words) == (2.0, 3.0)
    assert tw.snap_edges_to_words(1.1, 3.9, []) == (1.1, 3.9)
    assert tw.snap_edges_to_words(1.1, 3.9, None) == (1.1, 3.9)


def test_snap_edges_to_words_degenerate_snap_reverts():
    # Snapping would collapse the window to <= 0.05 s: keep the original.
    assert tw.snap_edges_to_words(1.01, 1.03, [
        {"start": 1.0, "end": 1.04, "word": "a"},
    ]) == (1.01, 1.03)


# ---------------------------------------------------------------------------
# repair_connector_start / repair_dangling_end
# ---------------------------------------------------------------------------

def test_repair_connector_start_includes_antecedent():
    analysis = tw.analyze_window(SEGMENTS, 5.0, 9.0)
    new_start, note = tw.repair_connector_start(5.0, 9.0, SEGMENTS, analysis, 60.0)
    assert (new_start, note) == (0.0, "connector_start_included_antecedent")


def test_repair_connector_start_blocked_by_duration_or_distance():
    analysis = tw.analyze_window(SEGMENTS, 5.0, 9.0)
    # Including the antecedent would exceed max_duration.
    assert tw.repair_connector_start(5.0, 9.0, SEGMENTS, analysis, 3.0) == (5.0, None)
    # No connector flag at all.
    assert tw.repair_connector_start(
        5.0, 9.0, SEGMENTS, {"starts_with_connector": False}, 60.0) == (5.0, None)
    # Antecedent more than CONNECTOR_LOOKBACK_SECONDS away.
    far = [{"start": 0.0, "end": 4.0, "text": "النجاح يحتاج صبر."},
           {"start": 20.0, "end": 24.0, "text": "لكن الطريق طويل."}]
    analysis = tw.analyze_window(far, 20.0, 24.0)
    assert tw.repair_connector_start(20.0, 24.0, far, analysis, 60.0) == (20.0, None)


def test_repair_dangling_end_extends_to_sentence_end():
    segments = [{"start": 0.0, "end": 10.0,
                 "text": "ذهب إلى السوق ثم عاد إلى البيت مع"}]
    analysis = tw.analyze_window(segments, 0.0, 6.0)
    assert analysis["ends_incomplete"] is True
    new_end, note = tw.repair_dangling_end(
        0.0, 6.0, segments, analysis, 60.0, transcript_end=10.0)
    assert (new_end, note) == (10.0, "dangling_end_extended_to_sentence_end")


def test_repair_dangling_end_noop_cases():
    analysis = tw.analyze_window(SEGMENTS, 0.0, 4.0)
    assert tw.repair_dangling_end(0.0, 4.0, SEGMENTS, analysis, 60.0) == (4.0, None)
    # Extension would exceed max_duration.
    segments = [{"start": 0.0, "end": 10.0,
                 "text": "ذهب إلى السوق ثم عاد إلى البيت مع"}]
    analysis = tw.analyze_window(segments, 0.0, 6.0)
    assert tw.repair_dangling_end(0.0, 6.0, segments, analysis, 5.0) == (6.0, None)


# ---------------------------------------------------------------------------
# refine_boundaries
# ---------------------------------------------------------------------------

def test_refine_boundaries_word_snap():
    start, end, notes = tw.refine_boundaries(
        0.2, 1.4, SEGMENTS, words=WORDS, max_duration=60.0)
    assert (start, end) == (0.0, 1.6)
    assert "word_boundary_snap" in notes


def test_refine_boundaries_max_duration_reverts():
    start, end, notes = tw.refine_boundaries(
        0.0, 4.0, SEGMENTS, words=WORDS, max_duration=2.0)
    assert (start, end) == (0.0, 4.0)
    assert "refinement_reverted_max_duration" in notes


def test_refine_boundaries_pre_post_roll_applied(monkeypatch):
    monkeypatch.setenv("VIRALCUTTER_PRE_ROLL", "0.3")
    monkeypatch.setenv("VIRALCUTTER_POST_ROLL", "0.5")
    start, end, notes = tw.refine_boundaries(
        1.0, 3.0, SEGMENTS, max_duration=60.0,
        transcript_start=0.0, transcript_end=9.0)
    assert (start, end) == (0.7, 3.5)
    assert "pre_post_roll_applied" in notes


def test_refine_boundaries_roll_budget_respects_max_duration(monkeypatch):
    monkeypatch.setenv("VIRALCUTTER_PRE_ROLL", "0.5")
    monkeypatch.setenv("VIRALCUTTER_POST_ROLL", "0.8")
    # Window is already 5.9 s of a 6.0 s budget: rolls shrink to fit.
    start, end, notes = tw.refine_boundaries(
        0.0, 5.9, SEGMENTS, max_duration=6.0, transcript_end=9.0)
    assert end - start <= 6.0 + 0.05


def test_refine_boundaries_never_empty():
    start, end, notes = tw.refine_boundaries(5.0, 5.05, SEGMENTS, max_duration=60.0)
    assert end - start >= 0.1


# ---------------------------------------------------------------------------
# Semantic duplication helpers
# ---------------------------------------------------------------------------

def test_semantic_similarity_identical_containment_and_empty():
    assert tw.semantic_similarity("مرحبا بالعالم", "مرحبا بالعالم") == 1.0
    assert tw.semantic_similarity("مرحبا", "مرحبا بالعالم") == 1.0
    assert tw.semantic_similarity("", "x") == 0.0
    assert tw.semantic_similarity("hello world", "hello there") > 0.0


def test_are_semantic_duplicates_identical():
    is_dup, sim = tw.are_semantic_duplicates(
        "النجاح يحتاج صبر واستمرارية", "النجاح يحتاج صبر واستمرارية")
    assert is_dup is True and sim == 1.0


def test_are_semantic_duplicates_numeric_guard():
    # Different stated numbers = different facts, never a duplicate.
    is_dup, _ = tw.are_semantic_duplicates("٣ خطوات للنجاح", "٥ خطوات للنجاح")
    assert is_dup is False
    is_dup, _ = tw.are_semantic_duplicates(
        "part 3 of the course", "part 7 of the course")
    assert is_dup is False


def test_are_semantic_duplicates_template_guard():
    # High character resemblance but almost no shared tokens.
    is_dup, sim = tw.are_semantic_duplicates(
        "aaaaaaaaaaaaaaaaaaaaaaaaaaaa bbbbb",
        "aaaaaaaaaaaaaaaaaaaaaaaaaaaa ccccc")
    assert sim >= 0.75
    assert is_dup is False


def test_are_semantic_duplicates_threshold_env(monkeypatch):
    # Non-containing pair: similarity ~0.8, so the threshold decides.
    text_a = "النجاح يحتاج صبر وجهد"
    text_b = "النجاح يحتاج صبر واستمرارية"
    monkeypatch.setenv("VIRALCUTTER_SEMANTIC_DUP_THRESHOLD", "0.99")
    is_dup, sim = tw.are_semantic_duplicates(text_a, text_b)
    assert sim < 0.99
    assert is_dup is False
    monkeypatch.setenv("VIRALCUTTER_SEMANTIC_DUP_THRESHOLD", "0.5")
    is_dup, _ = tw.are_semantic_duplicates(text_a, text_b)
    assert is_dup is True


def test_guarded_semantic_similarity():
    assert tw.guarded_semantic_similarity(
        "النجاح يحتاج صبر", "النجاح يحتاج صبر") == 1.0
    assert tw.guarded_semantic_similarity("٣ خطوات", "٥ خطوات") == 0.0
    # No real token overlap: character resemblance alone is not repetition.
    assert tw.guarded_semantic_similarity("a b c", "x y z") == 0.0


def test_semantic_duplicate_threshold_env(monkeypatch):
    monkeypatch.delenv("VIRALCUTTER_SEMANTIC_DUP_THRESHOLD", raising=False)
    assert tw.semantic_duplicate_threshold() == 0.75
    monkeypatch.setenv("VIRALCUTTER_SEMANTIC_DUP_THRESHOLD", "0.5")
    assert tw.semantic_duplicate_threshold() == 0.5
    monkeypatch.setenv("VIRALCUTTER_SEMANTIC_DUP_THRESHOLD", "99")
    assert tw.semantic_duplicate_threshold() == 1.0  # clamped
    monkeypatch.setenv("VIRALCUTTER_SEMANTIC_DUP_THRESHOLD", "junk")
    assert tw.semantic_duplicate_threshold() == 0.75


def test_repetition_penalty_slope_env(monkeypatch):
    monkeypatch.delenv("VIRALCUTTER_REPETITION_PENALTY_MAX", raising=False)
    assert tw.repetition_penalty_slope() == 25.0
    monkeypatch.setenv("VIRALCUTTER_REPETITION_PENALTY_MAX", "40")
    assert tw.repetition_penalty_slope() == 40.0
    monkeypatch.setenv("VIRALCUTTER_REPETITION_PENALTY_MAX", "-5")
    assert tw.repetition_penalty_slope() == 0.0  # clamped
    monkeypatch.setenv("VIRALCUTTER_REPETITION_PENALTY_MAX", "junk")
    assert tw.repetition_penalty_slope() == 25.0
