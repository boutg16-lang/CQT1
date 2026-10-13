# -*- coding: utf-8 -*-
"""Regression tests for the v7.44 publish-hardening pass.

Covers six execution-reproduced defects (effective YT_PRIVACY privacy,
scheduled-publish lead time, timezone offset materialisation, batch upload
duplicates, variant-aware duplicate detection, dry-run default for ``--plan``)
plus the concurrency loss in ``publish_history.record``.
"""

import datetime as dt
import json
import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import batch_process, platform_variant, publish_scheduler  # noqa: E402
from webui import publish_history  # noqa: E402
from webui import publish_panel as pp  # noqa: E402


def _project(tmp_path, name="proj"):
    project = tmp_path / name
    (project / "final").mkdir(parents=True)
    (project / "cuts").mkdir()
    (project / "viral_segments.txt").write_text(json.dumps({
        "segments": [{"title": "First Title", "caption": "cap 1",
                      "start_time": 0, "end_time": 5}]}), encoding="utf-8")
    (project / "final" / "000_clip.mp4").write_bytes(b"clip-bytes-0")
    return str(project)


# ---------------------------------------------------------------------------
# Defect 1 — the effective privacy (YT_PRIVACY) must drive guard + upload
# ---------------------------------------------------------------------------

class _UploadCapture:
    def __init__(self, status="uploaded"):
        self.init_kwargs = None
        self.upload_kwargs = None
        self.instance = None
        self.video = None
        self.status = status

    def factory(self):
        capture = self

        class FakeUploader:
            def __init__(self, project_folder, **kwargs):
                capture.init_kwargs = kwargs
                capture.instance = self

            def upload(self, video_path, title, caption, hashtags, index=None, **kwargs):
                capture.upload_kwargs = kwargs
                capture.video = video_path
                return {"status": capture.status, "platform": "youtube",
                        "video_id": "VID1"}

        return FakeUploader


def _run_youtube_upload(monkeypatch, project, clip, *, env_privacy=None, **kwargs):
    monkeypatch.setattr(pp, "_audio_qc_upload_allowed", lambda *a: (True, ""))
    monkeypatch.setenv("VIRALCUTTER_UPLOAD_THUMBNAIL", "0")
    from scripts import content_guard
    monkeypatch.setattr(content_guard, "channel_status",
                        lambda *a, **k: {"locked": False, "count": 0})
    if env_privacy is None:
        monkeypatch.delenv("YT_PRIVACY", raising=False)
    else:
        monkeypatch.setenv("YT_PRIVACY", env_privacy)
    capture = _UploadCapture()
    import scripts.upload_gate as ug
    monkeypatch.setattr(ug, "UPLOADERS", {"youtube": capture.factory()})
    result = list(pp.stream_upload(project, "youtube", clip, "T", "C", [],
                                   False, "warn", **kwargs))
    return capture, result


def test_env_public_does_not_publicize_an_explicitly_private_request(tmp_path, monkeypatch):
    """YT_PRIVACY=public + default/private request must still upload private."""
    project = _project(tmp_path)
    clip = os.path.join(project, "final", "000_clip.mp4")
    capture, _updates = _run_youtube_upload(monkeypatch, project, clip,
                                            env_privacy="public")
    # Explicit private is forwarded, so the uploader cannot fall back to public.
    assert capture.instance.privacy_status == "private"
    assert capture.upload_kwargs.get("privacy_status") == "private"
    assert capture.init_kwargs.get("privacy_status") == "private"
    events = publish_history.load(project)
    assert events[-1]["privacy_status"] == "private"


def test_env_public_optin_requires_confirmation(tmp_path, monkeypatch):
    """With no explicit value, YT_PRIVACY=public is used *and* gated."""
    project = _project(tmp_path)
    clip = os.path.join(project, "final", "000_clip.mp4")
    capture, updates = _run_youtube_upload(monkeypatch, project, clip,
                                           env_privacy="public",
                                           privacy_status=None,
                                           public_confirm=False)
    assert capture.instance is None  # uploader never constructed
    assert any("تأكيد النشر العام" in line for line in updates)


