# -*- coding: utf-8 -*-
"""v8.6 — no weight moves without evidence.

A Spearman point estimate from five clips is not proof. These tests pin the
bootstrap interval, the rule that an interval containing zero suppresses the
shift, and the transparency of listing suppressed shifts instead of hiding
them.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import analytics
from scripts import performance_loop as pl
from scripts import performance_weights as pw


def _project(tmp_path, hooks, views, count=None):
    project = tmp_path / "proj"
    project.mkdir()
    count = count or len(hooks)
    scorecard = {"segments": [
        {"index": index, "hook_strength": hooks[index]}
        for index in range(count)
    ]}
    (project / "risk_scorecard.json").write_text(json.dumps(scorecard), encoding="utf-8")
    events = [{
        "timestamp": "2026-10-{:02d}T10:00:00Z".format(index + 1),
        "platform": "youtube", "video": "{:03d}_clip.mp4".format(index),
        "title": "مقطع {}".format(index), "status": "uploaded",
        "video_id": "vid{}".format(index),
    } for index in range(count)]
    (project / "publish_history.jsonl").write_text(
        "\n".join(json.dumps(event, ensure_ascii=False) for event in events) + "\n",
        encoding="utf-8")
    return str(project), {
        "vid{}".format(index): {"views": views[index], "retention": 40.0,
                                "avg_view_duration": 10.0, "likes": 0,
                                "shares": 0, "subscribers_gained": 0}
        for index in range(count)
    }


def test_spearman_ci_excludes_zero_for_a_clean_relation():
    pairs = [(index, index * 2 + 1) for index in range(8)]
    low, high = pl._spearman_ci(pairs)
    assert low is not None and high is not None
    assert low > 0.0
    assert high >= low


def test_spearman_ci_includes_zero_for_noise():
    pairs = [(1, 5), (2, 1), (3, 4), (4, 2), (5, 3), (6, 6)]
    low, high = pl._spearman_ci(pairs)
    assert low is not None and high is not None
    assert low <= 0.0 <= high


def test_spearman_ci_needs_four_points():
    assert pl._spearman_ci([(1, 1), (2, 2), (3, 3)]) == (None, None)


def test_analyze_marks_a_clean_correlation_as_significant(tmp_path, monkeypatch):
    hooks = [10, 20, 30, 40, 50, 60, 70, 80]
    views = [100, 200, 300, 400, 500, 600, 700, 800]
    project, metrics = _project(tmp_path, hooks, views)
    monkeypatch.setattr(pl, "_fetch_real_metrics",
                        lambda ids, project_folder=None: metrics)
    report = pl.analyze(project, fetch_live=True)
    hook = report["correlations"]["hook_strength"]
    assert hook["vs_views"] == 1.0
    assert hook["vs_views_significant"] is True
    assert hook["vs_views_ci"][0] > 0
    assert "significance" in report["metric_notes"]


def test_analyze_marks_a_noisy_correlation_as_not_significant(tmp_path, monkeypatch):
    hooks = [10, 90, 20, 80, 30, 70, 40]
    views = [100, 200, 300, 400, 500, 600, 700]
    project, metrics = _project(tmp_path, hooks, views)
    monkeypatch.setattr(pl, "_fetch_real_metrics",
                        lambda ids, project_folder=None: metrics)
    report = pl.analyze(project, fetch_live=True)
    hook = report["correlations"]["hook_strength"]
    assert hook["vs_views_significant"] is False
    low, high = hook["vs_views_ci"]
    assert low <= 0 <= high


def test_markdown_labels_an_insignificant_correlation(tmp_path, monkeypatch):
    hooks = [10, 90, 20, 80, 30, 70, 40]
    views = [100, 200, 300, 400, 500, 600, 700]
    project, metrics = _project(tmp_path, hooks, views)
    monkeypatch.setattr(pl, "_fetch_real_metrics",
                        lambda ids, project_folder=None: metrics)
    report = pl.analyze(project, fetch_live=True)
    text = open(pl.write_markdown(project, report), encoding="utf-8").read()
    assert "CI [" in text
    assert "غير دال" in text


def test_weights_do_not_move_on_an_insignificant_correlation(tmp_path):
    project = tmp_path / "w"
    project.mkdir()
    (project / "performance_insights.json").write_text(json.dumps({
        "with_metrics": 6,
        "correlations": {"hook_strength": {
            "vs_views": 0.6, "vs_views_significant": False,
            "vs_views_ci": [-0.2, 0.9]}},
    }), encoding="utf-8")
    weights = pw.load_weights(str(project))
    assert weights["shifted"] == []
    assert weights["suppressed"][0]["component"] == "hook"
    assert weights["suppressed"][0]["reason"] == "ci_includes_zero"
    assert weights["weights"]["hook"] == pw.DEFAULT_WEIGHTS["hook"]


def test_weights_move_on_a_significant_correlation(tmp_path):
    project = tmp_path / "w"
    project.mkdir()
    (project / "performance_insights.json").write_text(json.dumps({
        "with_metrics": 6,
        "correlations": {"hook_strength": {
            "vs_views": 0.6, "vs_views_significant": True,
            "vs_views_ci": [0.1, 0.9]}},
    }), encoding="utf-8")
    weights = pw.load_weights(str(project))
    assert len(weights["shifted"]) == 1
    assert weights["shifted"][0]["significant"] is True
    assert weights["weights"]["hook"] > pw.DEFAULT_WEIGHTS["hook"]


def test_legacy_insights_without_a_ci_keep_their_old_behaviour(tmp_path):
    project = tmp_path / "w"
    project.mkdir()
    (project / "performance_insights.json").write_text(json.dumps({
        "with_metrics": 6,
        "correlations": {"hook_strength": {"vs_views": 0.6}},
    }), encoding="utf-8")
    weights = pw.load_weights(str(project))
    assert len(weights["shifted"]) == 1
    assert weights["suppressed"] == []


def test_hook_metric_ignores_the_constant_first_bucket():
    points = [{"ratio": 0.0, "watch": 1.0}]
    points += [{"ratio": index / 100.0, "watch": 0.70} for index in range(2, 11)]
    points += [{"ratio": index / 100.0, "watch": 0.60} for index in range(11, 20)]
    points += [{"ratio": index / 100.0, "watch": 0.50} for index in range(20, 30)]
    summary = analytics.summarize_retention(points)
    assert summary["hook_ratio"] == 0.7


def test_title_and_duration_boosts_are_gated_too(tmp_path):
    project = tmp_path / "w"
    project.mkdir()
    (project / "performance_insights.json").write_text(json.dumps({
        "with_metrics": 6,
        "correlations": {
            "title_quality_score": {"vs_views": 0.8, "vs_views_significant": False},
            "duration": {"vs_views": 0.7, "vs_views_significant": False},
        },
    }), encoding="utf-8")
    weights = pw.load_weights(str(project))
    assert weights["title_boost"] == 0.0
    assert weights["duration_bonus"] == 0.0
    components = {item["component"] for item in weights["suppressed"]}
    assert components == {"title", "duration"}
