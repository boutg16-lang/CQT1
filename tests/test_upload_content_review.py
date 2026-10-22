import pytest

from scripts import upload_content_review as review


def transcript(text="حديث محايد عن الطقس"):
    return [
        {"start": 0.0, "end": 20.0, "text": text},
        {"start": 35.0, "end": 55.0, "text": "تفاصيل إضافية عن الموضوع"},
        {"start": 70.0, "end": 80.0, "text": "نهاية المقطع"},
    ]


def safe_verdicts(windows):
    return {
        item["index"]: {"violation": False, "reason": ""}
        for item in windows
    }


def test_review_windows_overlap_and_cover_complete_transcript():
    windows = review.build_review_windows(transcript(), duration=80)

    assert len(windows) == 2
    assert windows[0]["start_time"] == 0
    assert windows[0]["end_time"] == 45
    assert windows[1]["start_time"] == 40
    assert "حديث محايد" in windows[0]["text"]
    assert "نهاية المقطع" in windows[1]["text"]


def test_review_fails_when_transcript_is_missing_or_truncated():
    with pytest.raises(ValueError, match="no usable speech"):
        review.build_review_windows([], duration=30)
    with pytest.raises(ValueError, match="complete-review limit"):
        review.build_review_windows(
            [{"start": 0, "end": 10, "text": "x" * 1401}], duration=10)


def test_complete_clean_review_is_clear_and_binds_media_and_metadata():
    result = review.review_transcript(
        transcript(), duration=80, title="عنوان", caption="وصف",
        backend="mock", reviewer=safe_verdicts, video_sha256="abc123")

    assert result["status"] == "clear"
    assert result["review_complete"] is True
    assert result["video_sha256"] == "abc123"
    assert result["metadata_sha256"] == review.metadata_fingerprint("عنوان", "وصف")


def test_local_hate_signal_blocks_even_if_contextual_reviewer_passes():
    result = review.review_transcript(
        transcript("اقتل المسلمين"), duration=80,
        backend="mock", reviewer=safe_verdicts)

    assert result["status"] == "blocked"
    assert result["blocked_windows"]


def test_ai_violation_blocks_and_incomplete_review_raises():
    result = review.review_transcript(
        transcript(), duration=80, backend="mock",
        reviewer=lambda windows: {
            **safe_verdicts(windows),
            0: {"violation": True, "reason": "hate speech"},
        })
    assert result["status"] == "blocked"
    assert result["ai_violations"] == [0]

    with pytest.raises(ValueError, match="incomplete"):
        review.review_transcript(
            transcript(), duration=80, backend="mock",
            reviewer=lambda _windows: {})
