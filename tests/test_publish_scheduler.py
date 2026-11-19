# -*- coding: utf-8 -*-
"""Direct tests for scripts/publish_scheduler.py (publish planning)."""

import datetime as dt
import json
import os
import sys
import types

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import publish_scheduler


@pytest.fixture
def videos(tmp_path):
    paths = []
    for name in ("clip_a.mp4", "clip_b.mp4", "clip_c.mp4"):
        path = tmp_path / name
        path.write_bytes(b"fake")
        paths.append(str(path))
    return paths


def _plan(videos, **kwargs):
    return publish_scheduler.build_plan(videos, start_at="2030-01-07T00:00:00Z", **kwargs)


class TestLoadMeasuredHours:
    def test_none_folder_returns_empty(self):
        assert publish_scheduler.load_measured_hours(None) == []

    def test_missing_file_returns_empty(self, tmp_path):
        assert publish_scheduler.load_measured_hours(str(tmp_path)) == []

    def test_malformed_json_returns_empty(self, tmp_path):
        (tmp_path / "performance_insights.json").write_text("{not json", encoding="utf-8")
        assert publish_scheduler.load_measured_hours(str(tmp_path)) == []

    def test_non_dict_json_returns_empty(self, tmp_path):
        (tmp_path / "performance_insights.json").write_text("[1, 2]", encoding="utf-8")
        assert publish_scheduler.load_measured_hours(str(tmp_path)) == []

    def test_non_list_hours_returns_empty(self, tmp_path):
        (tmp_path / "performance_insights.json").write_text(
            '{"best_hours": 17}', encoding="utf-8")
        assert publish_scheduler.load_measured_hours(str(tmp_path)) == []

    def test_hours_normalised_sorted_and_deduped(self, tmp_path):
        (tmp_path / "performance_insights.json").write_text(
            '{"best_hours": [17, 25, 9.5, "x", 17, 0]}', encoding="utf-8")
        assert publish_scheduler.load_measured_hours(str(tmp_path)) == [0, 1, 9, 17]


class TestBuildPlan:
    def test_no_videos_returns_error(self):
        result = publish_scheduler.build_plan([])
        assert result == {"ok": False, "error": "no valid video files", "plan": []}

    def test_missing_files_are_filtered_out(self, tmp_path):
        result = publish_scheduler.build_plan([str(tmp_path / "ghost.mp4")])
        assert result["ok"] is False
        assert result["error"] == "no valid video files"

    def test_invalid_start_at_returns_error(self, videos):
        result = publish_scheduler.build_plan(videos, start_at="not-a-date")
        assert result == {"ok": False, "error": "invalid start_at", "plan": []}

    def test_happy_path_youtube_weekday_windows(self, videos):
        # 2030-01-07 is a Monday; youtube weekday window is 17:00-21:00.
        result = _plan(videos[:2])
        assert result["ok"] is True
        assert result["count"] == 2
        assert result["platform"] == "youtube"
        assert [p["hour"] for p in result["plan"]] == [17, 18]
        assert [p["day"] for p in result["plan"]] == ["2030-01-07", "2030-01-07"]
        assert result["plan"][0]["video_name"] == "clip_a.mp4"
        assert result["plan"][0]["publish_at"].endswith("+00:00")
        assert result["plan"][0]["video_path"] == os.path.abspath(videos[0])

    def test_weekend_windows(self, videos):
        # 2030-01-12 is a Saturday; youtube weekend windows are 10-13 and 17-21.
        result = publish_scheduler.build_plan(videos[:2], start_at="2030-01-12T00:00:00Z")
        assert [p["hour"] for p in result["plan"]] == [10, 11]

    def test_user_hours_override_windows(self, videos):
        result = _plan(videos[:2], user_hours=[9])
        assert [p["hour"] for p in result["plan"]] == [9, 9]
        assert [p["day"] for p in result["plan"]] == ["2030-01-07", "2030-01-08"]

    def test_measured_hours_override_windows(self, videos):
        result = _plan(videos[:1], measured_hours=[20])
        assert result["plan"][0]["hour"] == 20

    def test_timezone_offset_shifts_slots(self, videos):
        result = _plan(videos[:1], timezone_offset_hours=2.0)
        assert result["plan"][0]["hour"] == 15  # local 17:00 -> UTC 15:00
        assert result["timezone_offset_hours"] == 2.0

    def test_unknown_platform_falls_back_to_youtube(self, videos):
        result = _plan(videos[:1], platform="dailymotion")
        assert result["plan"][0]["hour"] == 17

    def test_tiktok_windows(self, videos):
        result = _plan(videos[:1], platform="tiktok")
        assert result["plan"][0]["hour"] == 12

    def test_more_clips_than_daily_slots_spills_to_next_days(self, videos, tmp_path):
        extra = []
        for i in range(3):
            path = tmp_path / "extra_{}.mp4".format(i)
            path.write_bytes(b"fake")
            extra.append(str(path))
        result = _plan(videos + extra)  # 6 clips, 4 weekday slots per day
        assert result["count"] == 6
        assert [p["day"] for p in result["plan"]] == ["2030-01-07"] * 4 + ["2030-01-08"] * 2
        assert [p["hour"] for p in result["plan"]] == [17, 18, 19, 20, 17, 18]

    def test_naive_start_at_treated_as_utc(self, videos):
        result = publish_scheduler.build_plan(videos[:1], start_at="2030-01-07T00:00:00")
        assert result["plan"][0]["hour"] == 17

    def test_slots_in_the_past_are_skipped(self, videos):
        # Start Sunday 22:00: every Sunday slot has passed, so the first
        # slot is Monday's 17:00 window.
        result = publish_scheduler.build_plan(videos[:1], start_at="2030-01-06T22:00:00Z")
        assert result["plan"][0]["day"] == "2030-01-07"
        assert result["plan"][0]["hour"] == 17


