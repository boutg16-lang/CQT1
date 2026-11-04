import pytest

from scripts.viral_moment_intelligence import analyze_moments, suggest_moment_window


def test_adds_context_padding_without_changing_source_timestamps():
    source = {"start": 10, "end": 18, "text": "علاش؟ بصح عندي الدليل. الخلاصة واضحة."}
    result = suggest_moment_window(source)
    assert result["moment_window"]["suggested_start"] == 7.5
    assert result["moment_window"]["suggested_end"] == 21.5
    assert result["start"] == 10 and result["end"] == 18
    assert "revelation" in result["moment_window"]["importance_signals"]


def test_missing_timestamps_are_not_guessed():
    result = analyze_moments([{"text": "لماذا حدث ذلك؟"}])
    assert result["segments"][0]["moment_window"]["status"] == "needs_timestamps"
    assert result["summary"]["needs_timestamps_count"] == 1


def test_invalid_timestamp_and_padding_rejected():
    with pytest.raises(ValueError):
        suggest_moment_window({"start": 5, "end": 4, "text": "test"})
    with pytest.raises(ValueError):
        suggest_moment_window({"start": 1, "end": 2}, lead_in=-1)


def test_overlapping_or_negative_infinite_time_is_rejected():
    with pytest.raises(ValueError):
        suggest_moment_window({"start": float("nan"), "end": 2})
    with pytest.raises(ValueError, match="lead_in"):
        suggest_moment_window({"start": 1, "end": 2}, lead_in=float("nan"))
    with pytest.raises(TypeError, match="segment"):
        suggest_moment_window(None)
