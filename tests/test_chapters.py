# -*- coding: utf-8 -*-
"""Tests for YouTube chapters generation (round 2, item 5)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import chapters as ch  # noqa: E402


def _segs():
    return [
        {
            "start_time": 12.0,
            "end_time": 40.0,
            "hook_text": "قضية السبت تكشف تفاصيل جديدة.",
        },
        {
            "start_time": 45.0,
            "end_time": 90.0,
            "hook_text": "لما رفضوا الدفع انفجر الموقف.",
        },
        {
            "start_time": 120.0,
            "end_time": 180.0,
            "hook_text": "الرد الرسمي وصل متأخراً.",
        },
        {"start_time": 240.0, "end_time": 300.0, "hook_text": "ماذا حدث بعد الأزمة؟"},
    ]


def test_build_chapters_first_at_zero_and_min_spacing():
    result = ch.build_chapters(_segs(), source_duration=310.0)
    assert result["reason"] == ""
    chapters = result["chapters"]
    assert chapters[0]["start"] == 0.0
    starts = [c["start"] for c in chapters]
    assert starts == sorted(starts)
    for before, after in zip(starts, starts[1:], strict=False):
        assert after - before >= ch.MIN_CHAPTER_SECONDS


def test_build_chapters_skips_short_source():
    result = ch.build_chapters(_segs(), source_duration=120.0)
    assert result["chapters"] == []
    assert "3 minutes" in result["reason"]


def test_build_chapters_needs_three_chapters():
    two = _segs()[:2]
    result = ch.build_chapters(two, source_duration=310.0)
    assert result["chapters"] == []
    assert "at least 3" in result["reason"]


def test_build_chapters_drops_dense_segments():
    dense = _segs() + [{"start_time": 46.0, "end_time": 100.0, "hook_text": "متكرر"}]
    result = ch.build_chapters(dense, source_duration=310.0)
    titles = [c["title"] for c in result["chapters"]]
    assert titles.count("متكرر") == 0


def test_chapters_block_and_description_append():
    result = ch.build_chapters(_segs(), source_duration=310.0)
    block = ch.chapters_block(result["chapters"])
    assert block.startswith("الفصول:")
    description = ch.description_with_chapters("وصف الفيديو", result["chapters"])
    assert description.startswith("وصف الفيديو")
    assert "00:00 قضية السبت" in description


def test_description_with_chapters_is_idempotent():
    result = ch.build_chapters(_segs(), source_duration=310.0)
    once = ch.description_with_chapters("وصف", result["chapters"])
    twice = ch.description_with_chapters(once, result["chapters"])
    assert once == twice


def test_write_project_chapters(tmp_path):
    result = ch.build_chapters(_segs(), source_duration=310.0)
    outcome = ch.write_project_chapters(str(tmp_path), result["chapters"])
    path = outcome["chapters_file"]
    assert os.path.isfile(path)
    content = open(path, encoding="utf-8").read()
    assert "00:00" in content
    assert ch.project_has_chapters(str(tmp_path)) is True


def test_write_project_chapters_empty_is_noop(tmp_path):
    outcome = ch.write_project_chapters(str(tmp_path), [])
    assert outcome == {} or not ch.project_has_chapters(str(tmp_path))


def test_format_ts_hours():
    assert ch.format_ts(0.0) == "00:00"
    assert ch.format_ts(75.0) == "01:15"
    assert ch.format_ts(3725.0) == "1:02:05"
