# -*- coding: utf-8 -*-
"""Direct tests for scripts/clip_scoring.py (deterministic factor helpers).

Malformed-metadata robustness is covered by
tests/test_clip_scoring_malformed_metadata.py; these tests pin the
documented scoring behaviour: hook/emotion heuristics, narrative and
boundary factors, weight loading/normalisation (including the 35% cap on
model-influenced factors), the legacy ``completion_score`` alias, penalty
handling, and the weights fingerprint. All helpers are pure and
deterministic — no network, no media, no heavy deps.
"""

import json
import math

import pytest

from scripts import clip_scoring
from scripts.clip_scoring import (
    NEUTRAL_SCORE,
    audio_quality_proxy,
    boundary_quality_score,
    completion_score,
    compute_final_score,
    emotional_value_heuristic,
    hook_strength_heuristic,
    information_density_score,
    load_selection_weights,
    min_final_score,
    narrative_completeness_score,
    standalone_context_score,
    transcript_alignment_score,
    visual_quality_score,
    weights_fingerprint,
)

# ---------------------------------------------------------------------------
# hook_strength_heuristic
# ---------------------------------------------------------------------------


def test_hook_empty_uses_fallback():
    assert hook_strength_heuristic("") == 50.0
    assert hook_strength_heuristic(None) == 50.0
    assert hook_strength_heuristic("   ", fallback=30.0) == 30.0


def test_hook_question_with_hook_word_and_punchy_length():
    # 45 base + 22 (ends with ?) + 15 ("why") + 8 (4 words) = 90.0
    assert hook_strength_heuristic("Why did this happen?") == 90.0


def test_hook_plain_statement_gets_base_only():
    # 2 words: no length bonus, no question, no hook word, no digit.
    assert hook_strength_heuristic("Hello world") == 45.0


def test_hook_number_opener_adds_twelve():
    # "3 mistakes" — digit +12, hook word "mistakes" +15, 2 words, no length bonus.
    assert hook_strength_heuristic("3 mistakes to avoid") == 45.0 + 12.0 + 15.0 + 8.0


def test_hook_score_is_capped_at_100():
    assert hook_strength_heuristic("Why are there 7 secrets you never knew?") <= 100.0


# ---------------------------------------------------------------------------
# emotional_value_heuristic
# ---------------------------------------------------------------------------


def test_emotional_empty_is_zero():
    assert emotional_value_heuristic("") == 0.0
    assert emotional_value_heuristic("   ") == 0.0


def test_emotional_plain_text_gets_base_only():
    assert emotional_value_heuristic("The meeting is at noon tomorrow.") == 35.0


def test_emotional_word_and_exclamation_stack():
    # 35 base + 12 (one emotion word) + 7 (one "!") = 54.0
    assert emotional_value_heuristic("This is amazing!") == 54.0


def test_emotional_score_maxes_at_ninety():
    # Ceiling is 35 base + 35 (emotion hits) + 20 (exclamations) = 90.0,
    # not 100: the heuristic never awards a perfect emotional score.
    assert emotional_value_heuristic("amazing crazy insane wow! " * 10) == 90.0


# ---------------------------------------------------------------------------
# narrative / boundary / standalone / visual / transcript / audio factors
# ---------------------------------------------------------------------------


def test_narrative_completeness_full_marks():
    analysis = {
        "starts_mid_sentence": False,
        "ends_mid_sentence": False,
        "ends_with_terminal_punctuation": True,
    }
    assert narrative_completeness_score(analysis) == 100.0


def test_narrative_completeness_partial():
    analysis = {"starts_mid_sentence": True, "ends_mid_sentence": False}
    # +40 for a clean end only; no start bonus, no terminal punctuation.
    assert narrative_completeness_score(analysis) == 40.0


def test_narrative_completeness_non_dict_is_zero():
    assert narrative_completeness_score(None) == 0.0
    assert narrative_completeness_score("broken") == 0.0


def test_completion_score_is_the_v740_alias():
    assert completion_score is narrative_completeness_score


def test_boundary_quality_clean_edges():
    assert boundary_quality_score({}) == 100.0


def test_boundary_quality_penalises_mid_sentence_edges():
    analysis = {"starts_mid_sentence": True, "ends_mid_sentence": True}
    assert boundary_quality_score(analysis) == 40.0


def test_boundary_quality_silence_penalty():
    analysis = {"leading_silence": 2.0, "trailing_silence": 2.0}
    assert boundary_quality_score(analysis) == 80.0


def test_standalone_context_clean_window():
    assert standalone_context_score({"word_count": 50}) == 100.0


def test_standalone_context_connector_and_no_words():
    analysis = {"starts_with_connector": True, "word_count": 0}
    assert standalone_context_score(analysis) == 100.0 - 35.0 - 60.0


def test_standalone_context_non_dict_is_neutral():
    assert standalone_context_score(None) == NEUTRAL_SCORE


def test_visual_quality_neutral_baseline():
    assert visual_quality_score() == NEUTRAL_SCORE
    assert visual_quality_score({}) == NEUTRAL_SCORE
    assert visual_quality_score({"scene_snapped": True}) == 90.0


def test_transcript_alignment_coverage_and_edge():
    assert transcript_alignment_score({"speech_coverage": 1.0}, edge_match=1.0) == 100.0
    assert transcript_alignment_score({"speech_coverage": 0.5}, edge_match=0.5) == 50.0
    assert transcript_alignment_score({}) == 30.0  # 0 coverage + full edge


def test_audio_quality_proxy_silence_hurts():
    assert audio_quality_proxy({}) == 100.0
    assert audio_quality_proxy({"silence_ratio": 1.0}) == 100.0 - 40.0


