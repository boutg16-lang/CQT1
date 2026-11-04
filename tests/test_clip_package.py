import pytest

from scripts.clip_package import plan_package


def test_package_limits_repeated_topics_and_keeps_best_scores():
    items = [
        {"title": "A", "topic": "politics", "selection_score": 91, "text": "قرار جديد يغير الوضع"},
        {"title": "B", "topic": "politics", "selection_score": 88, "text": "اجتماع مهم وقرار جديد"},
        {"title": "C", "topic": "sports", "selection_score": 82, "text": "هدف رائع في المباراة"},
        {"title": "D", "topic": "politics", "selection_score": 80, "text": "تصريح حول ملف آخر تماما"},
    ]
    result = plan_package(items, limit=5, max_per_topic=2)
    assert [x["title"] for x in result["selected"]] == ["A", "B", "C"]
    assert result["summary"]["selected_count"] == 3
    assert any(x["reason"] == "topic_cap" for x in result["excluded"])
    assert items[0].get("package_order") is None  # input is not mutated


def test_package_rejects_overlapping_same_source_clips():
    items = [
        {"title": "first", "source_id": "video1", "start": 10, "end": 30,
         "selection_score": 90, "text": "شرح تفاصيل المشروع الجديد"},
        {"title": "second", "source_id": "video1", "start": 25, "end": 45,
         "selection_score": 80, "text": "حديث مختلف تماما عن الاقتصاد"},
    ]
    result = plan_package(items, limit=5)
    assert len(result["selected"]) == 1
    assert result["excluded"][0]["reason"] == "source_time_overlap"


def test_package_rejects_near_duplicate_transcripts():
    items = [
        {"title": "first", "selection_score": 90,
         "text": "الحكومة تعلن اليوم عن قرار جديد يتعلق بالاقتصاد الوطني"},
        {"title": "second", "selection_score": 80,
         "text": "الحكومة تعلن اليوم عن قرار جديد يتعلق بالاقتصاد الوطني"},
    ]
    result = plan_package(items, limit=5, min_similarity=0.7)
    assert len(result["selected"]) == 1
    assert result["excluded"][0]["reason"] == "transcript_near_duplicate"


def test_package_validates_options():
    try:
        plan_package([], limit=0)
    except ValueError as exc:
        assert "limit" in str(exc)
    else:
        raise AssertionError("invalid limit must fail")


def test_overlapping_timestamps_without_source_identity_do_not_collide():
    items = [
        {"title": "first", "start": 10, "end": 30, "selection_score": 90,
         "text": "شرح تفاصيل المشروع الجديد"},
        {"title": "second", "start": 10, "end": 30, "selection_score": 80,
         "text": "حديث مختلف تماما عن الاقتصاد"},
    ]
    result = plan_package(items, limit=2, min_similarity=1.0)
    assert len(result["selected"]) == 2


def test_package_rejects_non_finite_or_negative_thresholds():
    with pytest.raises(ValueError, match="min_similarity"):
        plan_package([], min_similarity=float("nan"))
    with pytest.raises(ValueError, match="min_gap_seconds"):
        plan_package([], min_gap_seconds=-1)
    with pytest.raises(ValueError, match="min_gap_seconds"):
        plan_package([], min_gap_seconds=float("inf"))
    with pytest.raises(TypeError, match="segments"):
        plan_package(None)
