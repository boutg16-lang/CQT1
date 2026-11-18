# -*- coding: utf-8 -*-
"""Direct tests for scripts/performance_weights.py — performance insights
to selection-score weights.

Covers title-style flags, bounded style bonuses, the feature→component
map, insights-file loading and the full ``load_weights`` flow (confidence
scaling, shift clamping, CI suppression, title/duration boosts, weight-mass
renormalisation). Only file I/O is reading ``performance_insights.json``
from tmp dirs; no network, no models.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import performance_weights as pw


def _write_insights(folder, payload):
    (folder / "performance_insights.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8")


INSIGHTS_WITH_STYLE = {
    "with_metrics": 10,
    "content_insights": {
        "overall_avg_views": 1000,
        "categories": {
            "hook_type": [
                {"value": "question", "samples": 5, "delta_pct": 50.0},
                {"value": "rare", "samples": 1, "delta_pct": 90.0},  # <2 samples
            ],
        },
        "title_styles": [
            {"style": "title_question", "delta_pct": 40.0},
            {"style": "bogus", "delta_pct": 99.0},  # unknown marker
        ],
    },
}


# ---------------------------------------------------------------------------
# title_style_flags
# ---------------------------------------------------------------------------

def test_title_style_flags_arabic():
    flags = pw.title_style_flags("كيف تنجح في ٣ خطوات؟")
    assert flags == {
        "title_question": True, "title_number": True, "title_hook_word": True}


def test_title_style_flags_plain_and_empty():
    assert pw.title_style_flags("plain title") == {
        "title_question": False, "title_number": False, "title_hook_word": False}
    assert pw.title_style_flags("")["title_question"] is False
    assert pw.title_style_flags(None)["title_hook_word"] is False


def test_title_style_flags_english_hook_words():
    flags = pw.title_style_flags("How to win? 10 tips")
    assert flags["title_question"] is True
    assert flags["title_number"] is True
    assert flags["title_hook_word"] is True


# ---------------------------------------------------------------------------
# _style_bonus
# ---------------------------------------------------------------------------

def test_style_bonus_threshold_and_clamps():
    assert pw._style_bonus(None) == 0.0
    assert pw._style_bonus(0.0) == 0.0
    assert pw._style_bonus(14.9) == 0.0   # below STYLE_MIN_DELTA_PCT
    assert pw._style_bonus(15.0) == 0.6   # 15 / 25
    assert pw._style_bonus(50.0) == 2.0
    assert pw._style_bonus(100.0) == 3.0  # clamped to STYLE_MAX_BONUS
    assert pw._style_bonus(-100.0) == -3.0


# ---------------------------------------------------------------------------
# style_bonuses / style_bonus_for
# ---------------------------------------------------------------------------

def test_style_bonuses_empty_without_channel_average():
    assert pw.style_bonuses({}) == {}
    assert pw.style_bonuses(None) == {}
    assert pw.style_bonuses({"content_insights": {}}) == {}


def test_style_bonuses_skips_thin_samples_and_unknown_markers():
    style = pw.style_bonuses(INSIGHTS_WITH_STYLE)
    assert style == {
        "basis": "content_insights",
        "hook_type": {"question": 2.0},
        "title_question": 1.6,
    }


def test_style_bonuses_confidence_scales_bonus():
    style = pw.style_bonuses(INSIGHTS_WITH_STYLE, 0.5)
    assert style["hook_type"] == {"question": 1.0}
    assert style["title_question"] == 0.8


def test_style_bonus_for_sums_fields_and_title_markers():
    style = pw.style_bonuses(INSIGHTS_WITH_STYLE)
    segment = {"hook_type": "question", "recommended_title": "كيف؟"}
    assert pw.style_bonus_for(segment, style) == 3.6  # 2.0 + 1.6


def test_style_bonus_for_empty_map_and_title_fallback():
    assert pw.style_bonus_for({"hook_type": "question"}, {}) == 0.0
    assert pw.style_bonus_for({"hook_type": "question"}, None) == 0.0
    style = pw.style_bonuses(INSIGHTS_WITH_STYLE)
    # "title" key is used when "recommended_title" is absent.
    assert pw.style_bonus_for({"title": "كيف؟"}, style) == 1.6
    assert pw.style_bonus_for({"hook_type": "other"}, style) == 0.0


# ---------------------------------------------------------------------------
# feature_to_component
# ---------------------------------------------------------------------------

def test_feature_to_component_mapping():
    assert pw.feature_to_component("hook_strength") == "hook"
    assert pw.feature_to_component("narrative_completeness") == "completeness"
    assert pw.feature_to_component("clarity_score") == "clarity"
    assert pw.feature_to_component("novelty_score") == "novelty"
    assert pw.feature_to_component("duration") is None
    assert pw.feature_to_component("selection_score") is None
    assert pw.feature_to_component("title_quality_score") is None
    assert pw.feature_to_component("unknown_feature") is None


# ---------------------------------------------------------------------------
# _load
# ---------------------------------------------------------------------------

def test_load_missing_file_is_empty(tmp_path):
    assert pw._load(str(tmp_path)) == {}


def test_load_malformed_and_non_dict(tmp_path):
    (tmp_path / "performance_insights.json").write_text("{bad json", encoding="utf-8")
    assert pw._load(str(tmp_path)) == {}
    (tmp_path / "performance_insights.json").write_text("[1, 2]", encoding="utf-8")
    assert pw._load(str(tmp_path)) == {}


def test_load_valid_dict(tmp_path):
    _write_insights(tmp_path, {"with_metrics": 3})
    assert pw._load(str(tmp_path)) == {"with_metrics": 3}


# ---------------------------------------------------------------------------
# load_weights
# ---------------------------------------------------------------------------

def test_load_weights_no_project_is_defaults():
    result = pw.load_weights(None)
    assert result["weights"] == dict(pw.DEFAULT_WEIGHTS)
    assert result["basis"] == "defaults"
    assert result["title_boost"] == 0.0
    assert result["duration_bonus"] == 0.0
    assert result["samples"] == 0


def test_load_weights_missing_file_needs_samples(tmp_path):
    result = pw.load_weights(str(tmp_path))
    assert result["basis"] == "defaults"
    assert result["needed_samples"] == 3
    assert result["samples"] == 0


def test_load_weights_too_few_samples(tmp_path):
    _write_insights(tmp_path, {"with_metrics": 2, "correlations": {}})
    result = pw.load_weights(str(tmp_path))
    assert result["basis"] == "defaults"
    assert result["samples"] == 2
    assert result["needed_samples"] == 1
    assert result["weights"] == dict(pw.DEFAULT_WEIGHTS)


def test_load_weights_full_flow(tmp_path):
    _write_insights(tmp_path, {
        "with_metrics": 10,
        "correlations": {
            "hook_strength": {
                "vs_views": 0.5,
                "vs_hook_retention": 0.8,
                "vs_hook_retention_significant": True,
            },
            "clarity_score": {"vs_views": -0.4, "vs_views_significant": False},
            "title_quality_score": {"vs_views": 0.6},
            "duration": {"vs_views": -0.3},
        },
    })
    result = pw.load_weights(str(tmp_path))
    assert result["basis"] == "performance_insights"
    assert result["samples"] == 10
    assert result["confidence"] == 1.0
    # Hook judged by hook-retention, shift clamped to +0.10.
    assert result["shifted"] == [{
        "feature": "hook_strength", "component": "hook", "r": 0.8,
        "signal": "hook_retention", "significant": True, "shift": 0.1}]
    # Non-significant clarity correlation is suppressed, not applied.
    assert result["suppressed"] == [{
        "feature": "clarity_score", "component": "clarity", "r": -0.4,
        "signal": "views", "reason": "ci_includes_zero"}]
    assert result["title_boost"] == 0.12   # 0.2 * 0.6
    assert result["duration_bonus"] == -0.06  # 0.2 * -0.3
    # Weight mass stays at 1.0 (renormalised after the shift).
    assert sum(result["weights"].values()) == pytest.approx(1.0, abs=0.001)
    assert result["weights"]["hook"] > pw.DEFAULT_WEIGHTS["hook"]


def test_load_weights_small_sample_scales_shift(tmp_path):
    _write_insights(tmp_path, {
        "with_metrics": 3,
        "correlations": {"hook_strength": {"vs_views": 0.5}},
    })
    result = pw.load_weights(str(tmp_path))
    assert result["confidence"] == 0.6  # 3 / 5
    # shift = 0.15 * 0.5 * 0.6 = 0.045
    assert result["shifted"][0]["shift"] == 0.045
    assert result["needed_samples"] == 2


def test_load_weights_tiny_shift_is_ignored(tmp_path):
    _write_insights(tmp_path, {
        "with_metrics": 10,
        "correlations": {"hook_strength": {"vs_views": 0.05}},
    })
    result = pw.load_weights(str(tmp_path))
    assert result["shifted"] == []  # 0.15 * 0.05 = 0.0075 < 0.01


def test_load_weights_weak_title_and_duration_ignored(tmp_path):
    _write_insights(tmp_path, {
        "with_metrics": 10,
        "correlations": {
            "title_quality_score": {"vs_views": 0.1},
            "duration": {"vs_views": 0.1},
        },
    })
    result = pw.load_weights(str(tmp_path))
    assert result["title_boost"] == 0.0
    assert result["duration_bonus"] == 0.0


def test_load_weights_suppressed_title_and_duration(tmp_path):
    _write_insights(tmp_path, {
        "with_metrics": 10,
        "correlations": {
            "title_quality_score": {"vs_views": 0.6, "vs_views_significant": False},
            "duration": {"vs_views": 0.6, "vs_views_significant": False},
        },
    })
    result = pw.load_weights(str(tmp_path))
    assert result["title_boost"] == 0.0
    assert result["duration_bonus"] == 0.0
    assert len(result["suppressed"]) == 2
    assert {row["feature"] for row in result["suppressed"]} == {
        "title_quality_score", "duration"}


def test_load_weights_style_learning_rides_along(tmp_path):
    payload = dict(INSIGHTS_WITH_STYLE)
    payload["with_metrics"] = 5
    _write_insights(tmp_path, payload)
    result = pw.load_weights(str(tmp_path))
    assert result["style"]["hook_type"] == {"question": 2.0}
    assert result["confidence"] == 1.0


def test_load_weights_malformed_file_falls_back(tmp_path):
    (tmp_path / "performance_insights.json").write_text("{bad", encoding="utf-8")
    result = pw.load_weights(str(tmp_path))
    assert result["basis"] == "defaults"
    assert result["weights"] == dict(pw.DEFAULT_WEIGHTS)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def test_main_prints_defaults_json(tmp_path, capsys):
    assert pw.main(["--project", str(tmp_path)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["basis"] == "defaults"
    assert out["weights"]["hook"] == 0.2


def test_main_missing_project_arg_exits():
    with pytest.raises(SystemExit):
        pw.main([])