# ---------------------------------------------------------------------------
# information_density_score
# ---------------------------------------------------------------------------


def test_information_density_sweet_spot():
    assert information_density_score(60, 30) == 100.0  # 2.0 words/sec


def test_information_density_zero_inputs():
    assert information_density_score(0, 30) == 0.0
    assert information_density_score(60, 0) == 0.0
    assert information_density_score(60, -5) == 0.0


def test_information_density_too_slow_scales_down():
    # 0.6 words/sec is half the 1.2 lower bound -> 50.0
    assert information_density_score(18, 30) == 50.0


def test_information_density_variety_adjusts():
    fast = information_density_score(60, 30, unique_ratio=0.0)
    varied = information_density_score(60, 30, unique_ratio=1.0)
    assert varied == 100.0
    assert fast == 70.0


# ---------------------------------------------------------------------------
# load_selection_weights / min_final_score
# ---------------------------------------------------------------------------


def test_default_weights_sum_to_one():
    weights = load_selection_weights()
    assert math.isclose(sum(weights.values()), 1.0, abs_tol=1e-9)
    assert weights == clip_scoring.DEFAULT_SELECTION_WEIGHTS


def test_selection_weights_override_and_clamping(monkeypatch):
    monkeypatch.setenv(
        "VIRALCUTTER_SELECTION_WEIGHTS",
        json.dumps({"hook_strength": 0.25, "visual_quality": 2.5, "nope": 0.5}),
    )
    weights = load_selection_weights()
    assert weights["hook_strength"] == 0.25
    assert weights["visual_quality"] == 1.0  # clamped
    assert "nope" not in weights


def test_selection_weights_invalid_json_falls_back(monkeypatch):
    monkeypatch.setenv("VIRALCUTTER_SELECTION_WEIGHTS", "{broken")
    assert load_selection_weights() == clip_scoring.DEFAULT_SELECTION_WEIGHTS


def test_selection_weights_non_dict_json_falls_back(monkeypatch):
    monkeypatch.setenv("VIRALCUTTER_SELECTION_WEIGHTS", "[1, 2]")
    assert load_selection_weights() == clip_scoring.DEFAULT_SELECTION_WEIGHTS


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("", 0.0), ("30", 30.0), ("150", 100.0), ("-5", 0.0), ("bad", 0.0)],
)
def test_min_final_score(monkeypatch, raw, expected):
    monkeypatch.setenv("VIRALCUTTER_MIN_FINAL_SCORE", raw)
    assert min_final_score() == expected


# ---------------------------------------------------------------------------
# compute_final_score
# ---------------------------------------------------------------------------


def test_final_score_all_perfect_is_100():
    factors = dict.fromkeys(clip_scoring.DEFAULT_SELECTION_WEIGHTS, 100.0)
    assert compute_final_score(factors) == 100.0


def test_final_score_all_zero_is_zero():
    factors = dict.fromkeys(clip_scoring.DEFAULT_SELECTION_WEIGHTS, 0.0)
    assert compute_final_score(factors) == 0.0


def test_final_score_missing_factors_use_neutral_default():
    assert compute_final_score({}) == NEUTRAL_SCORE


def test_final_score_legacy_alias_fills_narrative_completeness():
    factors = {"completion_score": 100.0}
    # All other positive factors default to 80; narrative gets 100 via alias.
    assert compute_final_score(factors) > NEUTRAL_SCORE


def test_final_score_penalties_subtract():
    factors = dict.fromkeys(clip_scoring.DEFAULT_SELECTION_WEIGHTS, 100.0)
    factors["repetition_penalty"] = 10.0
    factors["safety_penalty"] = 5.0
    assert compute_final_score(factors) == 85.0


def test_final_score_is_bounded():
    factors = dict.fromkeys(clip_scoring.DEFAULT_SELECTION_WEIGHTS, 100.0)
    factors["safety_penalty"] = 999.0
    assert compute_final_score(factors) == 0.0


def test_final_score_model_influenced_weight_is_capped():
    # Default weights give model-influenced factors 45% of the raw weight;
    # the normaliser must cap their share at 35% of the final score.
    weights = clip_scoring._normalized_positive_weights()
    model_share = sum(weights[name] for name in clip_scoring.MODEL_INFLUENCED_FACTORS)
    assert model_share <= clip_scoring.MAX_MODEL_INFLUENCED_WEIGHT + 1e-9
    assert math.isclose(sum(weights.values()), 1.0, abs_tol=1e-9)


def test_final_score_all_zero_weights_fall_back_to_defaults():
    weights = {"hook_strength": 0.0, "visual_quality": 0.0}
    factors = dict.fromkeys(clip_scoring.DEFAULT_SELECTION_WEIGHTS, 100.0)
    assert compute_final_score(factors, weights=weights) == 100.0


# ---------------------------------------------------------------------------
# weights_fingerprint
# ---------------------------------------------------------------------------


def test_weights_fingerprint_is_stable_hex():
    first = weights_fingerprint()
    assert first == weights_fingerprint()
    assert len(first) == 16
    int(first, 16)  # must be hex


def test_weights_fingerprint_changes_with_weights():
    base = weights_fingerprint()
    changed = weights_fingerprint(
        {**clip_scoring.DEFAULT_SELECTION_WEIGHTS, "hook_strength": 0.5}
    )
    assert changed != base


def test_weights_fingerprint_includes_scoring_version(monkeypatch):
    before = weights_fingerprint()
    monkeypatch.setattr(clip_scoring, "SCORING_VERSION", "9.9.9")
    assert weights_fingerprint() != before
