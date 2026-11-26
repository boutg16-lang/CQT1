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
    ok2 = sc.update_history_checklist(str(tmp_path), "youtube", video_id, "related_video", True)
    assert ok2 is True
    events = publish_history.load(str(tmp_path), limit=100)
    newest = sc.checklist_for_event(events[-1])
    assert [i for i in newest if i["key"] == "related_video"][0]["done"] is True
    assert sc.pending_count(newest) == 3
    # unknown key / unknown video -> no event appended
    assert sc.update_history_checklist(str(tmp_path), "youtube", "nope", "self_assessment") is False


def test_parse_rejects_garbage():
    assert sc.parse_checklist(None) == []
    assert sc.parse_checklist("") == []
    assert sc.parse_checklist("{not json") == []
    assert sc.parse_checklist({"a": 1}) == []
    assert sc.parse_checklist("[{}]") == [{}]