def test_env_public_optin_confirmed_records_public(tmp_path, monkeypatch):
    project = _project(tmp_path)
    clip = os.path.join(project, "final", "000_clip.mp4")
    capture, _updates = _run_youtube_upload(monkeypatch, project, clip,
                                            env_privacy="public",
                                            privacy_status=None,
                                            public_confirm=True)
    assert capture.instance.privacy_status == "public"
    events = publish_history.load(project)
    assert events[-1]["privacy_status"] == "public"


def test_effective_privacy_helper_prefers_explicit_value(monkeypatch):
    monkeypatch.setenv("YT_PRIVACY", "public")
    assert pp.effective_privacy("private") == "private"
    assert pp.effective_privacy("Unlisted") == "unlisted"
    assert pp.effective_privacy(None) == "public"
    monkeypatch.delenv("YT_PRIVACY", raising=False)
    assert pp.effective_privacy(None) == "private"


# ---------------------------------------------------------------------------
# Defect 2 — real scheduled publishing uploads before the slot
# ---------------------------------------------------------------------------

def _scheduler_plan(tmp_path, offset_minutes, *, ok=True):
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"x")
    target = dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=offset_minutes)
    return {
        "ok": ok,
        "plan": [{"video_path": str(clip), "video_name": "clip.mp4",
                  "publish_at": target.isoformat(), "platform": "youtube"}],
    }, target


def test_run_daemon_waits_lead_time_before_slot(tmp_path, monkeypatch):
    plan, target = _scheduler_plan(tmp_path, 120)
    waited = {}

    def fake_wait(deadline, sleep=None):
        waited["deadline"] = deadline

    monkeypatch.setattr(publish_scheduler, "_wait_until", fake_wait)
    captured = {}

    def fake_batch(*args, **kwargs):
        captured.update(kwargs)
        captured["paths"] = args[2]
        yield "uploaded clip"

    monkeypatch.setattr("webui.publish_panel.stream_upload_batch", fake_batch)
    summary = publish_scheduler.run_daemon(plan, dry_run=False, lead_minutes=15)
    expected_deadline = target - dt.timedelta(minutes=15)
    assert abs((waited["deadline"] - expected_deadline).total_seconds()) < 1
    # publish_at stays the planned (future) slot, so YouTube publishes it then.
    assert captured["publish_at"] == plan["plan"][0]["publish_at"]
    assert summary["results"][0]["status"] == "uploaded"


def test_run_daemon_slot_within_lead_window_uploads_immediately(tmp_path, monkeypatch):
    plan, target = _scheduler_plan(tmp_path, 2)  # target - 15min is already past
    waited = []
    monkeypatch.setattr(publish_scheduler, "_wait_until",
                        lambda deadline, sleep=None: waited.append(deadline))
    captured = {}
    monkeypatch.setattr(
        "webui.publish_panel.stream_upload_batch",
        lambda *a, **k: captured.update(k) or iter(["uploaded"]))
    summary = publish_scheduler.run_daemon(plan, dry_run=False, lead_minutes=15)
    assert len(waited) == 1
    assert waited[0] < dt.datetime.now(dt.timezone.utc)
    assert captured["publish_at"] == plan["plan"][0]["publish_at"]
    assert summary["results"][0]["status"] == "uploaded"


def test_run_daemon_past_slot_is_missed_not_submitted(tmp_path, monkeypatch):
    plan, _target = _scheduler_plan(tmp_path, -5)

    def explode(*a, **k):
        raise AssertionError("must not submit a past publish_at")

    monkeypatch.setattr("webui.publish_panel.stream_upload_batch", explode)
    summary = publish_scheduler.run_daemon(plan, dry_run=False)
    assert summary["results"][0]["status"] == "missed"


def test_run_daemon_dry_run_unchanged(tmp_path, monkeypatch):
    plan, _target = _scheduler_plan(tmp_path, 120)
    monkeypatch.setattr(publish_scheduler, "_wait_until",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("dry run must not wait")))
    captured = {}
    monkeypatch.setattr(
        "webui.publish_panel.stream_upload_batch",
        lambda *a, **k: captured.update(k) or iter(["dry-run ok"]))
    summary = publish_scheduler.run_daemon(plan, dry_run=True)
    assert captured["publish_at"] is None
    assert summary["results"][0]["status"] == "dry_run"


# ---------------------------------------------------------------------------
# Defect 3 — timezone_offset_hours is applied when materialising slots
# ---------------------------------------------------------------------------

