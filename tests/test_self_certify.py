# -*- coding: utf-8 -*-
"""Tests for the self-certification preflight (feat/pro-cutting item 7)."""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import self_certify as sc  # noqa: E402
from webui import publish_history as ph  # noqa: E402


def test_clean_transcript_certifies_none():
    report = sc.self_certify(
        "السلام عليكم. في هذا الفيديو نتحدث عن تاريخ المدينة وثقافتها.",
        title="تاريخ المدينة",
        caption="حلقة جديدة عن التاريخ",
    )
    assert report["clean"] is True
    assert report["recommended"]["contains_none"] is True
    assert report["recommended"]["contains_yes"] == []
    assert report["needs_manual_confirmation"] is False
    for name in sc.CATEGORIES:
        assert report["categories"][name]["contains"] is False


def test_violent_transcript_certifies_violence_category():
    report = sc.self_certify(
        "قالوا اذبحهم واقضي عليهم ان لم يدفعوا", title="تهديد بالقتل"
    )
    category = report["categories"]["violent_graphic"]
    assert category["contains"] is True
    assert category["confidence"] >= 0.8
    assert "اذبحهم" in category["signals"]
    assert report["recommended"]["contains_yes"] == ["violent_graphic"]
    assert report["needs_manual_confirmation"] is True
    assert report["clean"] is False


def test_report_is_json_safe():
    report = sc.self_certify("اذبحهم", title="عنوان")
    blob = json.dumps(report, ensure_ascii=False)
    assert json.loads(blob)["categories"]["violent_graphic"]["contains"] is True


def test_record_answer_attaches_studio_link():
    report = sc.self_certify("اذبحهم", title="عنوان")
    linked = sc.record_answer(report, "abc123XYZ_-")
    assert (
        linked["studio_url"] == "https://studio.youtube.com/video/abc123XYZ_-/edit"
    )


def test_publish_history_accepts_self_cert_extra(tmp_path):
    report = sc.self_certify("اذبحهم", title="عنوان")
    ok = ph.record(
        str(tmp_path),
        platform="youtube",
        video_path=str(tmp_path / "clip_001.mp4"),
        title="عنوان",
        result={"status": "uploaded", "video_id": "vid1"},
        extra={
            "self_cert": json.dumps(
                report, ensure_ascii=False, separators=(",", ":")
            )
        },
    )
    assert ok is True
    events = ph.load(str(tmp_path))
    assert events and events[0]["self_cert"]
    stored = json.loads(events[0]["self_cert"])
    assert stored["categories"]["violent_graphic"]["contains"] is True
