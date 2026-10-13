# -*- coding: utf-8 -*-
"""Selection-hardening regressions (empty transcript, live semantic penalty,
legacy alias, punctuation-aware opening analysis, fail-open editor review,
defensive de-duplication sort).

Each test reproduces a confirmed execution-reproduced defect and locks in the
hardened behaviour without touching the normal (well-formed) path.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import clip_scoring, editor_review, hook_analyzer
from scripts import create_viral_segments as cvs

# Two transcripts expressing the same idea with different wording; similarity
# lands in the [0.55, dup_threshold) band so the semantic repetition penalty
# (not de-duplication) is what must fire.
_IDEA_A = "النجاح في العمل الحر يحتاج مهارة واحدة وتسويق يومي مستمر"
_IDEA_B = "النجاح في العمل الحر يحتاج مهارة واحدة وتسويق يومي مستمر فعلا"


def _factors(value=80.0):
    return dict.fromkeys(clip_scoring.DEFAULT_SELECTION_WEIGHTS, value)


# ---------------------------------------------------------------------------
# 1. Empty transcript must not crash process_segments on a raw min()
# ---------------------------------------------------------------------------

def test_process_segments_empty_transcript_returns_empty_result():
    result = cvs.process_segments([], [], 10, 60)
    assert result == {"segments": []}


def test_process_segments_empty_transcript_with_raw_segments_returns_empty():
    raw = [{"title": "T", "start_time": 0.0, "end_time": 10.0, "score": 90}]
    result = cvs.process_segments(raw, [], 10, 60)
    assert result == {"segments": []}


# ---------------------------------------------------------------------------
# 2. Semantic repetition penalty must move selection_score (ranking + dedup)
# ---------------------------------------------------------------------------

def test_semantic_repetition_penalty_lowers_selection_score():
    weights = clip_scoring.load_selection_weights()
    first = {"title": "first", "start_time": 0.0, "end_time": 30.0,
             "transcript_text": _IDEA_A, "score_breakdown": _factors()}
    second = {"title": "second", "start_time": 200.0, "end_time": 230.0,
              "transcript_text": _IDEA_B, "score_breakdown": _factors()}
    for candidate in (first, second):
        candidate["selection_score"] = clip_scoring.compute_final_score(
            candidate["score_breakdown"], weights)

    before = second["selection_score"]
    cvs._apply_semantic_repetition_penalties([first, second], weights)

    penalty = second["score_breakdown"]["repetition_penalty"]
    assert penalty > 0.0
    # The recomputed score genuinely reflects the penalty (not just the
    # stored breakdown) so ranking/dedup/editorial floor can act on it.
    assert second["selection_score"] == clip_scoring.compute_final_score(
        second["score_breakdown"], weights)
    assert second["selection_score"] < before
    assert second["selection_score"] < first["selection_score"]


def test_semantic_repetition_penalty_preserves_performance_nudge():
    weights = clip_scoring.load_selection_weights()
    first = {"title": "first", "start_time": 0.0, "end_time": 30.0,
             "transcript_text": _IDEA_A, "score_breakdown": _factors()}
    second = {"title": "second", "start_time": 200.0, "end_time": 230.0,
              "transcript_text": _IDEA_B, "score_breakdown": _factors()}
    for candidate in (first, second):
        candidate["selection_score"] = round(
            clip_scoring.compute_final_score(candidate["score_breakdown"], weights) + 3.0, 1)

    cvs._apply_semantic_repetition_penalties([first, second], weights)

    assert second["selection_score"] == round(
        clip_scoring.compute_final_score(second["score_breakdown"], weights) + 3.0, 1)


def test_no_semantic_penalty_leaves_score_untouched():
    weights = clip_scoring.load_selection_weights()
    unique = {"title": "unique", "start_time": 0.0, "end_time": 30.0,
              "transcript_text": "البرمجة مهنة المستقبل تعلم لغة واحدة بعمق شديد",
              "score_breakdown": _factors()}
    unique["selection_score"] = clip_scoring.compute_final_score(
        unique["score_breakdown"], weights)
    before = unique["selection_score"]
    cvs._apply_semantic_repetition_penalties([unique], weights)
    assert unique["selection_score"] == before


# ---------------------------------------------------------------------------
# 3. Legacy factor alias must actually be picked up
# ---------------------------------------------------------------------------

def test_legacy_completion_score_alias_is_applied():
    legacy = clip_scoring.compute_final_score({"completion_score": 100.0})
    canonical = clip_scoring.compute_final_score({"narrative_completeness": 100.0})
    neutral = clip_scoring.compute_final_score({})
    assert legacy == canonical
    assert legacy != neutral


# ---------------------------------------------------------------------------
# 4. Opening-filler detection must ignore trailing punctuation
# ---------------------------------------------------------------------------

def test_opening_filler_detection_ignores_punctuation():
    punctuated = hook_analyzer.analyze_opening("يعني، طيب، هذا الموضوع مهم جدا")
    plain = hook_analyzer.analyze_opening("يعني طيب هذا الموضوع مهم جدا")
    assert punctuated["opens_with_filler"] is True
    assert punctuated["trim_words"] == 2
    assert punctuated["trim_words"] == plain["trim_words"]
    assert punctuated["hook_score"] == plain["hook_score"]
    assert hook_analyzer._norm_word(punctuated["filler_word"]) == (
        hook_analyzer._norm_word(plain["filler_word"]))


def test_mid_thought_marker_detection_ignores_punctuation():
    report = hook_analyzer.analyze_opening("وبعدين، قال لي إن المشروع كله اتغير")
    assert report["opens_mid_thought"] is True
    assert report["continuation_marker"] == "وبعدين"


def test_question_hook_detection_ignores_punctuation():
    report = hook_analyzer.analyze_opening("ليش، الناس تخاف من التغيير")
    assert report["has_question_hook"] is True


# ---------------------------------------------------------------------------
# 5. editor_review honours its "NEVER raises" contract
# ---------------------------------------------------------------------------

def test_editor_review_segments_never_raises_on_bad_duration():
    segment = {"title": "T", "start_time": 0.0, "end_time": 20.0,
               "duration": "abc", "score": 90, "transcript_text": "نص"}
    result = editor_review.editor_review_segments([segment], lambda prompt: "{}")
    assert result["summary"]["candidates"] == 1
    assert result["summary"]["errors"] == []


def test_editor_review_segments_never_raises_on_bad_score():
    segment = {"title": "T", "start_time": 0.0, "end_time": 20.0,
               "duration": 20.0, "score": "not-a-number", "transcript_text": "نص"}
    result = editor_review.editor_review_segments([segment], lambda prompt: "{}")
    assert result["summary"]["candidates"] == 1


def test_apply_editor_reviews_handles_none_trim_timestamps():
    segments = [{"title": "T", "start_time": 0.0, "end_time": 20.0,
                 "duration": 20.0, "score": 90}]
    result = editor_review.apply_editor_reviews(
        segments, [{"id": "c0", "verdict": "trim",
                    "trim_start": None, "trim_end": None}])
    entry = result["segments"][0]
    # Degrades to the original window instead of raising a TypeError.
    assert entry["start_time"] == 0.0
    assert entry["end_time"] == 20.0


def test_apply_editor_reviews_coerces_string_trim_timestamps():
    segments = [{"title": "T", "start_time": 0.0, "end_time": 30.0,
                 "duration": 30.0, "score": 90}]
    result = editor_review.apply_editor_reviews(
        segments, [{"id": "c0", "verdict": "trim",
                    "trim_start": "5", "trim_end": "28"}])
    entry = result["segments"][0]
    assert entry["start_time"] == 5.0
    assert entry["end_time"] == 28.0


def test_apply_editor_reviews_clamps_garbage_trim_without_raising():
    segments = [{"title": "T", "start_time": 0.0, "end_time": 20.0,
                 "duration": "bad", "score": 90}]
    result = editor_review.apply_editor_reviews(
        segments, [{"id": "c0", "verdict": "trim",
                    "trim_start": "junk", "trim_end": object()}])
    assert result["summary"]["reviewed"] == 1


# ---------------------------------------------------------------------------
# 6. deduplicate_segments sort key must survive malformed candidates
# ---------------------------------------------------------------------------

def test_deduplicate_segments_skips_non_dict_entries():
    segments = [
        {"title": "keep", "start_time": 0.0, "end_time": 10.0,
         "selection_score": 90},
        "junk",
        None,
    ]
    kept = cvs.deduplicate_segments(segments)
    assert [item["title"] for item in kept] == ["keep"]


def test_deduplicate_segments_tolerates_non_numeric_score():
    segments = [
        {"title": "bad", "start_time": 0.0, "end_time": 10.0,
         "selection_score": "abc"},
        {"title": "good", "start_time": 100.0, "end_time": 110.0,
         "selection_score": 90},
    ]
    kept = cvs.deduplicate_segments(segments)
    assert len(kept) == 2
    assert kept[0]["title"] == "good"  # numeric score outranks the junk one


def test_rank_segments_with_diversity_tolerates_non_numeric_score():
    segments = [
        {"title": "bad", "selection_score": "not-a-number"},
        {"title": "good", "selection_score": 90},
    ]
    ranked = cvs._rank_segments_with_diversity(segments)
    assert [item["title"] for item in ranked] == ["good", "bad"]
