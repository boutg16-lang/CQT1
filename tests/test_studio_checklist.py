# -*- coding: utf-8 -*-
"""Tests for the per-clip manual Studio checklist (round 2 item 1).

The checklist must NEVER pretend to automate steps that have no API
endpoint: the deep links point at Studio, the notes say why it is manual.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import studio_checklist as sc  # noqa: E402
from webui import publish_history  # noqa: E402


def test_build_checklist_covers_all_four_steps():
    items = sc.build_checklist(
        "vid1234567",
        related_video_id="srcVid98765",
        thumbnail_uploaded=True,
        source_video_url="https://youtube.com/watch?v=srcVid98765",
    )
    assert [i["key"] for i in items] == list(sc.CHECKLIST_KEYS)
    assert sc.CHECKLIST_KEYS == (
        "related_video",
        "self_assessment",
        "end_screen",
        "thumbnail",
    )
    related = items[0]
    assert related["done"] is False  # manual tick, never pre-marked
    assert related["url"] == sc.studio_video_url("srcVid98765")
    assert "no relatedvideoid" in related["note"].lower()
    # self-assessment points at the Short's own edit page
    assert items[1]["url"] == sc.studio_video_url("vid1234567")
    assert items[1]["done"] is False
    # end screen opens the editor (element picker)
    assert items[2]["url"] == sc.studio_video_url("vid1234567", editor=True)
    assert items[2]["done"] is False
    # thumbnail was uploaded via API, but search rendering is eyeball-only
    assert items[3]["done"] is True
    assert "search" in items[3]["note"].lower()


def test_roundtrip_json_and_markdown(tmp_path):
    items = sc.build_checklist("vid1234567")
    blob = sc.checklist_json(items)
    assert len(blob) < 4000  # publish_history extra cap
    parsed = sc.parse_checklist(blob)
    assert parsed == items
    md = sc.checklist_markdown(items)
    assert "قائمة الاستوديو" in md
    assert md.count("⬜") == 4
    assert sc.pending_count(items) == 4
    done_items = sc.mark_done(items, "self_assessment", True)
    assert sc.checklist_markdown(done_items).count("✅") == 1
    assert sc.pending_count(done_items) == 3


def test_update_history_checklist_appends_newest_state(tmp_path):
    video_id = "vid1234567"
    ok = publish_history.record(
        str(tmp_path),
        platform="youtube",
        video_path=str(tmp_path / "clip.mp4"),
        title="t",
        result={"status": "uploaded", "video_id": video_id, "url": "u"},
        extra={"studio_checklist": sc.checklist_json(sc.build_checklist(video_id))},
    )
    assert ok is True
    ok2 = sc.update_history_checklist(
        str(tmp_path), "youtube", video_id, "related_video", True
    )
    assert ok2 is True
    events = publish_history.load(str(tmp_path), limit=100)
    newest = sc.checklist_for_event(events[-1])
    assert [i for i in newest if i["key"] == "related_video"][0]["done"] is True
    assert sc.pending_count(newest) == 3
    # unknown key / unknown video -> no event appended
    assert (
        sc.update_history_checklist(str(tmp_path), "youtube", "nope", "self_assessment")
        is False
    )


def test_parse_rejects_garbage():
    assert sc.parse_checklist(None) == []
    assert sc.parse_checklist("") == []
    assert sc.parse_checklist("{not json") == []
    assert sc.parse_checklist({"a": 1}) == []
    assert sc.parse_checklist("[{}]") == [{}]


# ---------------------------------------------------------------------------
# Round 2 item 2 — post-upload verification (copyright / ad suitability)
# ---------------------------------------------------------------------------


def _write_history(project, events):
    path = os.path.join(str(project), "publish_history.jsonl")
    with open(path, "w", encoding="utf-8") as fh:
        for event in events:
            fh.write(json.dumps(event) + "\n")


def test_record_checks_anchors_on_upload_event(tmp_path):
    from scripts import verify_youtube as vy

    project = tmp_path / "proj"
    project.mkdir()
    _write_history(
        project,
        [
            {
                "timestamp": "2026-11-25T10:00:00Z",
                "platform": "youtube",
                "video": "clip.mp4",
                "title": "عنوان",
                "status": "uploaded",
                "video_id": "vid1234567",
                "url": "https://youtube.com/shorts/vid1234567",
            }
        ],
    )
    checks = vy.build_pending("vid1234567", reason="async")
    assert vy.record_checks(str(project), "youtube", "vid1234567", checks) is True
    events = publish_history.load(str(project))
    assert len(events) == 2
    stored = vy.parse_checks(events[-1].get(vy.HISTORY_KEY))
    assert stored is not None
    assert stored[vy.COPYRIGHT]["verdict"] == vy.VERDICT_PENDING
    assert stored[vy.AD_SUITABILITY]["verdict"] == vy.VERDICT_PENDING
    assert events[-1]["video_id"] == "vid1234567"


def test_record_checks_without_upload_event_is_noop(tmp_path):
    from scripts import verify_youtube as vy

    project = tmp_path / "proj"
    project.mkdir()
    _write_history(project, [])
    checks = vy.build_pending("vid1234567")
    assert vy.record_checks(str(project), "youtube", "vid1234567", checks) is False


def test_latest_checks_returns_newest(tmp_path):
    from scripts import verify_youtube as vy

    project = tmp_path / "proj"
    project.mkdir()
    first = vy.build_pending("vid1234567", reason="first")
    second = dict(first)
    second[vy.COPYRIGHT] = {
        "verdict": vy.VERDICT_NO_CLAIMS,
        "source": "youtubePartner.claims.list",
        "detail": "0 claim(s)",
    }
    _write_history(
        project,
        [
            {
                "timestamp": "2026-11-25T10:00:00Z",
                "platform": "youtube",
                "video": "clip.mp4",
                "title": "t",
                "status": "uploaded",
                "video_id": "vid1234567",
                vy.HISTORY_KEY: vy.checks_json(first),
            },
            {
                "timestamp": "2026-11-25T12:00:00Z",
                "platform": "youtube",
                "video": "clip.mp4",
                "title": "t",
                "status": "uploaded",
                "video_id": "vid1234567",
                vy.HISTORY_KEY: vy.checks_json(second),
            },
        ],
    )
    got = vy.latest_checks(str(project), platform="youtube", video_id="vid1234567")
    assert got[vy.COPYRIGHT]["verdict"] == vy.VERDICT_NO_CLAIMS


def test_fetch_verdicts_maps_partner_responses(monkeypatch):
    from scripts import verify_youtube as vy

    monkeypatch.setattr(vy, "_access_token", lambda token_path: "fake-access-token")

    def fake_get(url, token):
        if url.startswith(vy.DATA_API_BASE):
            return {
                "items": [
                    {
                        "status": {
                            "uploadStatus": "processed",
                            "privacyStatus": "public",
                        }
                    }
                ]
            }
        if "claims" in url:
            return {"items": [{"claimId": "c1"}]}
        if "rating" in url:
            return {"items": [{"rating": {"ytRating": "YT_RATING_LIMITED_ADS"}}]}
        raise AssertionError("unexpected url " + url)

    monkeypatch.setattr(vy, "_api_get_json", fake_get)
    checks = vy.fetch_verdicts("vid1234567", "token.json")
    assert checks[vy.COPYRIGHT]["verdict"] == vy.VERDICT_CLAIM_FOUND
    assert checks[vy.AD_SUITABILITY]["verdict"] == vy.VERDICT_LIMITED
    assert checks["data_api"]["upload_status"] == "processed"


def test_recheck_pending_updates_when_window_passed(tmp_path, monkeypatch):
    from scripts import verify_youtube as vy

    project = tmp_path / "proj"
    project.mkdir()
    stale = vy.build_pending("vid1234567", reason="initial")
    # force the recheck window into the past
    stale["recheck_at"] = "2026-01-01T00:00:00+00:00"
    _write_history(
        project,
        [
            {
                "timestamp": "2026-01-01T00:00:00Z",
                "platform": "youtube",
                "video": "clip.mp4",
                "title": "t",
                "status": "uploaded",
                "video_id": "vid1234567",
                vy.HISTORY_KEY: vy.checks_json(stale),
            }
        ],
    )
    fresh = vy.build_pending("vid1234567", reason="recheck")
    fresh[vy.COPYRIGHT] = {
        "verdict": vy.VERDICT_NO_CLAIMS,
        "source": "youtubePartner.claims.list",
        "detail": "0 claim(s)",
    }
    monkeypatch.setattr(vy, "fetch_verdicts", lambda vid, token_path: fresh)
    monkeypatch.setattr(
        vy,
        "_latest_checks_event",
        lambda project_path, platform, video_id: None,
    )
    token = tmp_path / "token.json"
    token.write_text("{}", encoding="utf-8")
    summary = vy.recheck_pending(str(project), token_path=str(token))
    assert summary["checked"] == 1
    assert summary["updated"] == 0  # anchor disabled in this test → no record
    assert summary["details"][0][vy.COPYRIGHT] == vy.VERDICT_NO_CLAIMS
