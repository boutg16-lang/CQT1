# -*- coding: utf-8 -*-
"""Tests for round 2 item 8 — hook fingerprints, published-hook refusal and
continuation proposals."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import hook_fingerprints as hf
from scripts import query_targeting as qt


def _event(title, transcript, status="uploaded"):
    return {
        "title": title,
        "transcript_text": transcript,
        "status": status,
    }


def test_normalize_hook_unifies_hamza_and_diacritics():
    a = hf.normalize_hook("السَّلاَمُ عَلَيْكُم ورحمة الله")
    b = hf.normalize_hook("السلام عليكم ورحمة الله")
    assert a == b
    assert "  " not in a


def test_hook_fingerprint_first_words_only():
    fp = hf.hook_fingerprint(
        "لما رفضوا الدفع انفجر الموقف بالكامل. في هذا الفيديو", words=4
    )
    assert fp == "لما رفضوا الدفع انفجر"


def test_published_hooks_success_only(tmp_path):
    events = [
        _event("منشور", "لما رفضوا الدفع انفجر الموقف. بقية القصة."),
        _event(
            "مجدول", "لما رفضوا الدفع انفجر الموقف. بقية القصة.", status="scheduled"
        ),
        _event("فاشل", "لما رفضوا الدفع انفجر الموقف. بقية القصة.", status="failed"),
    ]
    hooks = hf.published_hooks(str(tmp_path), events=events)
    assert len(hooks) == 1


def test_event_without_transcript_is_not_fingerprinted(tmp_path):
    hooks = hf.published_hooks(str(tmp_path), events=[_event("عنوان فقط", "")])
    assert hooks == {}


def test_segment_hook_conflict_matches_normalized_quote(tmp_path):
    hooks = hf.published_hooks(
        str(tmp_path),
        events=[_event("منشور", "لما رفضوا الدفع انفجر الموقف. بقية القصة.")],
    )
    conflict_fp, published = hf.segment_hook_conflict(
        {"transcript_text": "لمَا رَفَضُوا الدَّفْع انفجر الموقف. جملة ثانية."}, hooks
    )
    assert published and published["title"] == "منشور"
    assert conflict_fp == hf.hook_fingerprint("لما رفضوا الدفع انفجر الموقف")


def test_segment_without_conflict_passes(tmp_path):
    hooks = hf.published_hooks(
        str(tmp_path),
        events=[_event("منشور", "لما رفضوا الدفع انفجر الموقف. بقية القصة.")],
    )
    fp, published = hf.segment_hook_conflict(
        {"transcript_text": "قضية السبت تكشف تفاصيل جديدة كليا. المزيد هنا."}, hooks
    )
    assert published == {}


def test_apply_published_hook_guard_blocks_and_reports():
    hooks = {"لما رفضوا الدفع انفجر الموقف كله": {"title": "المنشور"}}
    blocked = []
    kept = hf.apply_published_hook_guard(
        [
            {
                "title": "أ",
                "transcript_text": "لما رفضوا الدفع انفجر الموقف كله. ثانية.",
            },
            {"title": "ب", "transcript_text": "قضية السبت تكشف تفاصيل جديدة. المزيد."},
        ],
        hooks,
        blocked=blocked,
    )
    assert [s["title"] for s in kept] == ["ب"]
    assert len(blocked) == 1
    assert blocked[0]["blocked_reason"].startswith("opening hook already published")
    assert blocked[0]["hook_fingerprint"] == "لما رفضوا الدفع انفجر الموقف كله"


def _segment(transcript, start=10.0, end=40.0):
    return {
        "title": "مقطع",
        "start_time": start,
        "end_time": end,
        "transcript_text": transcript,
    }


def test_continuation_offers_second_clip_when_two_related_ideas():
    seg = _segment(
        "لما رفضوا الدفع انفجر الموقف بالكامل. ومصطفى بونيف تبون رفض التوقيع على "
        "الاتفاق مع رفض الدفع. وهذا ما فعله بعد رفض الدفع في الاجتماع."
    )
    proposals = qt.continuation_candidates(seg, "رفض الدفع")
    assert proposals, "a related later sentence should produce a proposal"
    first = proposals[0]
    assert first["origin"] == "continuation"
    assert first["start_time"] > seg["start_time"] - 0.001
    assert first["transcript_text"] != seg["transcript_text"].split(".")[0]
    assert first["query_relevance"] >= qt.DEFAULT_MIN_RELEVANCE
    assert first["hook_score"] >= 55.0
    assert first.get("recommended_title")


def test_continuation_drops_unrelated_or_weak_sentences():
    seg = _segment("لما رفضوا الدفع انفجر الموقف بالكامل. الطقس جميل اليوم في الجزائر.")
    proposals = qt.continuation_candidates(seg, "رفض الدفع")
    assert proposals == []


def test_apply_target_query_reports_continuations():
    seg = _segment(
        "مصطفى بونيف تبون رفض الدفع من البداية. ومصطفى بونيف تبون رفض الدفع "
        "مرة ثانية في الاجتماع الأخير أمام الجميع."
    )
    result = {}
    kept = qt.apply_target_query([seg], "رفض الدفع", result)
    base = [s for s in kept if s.get("origin") != "continuation"]
    assert base and kept[0]["query"] == "رفض الدفع"
    report = result["target_query"]
    assert report["kept"] >= 1
    for cont in report["continuations"]:
        assert cont["continuation_of"] == (
            base[0].get("recommended_title") or base[0].get("title")
        )


def test_continuation_not_added_when_single_idea():
    seg = _segment("مصطفى بونيف تبون رفض الدفع من البداية حتى النهاية.")
    result = {}
    qt.apply_target_query([seg], "رفض الدفع", result)
    assert result["target_query"]["continuations"] == []