def _clips(tmp_path, n=1):
    paths = []
    for index in range(n):
        path = tmp_path / "clip{}.mp4".format(index)
        path.write_bytes(b"x")
        paths.append(str(path))
    return paths


def test_timezone_offset_shifts_local_hour_to_utc(tmp_path):
    plan = publish_scheduler.build_plan(_clips(tmp_path), user_hours=[21],
                                        timezone_offset_hours=3.0)
    assert plan["ok"] is True
    slot = dt.datetime.fromisoformat(plan["plan"][0]["publish_at"])
    assert slot.hour == 18  # 21:00 local (UTC+3) == 18:00 UTC
    assert slot.utcoffset() == dt.timedelta(0)
    assert plan["plan"][0]["hour"] == 18
    assert plan["timezone_offset_hours"] == 3.0


def test_timezone_offset_zero_matches_legacy_behaviour(tmp_path):
    plan = publish_scheduler.build_plan(_clips(tmp_path), user_hours=[21],
                                        timezone_offset_hours=0.0)
    slot = dt.datetime.fromisoformat(plan["plan"][0]["publish_at"])
    assert slot.hour == 21
    assert slot.utcoffset() == dt.timedelta(0)


def test_timezone_offset_negative(tmp_path):
    plan = publish_scheduler.build_plan(_clips(tmp_path), user_hours=[1],
                                        timezone_offset_hours=-5.0)
    slot = dt.datetime.fromisoformat(plan["plan"][0]["publish_at"])
    assert slot.hour == 6  # 01:00 local (UTC-5) == 06:00 UTC


# ---------------------------------------------------------------------------
# Defect 4 — batch_process --upload must not upload every source copy
# ---------------------------------------------------------------------------

class _BatchArgs:
    dry_run = True
    privacy = "private"


def test_batch_upload_uses_one_source_for_duplicate_clip(tmp_path, monkeypatch):
    project = tmp_path / "proj"
    for folder in ("final_polished", "final", "cuts"):
        (project / folder).mkdir(parents=True)
        (project / folder / "000_clip.mp4").write_bytes(b"same-segment")
    captured = {}

    def fake_batch(project_path, platform, video_paths, *args, **kwargs):
        captured["paths"] = list(video_paths)
        yield "uploaded clip"

    monkeypatch.setattr("webui.publish_panel.stream_upload_batch", fake_batch)
    result = batch_process.upload_project(str(project), _BatchArgs())
    assert result["ok"] is True
    assert len(captured["paths"]) == 1
    assert os.sep + "final_polished" + os.sep in captured["paths"][0]


def test_batch_upload_falls_back_to_next_non_empty_source(tmp_path, monkeypatch):
    project = tmp_path / "proj"
    (project / "final_polished").mkdir(parents=True)  # empty
    (project / "final").mkdir()
    (project / "cuts").mkdir()
    (project / "final" / "000_clip.mp4").write_bytes(b"final-copy")
    (project / "cuts" / "000_clip_original.mp4").write_bytes(b"cuts-copy")
    captured = {}

    def fake_batch(project_path, platform, video_paths, *args, **kwargs):
        captured["paths"] = list(video_paths)
        yield "uploaded clip"

    monkeypatch.setattr("webui.publish_panel.stream_upload_batch", fake_batch)
    batch_process.upload_project(str(project), _BatchArgs())
    assert captured["paths"] == [os.path.abspath(
        str(project / "final" / "000_clip.mp4"))]


# ---------------------------------------------------------------------------
# Defect 5 — a successful variant counts as a success for the original
# ---------------------------------------------------------------------------

def test_find_success_matches_variant_of_the_original(tmp_path):
    project = tmp_path / "proj"
    (project / "final").mkdir(parents=True)
    original = project / "final" / "000_clip.mp4"
    original.write_bytes(b"original-bytes")
    variant = project / "variants" / "000_clip__tiktok_7.mp4"
    variant.parent.mkdir()
    variant.write_bytes(b"variant-bytes")
    publish_history.record(project, platform="tiktok", video_path=str(variant),
                           title="T", result={"status": "uploaded", "video_id": "VV"},
                           extra={"variant_of": "000_clip.mp4"})
    prior = publish_history.find_success(str(project), platform="tiktok",
                                         video_path=str(original))
    assert prior is not None
    assert prior["video_id"] == "VV"
    # A different platform must not be matched through variant_of.
    assert publish_history.find_success(str(project), platform="youtube",
                                        video_path=str(original)) is None


