# -*- coding: utf-8 -*-
"""v8.5 — per-video retention curves as a first-class learning signal.

Averages hide the shape of attention. These tests pin the curve math, the
per-video cache (bounded API calls), and the rule that the hook weight is
learned from early retention when that signal exists.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import analytics
from scripts import performance_loop as pl
from scripts import performance_weights as pw


def _curve_points():
    points = []
    for index in range(0, 101):
        ratio = index / 100.0
        if ratio < 0.50:
            watch = 0.90
        elif ratio < 0.55:
            watch = 0.60
        else:
            watch = 0.55
        points.append({"ratio": ratio, "watch": watch, "relative": 0.0})
    return points


def _project(tmp_path, count=6):
    project = tmp_path / "proj"
    project.mkdir()
    scorecard = {"segments": [
        {"index": index, "duration": 40 + index * 5,
         "hook_strength": 40 + index * 8,
         "narrative_completeness": 50 + index * 5,
         "clarity_score": 60 + index * 4,
         "novelty_score": 55 + index * 3,
         "selection_score": 60 + index * 5,
         "title_quality_score": 50 + index * 6}
        for index in range(count)
    ]}
    (project / "risk_scorecard.json").write_text(json.dumps(scorecard), encoding="utf-8")
    events = [{
        "timestamp": "2026-09-{:02d}T10:00:00Z".format(index + 1),
        "platform": "youtube", "video": "{:03d}_clip.mp4".format(index),
        "title": "مقطع {}".format(index), "status": "uploaded",
        "video_id": "vid{}".format(index),
        "hook_type": "question" if index % 2 else "story",
    } for index in range(count)]
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
            "retention_curve": {
                "hook_ratio": 0.60 + index * 0.05,
                "mid_ratio": 0.55, "end_ratio": 0.50,
                "max_drop_at": 0.40, "max_drop_pct": 10.0, "points": 100,
            },
        } for index in range(count)
    }


def test_summarize_retention_finds_hook_and_cliff():
    summary = analytics.summarize_retention(_curve_points())
    assert summary["hook_ratio"] == 0.9
    assert summary["mid_ratio"] is not None and summary["mid_ratio"] < 0.8
    assert summary["end_ratio"] == 0.55
    assert 0.49 <= summary["max_drop_at"] <= 0.52
    assert summary["max_drop_pct"] >= 10.0
    assert summary["points"] == 101


def test_summarize_retention_rejects_thin_or_malformed_curves():
    assert analytics.summarize_retention([]) is None
    assert analytics.summarize_retention(
        [{"ratio": 0.0, "watch": 1.0}] * 4) is None
    mixed = _curve_points()[:10] + [
        {"ratio": "bad", "watch": 1.0}, {"ratio": 0.5, "watch": -1.0},
        {"ratio": 2.0, "watch": 1.0}]
    summary = analytics.summarize_retention(mixed)
    assert summary is not None and summary["points"] == 10


class _FakeQuery:
    def __init__(self, rows, calls):
        self.rows = rows
        self.calls = calls

    def query(self, **kwargs):
        self.calls.append(kwargs)
        return self

    def execute(self):
        return {"rows": self.rows}


def test_fetch_retention_curve_queries_the_right_report():
    calls = []
    ya = type("YA", (), {"reports": lambda self: _FakeQuery(
        [[0.0, 1.0, 1.2], [0.1, 0.9, 1.1], [0.2, 0.88, 1.0],
         [0.3, 0.8, 0.9], [0.4, 0.79, 0.9], [0.5, 0.6, 0.7]], calls)})()
    curve = analytics.fetch_retention_curve(ya, "abc123", days=7)
    request = calls[0]
    assert request["dimensions"] == "elapsedVideoTimeRatio"
    assert request["metrics"] == "audienceWatchRatio,relativeRetentionPerformance"
    assert request["filters"] == "video==abc123"
    assert curve["video_id"] == "abc123"
    assert len(curve["points"]) == 6
    assert curve["summary"]["hook_ratio"] == 0.95


def test_attach_retention_curves_caches_per_video(tmp_path):
    project = str(tmp_path)
    calls = []

    def fetcher(video_id):
        calls.append(video_id)
        return {"summary": {"hook_ratio": 0.8, "mid_ratio": 0.7,
                            "end_ratio": 0.6, "max_drop_at": 0.4,
                            "max_drop_pct": 8.0, "points": 100}}

    metrics = {"a": {"views": 10}, "b": {"views": 20}}
    pl.attach_retention_curves(metrics, project, ["a", "b"], fetcher=fetcher)
    assert calls == ["a", "b"]
    assert metrics["a"]["retention_curve"]["hook_ratio"] == 0.8
    cache = json.loads((tmp_path / pl.RETENTION_CACHE_NAME).read_text(encoding="utf-8"))
    assert set(cache) == {"a", "b"}

    calls.clear()
    metrics2 = {"a": {"views": 10}, "b": {"views": 20}}
    pl.attach_retention_curves(metrics2, project, ["a", "b"], fetcher=fetcher)
    assert calls == []
    assert metrics2["b"]["retention_curve"]["hook_ratio"] == 0.8


def test_attach_retention_curves_refetches_stale_and_survives_failure(tmp_path):
    project = str(tmp_path)
    (tmp_path / pl.RETENTION_CACHE_NAME).write_text(json.dumps({
        "a": {"summary": {"hook_ratio": 0.5}, "fetched_ts": 0},
        "b": {"summary": {"hook_ratio": 0.5}, "fetched_ts": 0},
    }), encoding="utf-8")
    calls = []

    def fetcher(video_id):
        calls.append(video_id)
        if video_id == "b":
            raise RuntimeError("quota")
        return {"summary": {"hook_ratio": 0.75, "max_drop_pct": 4.0}}

    metrics = {"a": {}, "b": {}}
    pl.attach_retention_curves(metrics, project, ["a", "b"], fetcher=fetcher)
    assert calls == ["a", "b"]
    assert metrics["a"]["retention_curve"]["hook_ratio"] == 0.75
    assert "retention_curve" not in metrics["b"]


def test_attach_retention_curves_can_be_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv("VIRALCUTTER_RETENTION_CURVES", "0")
    calls = []
    metrics = {"a": {}}
    pl.attach_retention_curves(
        metrics, str(tmp_path), ["a"], fetcher=lambda vid: calls.append(vid) or {})
    assert calls == []
    assert "retention_curve" not in metrics["a"]


def test_analyze_correlates_hook_with_early_retention(tmp_path, monkeypatch):
    project = _project(tmp_path, 6)
    monkeypatch.setattr(pl, "_fetch_real_metrics",
                        lambda ids, project_folder=None: _metrics(6))
    report = pl.analyze(project, fetch_live=True)
    hook = report["correlations"]["hook_strength"]
    assert hook["vs_hook_retention"] == 1.0
    assert hook["hook_retention_samples"] == 6
    assert report["retention_curve"]["samples"] == 6
    assert any("Retention curve" in line for line in report["insights"])


def test_markdown_has_a_retention_section(tmp_path, monkeypatch):
    project = _project(tmp_path, 5)
    monkeypatch.setattr(pl, "_fetch_real_metrics",
                        lambda ids, project_folder=None: _metrics(5))
    status = pl.auto_refresh(project, force=True)
    text = open(status["markdown"], encoding="utf-8").read()
    assert "منحنى الاحتفاظ" in text
    assert "أكبر هبوط" in text


def test_hook_weight_prefers_early_retention(tmp_path):
    project = tmp_path / "weights"
    project.mkdir()
    payload = {
        "with_metrics": 5,
        "correlations": {
            "hook_strength": {"vs_views": -0.9, "vs_hook_retention": 0.8},
        },
    }
    (project / "performance_insights.json").write_text(
        json.dumps(payload), encoding="utf-8")
    weights = pw.load_weights(str(project))
    assert weights["shifted"][0]["component"] == "hook"
    assert weights["shifted"][0]["signal"] == "hook_retention"
    assert weights["shifted"][0]["r"] == 0.8
    assert weights["weights"]["hook"] > pw.DEFAULT_WEIGHTS["hook"]


def test_hook_weight_falls_back_to_views_without_a_curve(tmp_path):
    project = tmp_path / "weights"
    project.mkdir()
    payload = {"with_metrics": 5,
               "correlations": {"hook_strength": {"vs_views": -0.4}}}
    (project / "performance_insights.json").write_text(
        json.dumps(payload), encoding="utf-8")
    weights = pw.load_weights(str(project))
    assert weights["shifted"][0]["signal"] == "views"
    assert weights["weights"]["hook"] < pw.DEFAULT_WEIGHTS["hook"]


def test_flat_curves_do_not_report_a_fake_drop(tmp_path, monkeypatch):
    metrics = _metrics(4)
    for item in metrics.values():
        item["retention_curve"] = {
            "hook_ratio": 0.9, "mid_ratio": 0.9, "end_ratio": 0.9,
            "max_drop_at": None, "max_drop_pct": 0.0, "points": 100,
        }
    monkeypatch.setattr(pl, "_fetch_real_metrics",
                        lambda ids, project_folder=None: metrics)
    report = pl.analyze(_project(tmp_path, 4), fetch_live=True)
    assert report["retention_curve"]["worst"] == {}
    assert any("no sharp drop" in line for line in report["insights"])
    markdown = open(pl.write_markdown(report["project"], report),
                    encoding="utf-8").read()
    assert "لا هبوط حاد" in markdown
    assert "عند 0%" not in markdown
