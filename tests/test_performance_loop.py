import json

from scripts import performance_loop


def _history(tmp_path, rows):
    (tmp_path / "publish_history.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8"
    )


def test_empty_history_reports_nothing_linked(tmp_path):
    report = performance_loop.analyze(str(tmp_path), fetch_live=False)
    assert report["published_count"] == 0
    assert report["insights"]


def test_links_published_events_with_scorecard_features(tmp_path, monkeypatch):
    (tmp_path / "risk_scorecard.json").write_text(
        json.dumps(
            {
                "segments": [{"index": 2, "duration": 45, "hook_strength": 0.8}],
            }
        ),
        encoding="utf-8",
    )
    _history(
        tmp_path,
        [
            {
                "video_id": "abc",
                "status": "uploaded",
                "video": "clip_002.mp4",
                "title": "T2",
                "platform": "youtube",
                "timestamp": "2026-08-01T18:00:00Z",
            },
            {"video_id": "", "status": "uploaded", "video": "no_id.mp4"},
            {"video_id": "xyz", "status": "failed", "video": "clip_003.mp4"},
        ],
    )
    monkeypatch.setattr(
        performance_loop,
        "_fetch_real_metrics",
        lambda ids, project_folder=None: {
            "abc": {"views": 120, "avg_view_duration": 33.0, "likes": 5, "shares": 1}
        },
    )
    report = performance_loop.analyze(str(tmp_path), fetch_live=True)
    assert report["published_count"] == 1
    assert report["with_metrics"] == 1
    clip = report["clips"][0]
    assert clip["video_id"] == "abc"
    assert clip["features"]["duration"] == 45
    assert any(
        "correlation" in i or "no feature" in i or "meaningful" in i
        for i in report["insights"]
    )


def test_insights_reported_when_metrics_missing(tmp_path, monkeypatch):
    _history(
        tmp_path,
        [
            {
                "video_id": "abc",
                "status": "uploaded",
                "video": "clip_001.mp4",
                "title": "T",
                "platform": "youtube",
                "timestamp": "2026-08-01T18:00:00Z",
            }
        ],
    )
    monkeypatch.setattr(
        performance_loop, "_fetch_real_metrics", lambda ids, project_folder=None: {}
    )
    report = performance_loop.analyze(str(tmp_path), fetch_live=True)
    assert report["published_count"] == 1
    assert report["with_metrics"] == 0
    assert report["insights"]


def test_corrupt_scorecard_logs_and_keeps_published_history(tmp_path, caplog):
    (tmp_path / "risk_scorecard.json").write_text("{broken", encoding="utf-8")
    _history(
        tmp_path,
        [
            {
                "video_id": "abc",
                "status": "uploaded",
                "video": "clip_001.mp4",
                "title": "T",
                "platform": "youtube",
            }
        ],
    )

    published = performance_loop._published_videos(str(tmp_path))

    assert len(published) == 1
    assert any(
        "Could not read risk scorecard" in record.message for record in caplog.records
    )


def test_analytics_failure_is_visible_but_report_still_builds(
    tmp_path, caplog, monkeypatch
):
    """feat/pro-cutting item 5: a broken Analytics backend must not kill the
    loop — the report still builds and the failure is logged with a reason."""
    import logging

    import scripts.analytics as analytics_mod

    (tmp_path / "risk_scorecard.json").write_text("{}", encoding="utf-8")
    _history(
        tmp_path,
        [
            {
                "video_id": "vid5",
                "status": "uploaded",
                "video": "clip_005.mp4",
                "title": "T5",
                "platform": "youtube",
            }
        ],
    )

    def _boom():
        raise RuntimeError("no oauth token")

    monkeypatch.setattr(analytics_mod, "_build_services", _boom)
    with caplog.at_level(logging.WARNING, logger="scripts.performance_loop"):
        report = performance_loop.analyze(str(tmp_path), fetch_live=True)

    assert report["with_metrics"] == 0
    assert report["published_count"] == 1
    assert any("Analytics unavailable" in record.message for record in caplog.records)


def test_analytics_filter_commajoin_and_ctr_query(tmp_path, monkeypatch):
    """Round 2 item 7 — live-verify the 39bac5b fix with a fake API service.

    Captures the exact query kwargs _fetch_real_metrics sends and asserts:
    * one video dimension value per batch, comma-joined (video==a,b,c) —
      the semicolon bug would send video==a;b;c and silently return zero rows;
    * the CTR column set is requested first and the query degrades to the
      base metrics when the API rejects the extra columns.
    """
    captured = []

    class _FakeRequest:
        def __init__(self, kwargs, fail_ctr=False):
            self._kwargs = kwargs
            self._fail_ctr = fail_ctr

        def execute(self):
            captured.append(self._kwargs)
            if self._fail_ctr and "impressions" in self._kwargs["metrics"]:
                raise RuntimeError("invalidMetricsCombination")
            return {
                "rows": [
                    ["abc", 120, 33.0, 40.0, 5, 1, 2, 900, 42],
                ]
            }

    class _FakeReports:
        def __init__(self, fail_ctr=False):
            self._fail_ctr = fail_ctr

        def query(self, **kwargs):
            return _FakeRequest(kwargs, self._fail_ctr)

    class _FakeAnalytics:
        def __init__(self):
            self.reports_called = 0

        def reports(self):
            self.reports_called += 1
            return _FakeReports(fail_ctr=self.reports_called == 1)

    fake = _FakeAnalytics()
    import scripts.analytics as analytics_mod
    monkeypatch.setattr(
        analytics_mod, "_build_services", lambda: (fake, object())
    )
    metrics = performance_loop._fetch_real_metrics(["abc"], str(tmp_path))
    # 39bac5b: the filter is comma-joined per dimension, never semicolons.
    filters = [kwargs["filters"] for kwargs in captured]
    # CTR attempt + degraded retry for the same batch: identical filter each
    # time, and the metrics column set degrades instead of failing.
    assert len(filters) == 3  # CTR attempt + degraded retry + retention
    assert filters == ["video==abc", "video==abc", "video==abc"]
    assert "impressions" in captured[0]["metrics"]
    assert "impressions" not in captured[1]["metrics"]
    assert all(";" not in f for f in filters)
    # The batch row is parsed: CTR present when the API accepted the columns.
    row = metrics["abc"]
    assert row["views"] == 120
    assert row["impressions"] == 900
    assert row["clicks"] == 42
    assert row["ctr"] == round(42 / 900 * 100.0, 2)