def test_find_success_ignores_failed_variant_and_keeps_fingerprint_matching(tmp_path):
    project = tmp_path / "proj"
    (project / "final").mkdir(parents=True)
    original = project / "final" / "000_clip.mp4"
    original.write_bytes(b"original-bytes")
    variant = project / "variants" / "000_clip__tiktok_1.mp4"
    variant.parent.mkdir()
    variant.write_bytes(b"variant-bytes")
    publish_history.record(project, platform="tiktok", video_path=str(variant),
                           title="T", error=RuntimeError("boom"),
                           extra={"variant_of": "000_clip.mp4"})
    assert publish_history.find_success(str(project), platform="tiktok",
                                        video_path=str(original)) is None
    publish_history.record(project, platform="tiktok", video_path=str(original),
                           title="T", result={"status": "uploaded", "video_id": "O1"})
    prior = publish_history.find_success(str(project), platform="tiktok",
                                         video_path=str(original))
    assert prior["video_id"] == "O1"


def test_previous_target_attempts_counts_variant_for_original(tmp_path):
    project = tmp_path / "proj"
    (project / "final").mkdir(parents=True)
    original = project / "final" / "000_clip.mp4"
    original.write_bytes(b"original-bytes")
    events = [{"platform": "tiktok", "status": "uploaded",
               "video": "000_clip__tiktok_7.mp4", "variant_of": "000_clip.mp4"}]
    assert platform_variant._previous_target_attempts(
        str(project), str(original), "tiktok", events) == 1


# ---------------------------------------------------------------------------
# Defect 6 — publish_scheduler --plan is dry-run unless --live is given
# ---------------------------------------------------------------------------

def _plan_file(tmp_path):
    path = tmp_path / "plan.json"
    path.write_text(json.dumps({"ok": True, "plan": []}), encoding="utf-8")
    return str(path)


def test_plan_defaults_to_dry_run(tmp_path, monkeypatch):
    captured = {}
    monkeypatch.setattr(publish_scheduler, "run_daemon",
                        lambda plan, **kwargs: captured.update(kwargs) or {"ok": True})
    assert publish_scheduler.main(["--plan", _plan_file(tmp_path)]) == 0
    assert captured["dry_run"] is True
    assert captured["lead_minutes"] == publish_scheduler.DEFAULT_LEAD_MINUTES


def test_plan_live_flag_opts_in(tmp_path, monkeypatch):
    captured = {}
    monkeypatch.setattr(publish_scheduler, "run_daemon",
                        lambda plan, **kwargs: captured.update(kwargs) or {"ok": True})
    assert publish_scheduler.main(["--plan", _plan_file(tmp_path), "--live"]) == 0
    assert captured["dry_run"] is False


def test_plan_dry_run_flag_wins_over_live(tmp_path, monkeypatch):
    captured = {}
    monkeypatch.setattr(publish_scheduler, "run_daemon",
                        lambda plan, **kwargs: captured.update(kwargs) or {"ok": True})
    publish_scheduler.main(["--plan", _plan_file(tmp_path), "--live", "--dry-run"])
    assert captured["dry_run"] is True


# ---------------------------------------------------------------------------
# Secondary defect — publish_history.record must not lose concurrent events
# ---------------------------------------------------------------------------

def test_record_persists_events_under_concurrency(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    clip = project / "clip.mp4"
    clip.write_bytes(b"x")

    def write(index):
        publish_history.record(
            project, platform="youtube", video_path=str(clip),
            title="event-{}".format(index),
            result={"status": "uploaded", "video_id": "v{}".format(index)})

    threads = [threading.Thread(target=write, args=(i,)) for i in range(60)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    rows = publish_history.load(str(project), limit=1000)
    assert len(rows) == 60
    assert {row["title"] for row in rows} == {"event-{}".format(i) for i in range(60)}


def test_record_appends_without_losing_existing_events(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    clip = project / "clip.mp4"
    clip.write_bytes(b"x")
    publish_history.record(project, platform="youtube", video_path=str(clip),
                           title="first", result={"status": "uploaded"})
    publish_history.record(project, platform="youtube", video_path=str(clip),
                           title="second", result={"status": "uploaded"})
    rows = publish_history.load(str(project))
    assert [row["title"] for row in rows] == ["first", "second"]