class TestSavePlan:
    def test_writes_json_and_returns_path(self, tmp_path, videos):
        plan = _plan(videos[:1])
        path = publish_scheduler.save_plan(plan, str(tmp_path / "proj"))
        assert path == str(tmp_path / "proj" / "publish_schedule.json")
        assert json.loads(open(path, encoding="utf-8").read())["count"] == 1


class TestWaitUntil:
    def test_past_target_returns_immediately(self):
        calls = []
        publish_scheduler._wait_until(dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=1),
                                      sleep=calls.append)
        assert calls == []


def _stub_publish_panel(monkeypatch, stream_fn):
    fake_pp = types.ModuleType("webui.publish_panel")
    fake_pp.stream_upload_batch = stream_fn
    fake_webui = types.ModuleType("webui")
    fake_webui.publish_panel = fake_pp
    monkeypatch.setitem(sys.modules, "webui", fake_webui)
    monkeypatch.setitem(sys.modules, "webui.publish_panel", fake_pp)


class TestRunDaemon:
    def test_invalid_plan_returns_error(self):
        assert publish_scheduler.run_daemon({"ok": False, "error": "nope"}) == {
            "ok": False, "error": "nope"}

    def test_dry_run_calls_uploader_with_dry_run_flag(self, videos, monkeypatch):
        seen = {}

        def fake_stream(folder, platform, paths, dry_run, *args, **kwargs):
            seen.update({"folder": folder, "platform": platform, "paths": paths,
                         "dry_run": dry_run, "publish_at": kwargs.get("publish_at")})
            return ["uploaded clip_a.mp4"]

        _stub_publish_panel(monkeypatch, fake_stream)
        progress = []
        plan = _plan(videos[:1])
        summary = publish_scheduler.run_daemon(
            plan, dry_run=True, progress=lambda *a: progress.append(a))
        assert summary["ok"] is True
        assert summary["dry_run"] is True
        assert summary["results"][0]["status"] == "dry_run"
        assert "uploaded clip_a.mp4" in summary["results"][0]["detail"]
        assert seen["dry_run"] is True
        assert seen["publish_at"] is None  # dry-run never passes a real publish_at
        assert ("scheduled", "clip_a.mp4", plan["plan"][0]["publish_at"]) in progress
        assert ("done", "clip_a.mp4", "dry_run") in progress

    def test_upload_failure_is_reported_not_raised(self, videos, monkeypatch):
        def boom(*args, **kwargs):
            raise RuntimeError("auth expired")

        _stub_publish_panel(monkeypatch, boom)
        summary = publish_scheduler.run_daemon(_plan(videos[:1]), dry_run=True)
        assert summary["results"][0]["status"] == "failed"
        assert "auth expired" in summary["results"][0]["detail"]

    def test_past_slot_is_missed_in_live_mode(self, videos, monkeypatch):
        calls = []

        def fake_stream(*args, **kwargs):
            calls.append(True)
            return []

        _stub_publish_panel(monkeypatch, fake_stream)
        plan = publish_scheduler.build_plan(videos[:1], start_at="2020-01-01T00:00:00Z")
        assert plan["ok"] is True
        summary = publish_scheduler.run_daemon(plan, dry_run=False)
        assert summary["results"][0]["status"] == "missed"
        assert calls == []  # no upload attempted for a missed slot

    def test_invalid_lead_minutes_falls_back_to_default(self, videos, monkeypatch):
        _stub_publish_panel(monkeypatch, lambda *a, **k: [])
        summary = publish_scheduler.run_daemon(_plan(videos[:1]), dry_run=True,
                                               lead_minutes="bogus")
        assert summary["ok"] is True

    def test_progress_none_is_fine(self, videos, monkeypatch):
        _stub_publish_panel(monkeypatch, lambda *a, **k: ["ok"])
        summary = publish_scheduler.run_daemon(_plan(videos[:1]), dry_run=True)
        assert summary["results"][0]["status"] == "dry_run"


class TestMain:
    def test_no_videos_or_plan_returns_2(self, monkeypatch, capsys):
        monkeypatch.setattr(sys, "argv", ["publish_scheduler"])
        assert publish_scheduler.main() == 2
        assert "provide --videos or --plan" in capsys.readouterr().out

    def test_build_plan_from_cli(self, videos, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(sys, "argv", ["publish_scheduler", "--videos", videos[0],
                                          "--start-at", "2030-01-07T00:00:00Z",
                                          "--project", str(tmp_path / "proj")])
        assert publish_scheduler.main() == 0
        plan = json.loads(capsys.readouterr().out.split("plan saved: ")[-1].split("\n", 1)[-1])
        assert plan["count"] == 1
        assert (tmp_path / "proj" / "publish_schedule.json").exists()

    def test_missing_videos_from_cli_returns_1(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(sys, "argv", ["publish_scheduler", "--videos",
                                          str(tmp_path / "ghost.mp4")])
        assert publish_scheduler.main() == 1

    def test_plan_file_executes_dry_run(self, videos, tmp_path, monkeypatch, capsys):
        _stub_publish_panel(monkeypatch, lambda *a, **k: ["simulated"])
        plan_path = tmp_path / "plan.json"
        plan_path.write_text(json.dumps(_plan(videos[:1])), encoding="utf-8")
        monkeypatch.setattr(sys, "argv", ["publish_scheduler", "--plan", str(plan_path)])
        assert publish_scheduler.main() == 0
        summary = json.loads(capsys.readouterr().out)
        assert summary["results"][0]["status"] == "dry_run"
