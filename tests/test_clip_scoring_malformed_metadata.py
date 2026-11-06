import json
import math

import pytest

from scripts import clip_scoring
from scripts.clip_scoring import (
    audio_quality_proxy,
    boundary_quality_score,
    compute_final_score,
    information_density_score,
    transcript_alignment_score,
)


@pytest.mark.parametrize("bad", ["broken", "", None, float("nan"), float("inf"), -float("inf")])
def test_boundary_metadata_never_crashes(bad):
    score = boundary_quality_score({"leading_silence": bad, "trailing_silence": bad})
    assert math.isfinite(score)
    assert 0 <= score <= 100


@pytest.mark.parametrize("bad", ["broken", "", None, float("nan"), float("inf"), -float("inf")])
def test_audio_metadata_never_crashes(bad):
    score = audio_quality_proxy({"leading_silence": bad, "trailing_silence": bad, "silence_ratio": bad})
    assert math.isfinite(score)
    assert 0 <= score <= 100


@pytest.mark.parametrize("bad", ["broken", "", None, float("nan"), float("inf"), -float("inf")])
def test_transcript_alignment_metadata_is_finite(bad):
    score = transcript_alignment_score({"speech_coverage": bad}, edge_match=bad)
    assert math.isfinite(score)
    assert 0 <= score <= 100


@pytest.mark.parametrize("bad", ["broken", "", None, float("nan"), float("inf"), -float("inf")])
def test_information_density_metadata_is_finite(bad):
    score = information_density_score(100, 30, unique_ratio=bad)
    assert math.isfinite(score)
    assert 0 <= score <= 100


def test_final_score_is_bounded_with_non_finite_factors():
    score = compute_final_score({"hook_strength": float("nan"), "audio_quality": float("inf")})
    assert math.isfinite(score)
    assert 0 <= score <= 100


@pytest.mark.parametrize("bad_value", ["NaN", "Infinity", "-Infinity", "broken"])
def test_selection_weight_environment_rejects_non_finite_values(monkeypatch, bad_value):
    monkeypatch.setenv("VIRALCUTTER_SELECTION_WEIGHTS", json.dumps({"hook_strength": bad_value}))
    weights = clip_scoring.load_selection_weights()
    assert weights["hook_strength"] == clip_scoring.DEFAULT_SELECTION_WEIGHTS["hook_strength"]
    assert all(math.isfinite(value) for value in weights.values())


@pytest.mark.parametrize("bad_value", ["NaN", "Infinity", "-Infinity", "broken"])
def test_min_final_score_environment_falls_back_for_invalid_values(monkeypatch, bad_value):
    monkeypatch.setenv("VIRALCUTTER_MIN_FINAL_SCORE", bad_value)
    assert clip_scoring.min_final_score(default=12.5) == 12.5
