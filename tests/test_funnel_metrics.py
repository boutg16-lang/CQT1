# -*- coding: utf-8 -*-
"""Round 2 item 6b — per-Short funnel metrics.

Pure-logic tests: injectable fetchers, honest ``None`` for unmeasured
signals, funnel scores, the publish-history baseline round-trip and the
performance_loop/performance_weights integration.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import funnel_metrics as fm
from scripts import performance_weights as pw


def _write_history(project, events):
    path = os.path.join(project, "publish_history.jsonl")
    with open(path, "w", encoding="utf-8") as handle:
        for event in events:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")


def _project(tmp_path, events):
    project = tmp_path / "proj"
    project.mkdir()
    _write_history(str(project), events)
    return str(project)


def test_funnel_rows_compute_source_delta_and_score(tmp_path):
    project = _project(
        tmp_path,
        [
            {
                "status": "uploaded",
                "video_id": "short1",
                "title": "شورت أول",
                "url": "https://youtu.be/short1",
                "source_video_url": "https://youtu.be/srcVid98765",
                "source_video_id": "srcVid98765",
                "source_views_at_publish": 1000,
                "topic": "test",
            }
        ],
    )
    report = fm.funnel_rows(
        project,
        fetch_metrics=lambda ids: {
            "short1": {"views": 420, "subscribers_gained": 7}
        },
        fetch_source_views=lambda vid: 1200 if vid == "srcVid98765" else None,
        fetch_related=lambda vid: 33 if vid == "short1" else None,
    )
    assert report["published_count"] == 1
    row = report["rows"][0]
    assert row["short_views"] == 420
    assert row["related_clicks"] == 33
    assert row["source_before"] == 1000
    assert row["source_after"] == 1200
    assert row["source_delta"] == 200
    assert row["subscribers_gained"] == 7
    assert row["funnel_score"] is not None and row["funnel_score"] > 0.5
    assert row["missing"] == []
    assert report["totals"]["source_delta"] == 200
    assert report["totals"]["subscribers_gained"] == 7
    assert report["unavailable_reason"] == ""


def test_funnel_rows_honest_when_oauth_missing(tmp_path):
    """No fetchers wired → every signal is None with an explicit reason."""

    def fail_source(video_id):
        raise RuntimeError("OAuth token file not found")

    project = _project(
        tmp_path,
        [
            {
                "status": "uploaded",
                "video_id": "short2",
                "title": "بدون أوث",
                "source_video_id": "srcVid98765",
            }
        ],
    )
    report = fm.funnel_rows(
        project,
        fetch_metrics=lambda ids: {},
        fetch_source_views=fail_source,
        fetch_related=fail_source,
    )
    row = report["rows"][0]
    assert row["short_views"] is None
    assert row["related_clicks"] is None
    assert row["source_after"] is None
    assert row["source_delta"] is None
    assert row["subscribers_gained"] is None
    assert row["funnel_score"] is None
    assert set(row["missing"]) == {
        "short_views",
        "related_clicks",
        "source_delta",
        "subscribers_gained",
    }
    assert "OAuth" in report["unavailable_reason"]


def test_funnel_rows_without_baseline_leaves_delta_none(tmp_path):
    """A Short published before the baseline capture shows an honest gap."""
    project = _project(
        tmp_path,
        [
            {
                "status": "uploaded",
                "video_id": "short3",
                "title": "قديم",
                "source_video_id": "srcVid98765",
            }
        ],
    )
    report = fm.funnel_rows(
        project,
        fetch_metrics=lambda ids: {"short3": {"views": 10}},
        fetch_source_views=lambda vid: 555,
        fetch_related=lambda vid: 0,
    )
    row = report["rows"][0]
    assert row["source_before"] is None
    assert row["source_after"] == 555
    assert row["source_delta"] is None
    assert "source_delta" in row["missing"]


def test_funnel_rows_ignores_non_uploaded_events(tmp_path):
    project = _project(
        tmp_path,
        [
            {"status": "failed", "video_id": "bad1"},
            {"status": "uploaded", "video_id": ""},
        ],
    )
    report = fm.funnel_rows(
        project,
        fetch_metrics=lambda ids: {},
        fetch_source_views=lambda vid: None,
        fetch_related=lambda vid: None,
    )
    assert report["rows"] == []
    assert report["published_count"] == 0


def test_funnel_score_prefers_channel_pulling_shorts():
    base = {
        "short_views": 0,
        "source_before": 0,
        "source_after": 0,
    }
    puller = dict(base, source_delta=500, related_clicks=80, subscribers_gained=30)
    idle = dict(base, source_delta=0, related_clicks=0, subscribers_gained=0)
    assert fm.funnel_score(puller) > 0.7
    assert fm.funnel_score(idle) == 0.0
    partial = dict(base, source_delta=None, related_clicks=40, subscribers_gained=None)
    assert fm.funnel_score(partial) is not None


def test_baseline_recorded_in_publish_history(tmp_path):
    """publish_history.record stores the funnel baseline keys verbatim."""
    from webui import publish_history

    project = str(tmp_path / "proj2")
    os.makedirs(project, exist_ok=True)
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"fake video bytes")
    publish_history.record(
        project,
        platform="youtube",
        video_path=str(clip),
        title="شورت قمع",
        result={
            "status": "uploaded",
            "video_id": "shortBase",
            "url": "https://youtu.be/shortBase",
        },
        extra={
            "source_video_url": "https://youtu.be/srcVid98765",
            "source_video_id": "srcVid98765",
            "source_views_at_publish": 4321,
        },
    )
    event = publish_history.find_success(
        project, platform="youtube", video_path=str(clip)
    )
    assert event is not None
    assert int(event["source_views_at_publish"]) == 4321
    assert event["source_video_id"] == "srcVid98765"
    report = fm.funnel_rows(
        project,
        fetch_metrics=lambda ids: {"shortBase": {"views": 90}},
        fetch_source_views=lambda vid: 4500,
        fetch_related=lambda vid: 5,
    )
    row = report["rows"][0]
    assert row["source_before"] == 4321
    assert row["source_delta"] == 179


def test_performance_weights_learn_funnel_categories(tmp_path):
    """Funnel buckets flow through content_insights into style bonuses."""
    from scripts import performance_loop as pl

    project = _project(
        tmp_path,
        [
            {
                "status": "uploaded",
                "video_id": "vidA",
                "title": "A",
                "topic": "اقتصاد",
                "source_video_id": "s1",
                "source_views_at_publish": 0,
            },
            {
                "status": "uploaded",
                "video_id": "vidB",
                "title": "B",
                "topic": "اقتصاد",
                "source_video_id": "s2",
                "source_views_at_publish": 0,
            },
            {
                "status": "uploaded",
                "video_id": "vidC",
                "title": "C",
                "topic": "اقتصاد",
                "source_video_id": "s3",
                "source_views_at_publish": 0,
            },
            {
                "status": "uploaded",
                "video_id": "vidD",
                "title": "D",
                "topic": "اقتصاد",
                "source_video_id": "s4",
                "source_views_at_publish": 0,
            },
        ],
    )

    def fetch_related(vid):
        return {"vidA": 0, "vidB": 120, "vidC": 0, "vidD": 0}.get(vid)

    funnel = fm.funnel_rows(
        project,
        fetch_metrics=lambda ids: {
            "vidA": {"views": 300},
            "vidB": {"views": 100},
            "vidC": {"views": 280},
            "vidD": {"views": 140},
        },
        fetch_source_views=lambda vid: {
            "s1": 200,
            "s2": 0,
            "s3": 150,
            "s4": 0,
        }.get(vid),
        fetch_related=fetch_related,
    )
    measured = [
        {
            "video_id": "vidA",
            "title": "A",
            "content": {"topic": "اقتصاد"},
            "metrics": {"views": 300, "retention": 40},
        },
        {
            "video_id": "vidB",
            "title": "B",
            "content": {"topic": "اقتصاد"},
            "metrics": {"views": 100, "retention": 60},
        },
        {
            "video_id": "vidC",
            "title": "C",
            "content": {"topic": "اقتصاد"},
            "metrics": {"views": 280, "retention": 55},
        },
        {
            "video_id": "vidD",
            "title": "D",
            "content": {"topic": "اقتصاد"},
            "metrics": {"views": 140, "retention": 50},
        },
    ]
    for item in measured:
        row = next(r for r in funnel["rows"] if r["video_id"] == item["video_id"])
        bucket = pl._funnel_bucket(row["source_delta"])
        if bucket:
            item["content"]["funnel_source_delta"] = bucket
    content = pl._content_insights(measured)
    rows = (content.get("categories") or {}).get("funnel_source_delta") or []
    assert rows, "funnel bucket must become a learnable category"
    values = {row["value"]: row for row in rows}
    assert "zero" in values and "high" in values
    assert values["high"]["avg_views"] > values["zero"]["avg_views"]
    bonuses = pw.style_bonuses({"content_insights": content})
    assert "funnel_source_delta" in bonuses
    assert bonuses["funnel_source_delta"].get("high", 0) > 0
