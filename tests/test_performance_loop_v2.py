# -*- coding: utf-8 -*-
"""v8.4 — fair-window, rank-robust performance learning.

Pins the properties that make the loop trustworthy: rank correlation instead
of raw Pearson, a shared measurement window, retention as a second signal,
sample-size confidence, and an automatic refresh that never overwrites good
data with an empty analytics response.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import performance_loop as pl
from scripts import performance_weights as pw


def _project(tmp_path, count=6):
    project = tmp_path / "proj"
    project.mkdir()
    scorecard = {"segments": [
        {"index": index, "duration": 30 + index * 5,
         "hook_strength": 40 + index * 8,
         "narrative_completeness": 50 + index * 5,
         "clarity_score": 60 + index * 4,
         "novelty_score": 55 + index * 3,
         "selection_score": 60 + index * 5,
         "title_quality_score": 50 + index * 6}
        for index in range(count)
    ]}
    (project / "risk_scorecard.json").write_text(
        json.dumps(scorecard), encoding="utf-8")
    events = []
    for index in range(count):
        events.append({
            "timestamp": "2026-08-{:02d}T10:00:00Z".format(index + 1),
            "platform": "youtube",
            "video": "{:03d}_clip.mp4".format(index),
            "title": "عنوان {}".format(index),
            "status": "uploaded",
            "video_id": "vid{}".format(index),
            "topic": "topic-{}".format(index % 2),
            "angle": "lesson" if index % 2 else "story",
            "hook_type": "question" if index % 2 else "story",
        })
    (project / "publish_history.jsonl").write_text(
        "\n".join(json.dumps(event, ensure_ascii=False) for event in events) + "\n",
        encoding="utf-8")
    return str(project)


def _metrics(count=6):
    return {
        "vid{}".format(index): {
            "views": 100 + index * 90,
            "avg_view_duration": 10 + index * 2,
            "retention": 30 + index * 5,
            "likes": index * 3,
            "shares": index,
            "subscribers_gained": index,
        }
        for index in range(count)
    }


def test_spearman_is_robust_to_a_single_viral_outlier():
    # Perfectly monotonic feature/views relation except one huge outlier.
    pairs = [(1, 10), (2, 20), (3, 30), (4, 40), (5, 100000)]
    rho = pl._spearman(pairs)
    assert rho == 1.0
    # Pearson on raw values is dragged away from 1 by the same point.
    pearson = pl._pearson([(float(x), float(y)) for x, y in pairs])
    assert pearson < 0.9


def test_spearman_handles_ties():
    assert pl._spearman([(1, 10), (1, 10), (2, 20), (2, 20), (3, 30)]) > 0.8


def test_analyze_reports_the_shared_window_and_retention(tmp_path, monkeypatch):
    project = _project(tmp_path, 6)
    monkeypatch.setattr(pl, "_fetch_real_metrics", lambda ids: _metrics(6))
    report = pl.analyze(project, fetch_live=True)
    assert report["window_days"] == pl.DEFAULT_WINDOW_DAYS
    hook = report["correlations"]["hook_strength"]
    assert hook["method"] == "spearman"
    assert hook["samples"] == 6
    assert hook["vs_views"] is not None
    assert hook["vs_retention"] is not None
    assert report["content_insights"]["categories"]["hook_type"][0].get(
        "avg_retention") is not None
    text = "\n".join(report["insights"])
    assert "Spearman" in text
    assert "retention" in text


def test_window_days_is_env_tunable_and_clamped(tmp_path, monkeypatch):
    project = _project(tmp_path, 3)
    monkeypatch.setattr(pl, "_fetch_real_metrics", lambda ids: _metrics(3))
    monkeypatch.setenv("VIRALCUTTER_PERF_WINDOW_DAYS", "90")
    assert pl.analyze(project, fetch_live=True)["window_days"] == 90
    monkeypatch.setenv("VIRALCUTTER_PERF_WINDOW_DAYS", "1")
    assert pl.analyze(project, fetch_live=True)["window_days"] == pl.MIN_WINDOW_DAYS


def test_weights_shrink_small_samples(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    base = {
        "correlations": {"title_quality_score": {"vs_views": 0.6}},
    }
    for samples in (3, 5):
        payload = dict(base, with_metrics=samples)
        (project / "performance_insights.json").write_text(
            json.dumps(payload), encoding="utf-8")
        weights = pw.load_weights(str(project))
        if samples == 3:
            small = weights
        else:
            full = weights
    assert small["confidence"] == 0.6
    assert full["confidence"] == 1.0
    assert 0 < small["title_boost"] < full["title_boost"]
    assert small["needed_samples"] == 2
    assert full["needed_samples"] == 0


def test_auto_refresh_writes_reports_and_then_skips_fresh(tmp_path, monkeypatch):
    project = _project(tmp_path, 5)
    monkeypatch.setattr(pl, "_fetch_real_metrics", lambda ids: _metrics(5))
    first = pl.auto_refresh(project)
    assert first["refreshed"] is True
    assert os.path.exists(os.path.join(project, pl.REPORT_NAME))
    assert os.path.exists(os.path.join(project, pl.MARKDOWN_NAME))
    markdown = open(os.path.join(project, pl.MARKDOWN_NAME), encoding="utf-8").read()
    assert "Spearman" in markdown
    assert "ما ينجح على قناتك" in markdown

    second = pl.auto_refresh(project)
    assert second["refreshed"] is False
    assert second["reason"] == "fresh"


def test_refresh_due_forces_a_new_query(tmp_path, monkeypatch):
    project = _project(tmp_path, 5)
    calls = []

    def fetch(ids):
        calls.append(1)
        return _metrics(5)

    monkeypatch.setattr(pl, "_fetch_real_metrics", fetch)
    pl.auto_refresh(project)
    assert len(calls) == 1
    pl.auto_refresh(project)
    assert len(calls) == 1  # fresh, no second query
    pl.mark_refresh_due(project)
    assert os.path.exists(os.path.join(project, pl.DUE_NAME))
    pl.auto_refresh(project)
    assert len(calls) == 2
    assert not os.path.exists(os.path.join(project, pl.DUE_NAME))


def test_failed_analytics_never_overwrites_good_metrics(tmp_path, monkeypatch):
    project = _project(tmp_path, 4)
    monkeypatch.setattr(pl, "_fetch_real_metrics", lambda ids: _metrics(4))
    pl.auto_refresh(project, force=True)
    good = json.loads(open(os.path.join(project, pl.REPORT_NAME),
                           encoding="utf-8").read())
    assert good["with_metrics"] == 4

    # A later OAuth/network failure returns no metrics.
    monkeypatch.setattr(pl, "_fetch_real_metrics", lambda ids: {})
    status = pl.auto_refresh(project, force=True)
    assert status["refreshed"] is False
    assert status["reason"] == "kept_previous_metrics"
    kept = json.loads(open(os.path.join(project, pl.REPORT_NAME),
                           encoding="utf-8").read())
    assert kept["with_metrics"] == 4
    assert kept["generated_at"] == good["generated_at"]


def test_auto_refresh_without_published_videos_is_a_noop(tmp_path):
    project = tmp_path / "empty"
    project.mkdir()
    status = pl.auto_refresh(str(project))
    assert status["refreshed"] is False
    assert status["reason"] == "no_published_videos"


def test_style_bonus_shrinks_with_small_samples():
    content = {
        "with_metrics": 3,
        "content_insights": {
            "overall_avg_views": 500.0,
            "categories": {"hook_type": [
                {"value": "question", "samples": 3, "avg_views": 1000.0,
                 "delta_pct": 100.0}]},
        },
    }
    full = pw.style_bonuses(dict(content, with_metrics=5), 1.0)
    small = pw.style_bonuses(content, 0.6)
    assert full["hook_type"]["question"] == 3.0
    assert small["hook_type"]["question"] == 1.8
