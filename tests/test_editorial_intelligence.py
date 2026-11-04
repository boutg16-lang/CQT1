from scripts.editorial_intelligence import build_editorial_package, prepare_candidates


def test_dialogue_cues_add_explainable_small_bonus():
    items = [{"text": "علاش هذا القرار؟ بصح عندي الدليل. الخلاصة واضحة.", "score": 80}]
    result = prepare_candidates(items)[0]
    assert result["dialogue_cue_bonus"] > 0
    assert result["editorial_review_score"] >= 80
    assert "human_context_review_required" in result["editorial_review_flags"]
    assert "editorial_review_score" not in items[0]


def test_pipeline_returns_selected_and_excluded_with_review_notice():
    items = [
        {"title": "سؤال", "topic": "سياسة", "text": "لماذا تغير القرار؟ الخلاصة واضحة.", "score": 90},
        {"title": "رياضة", "topic": "رياضة", "text": "هدف رائع في المباراة انتهى اللقاء.", "score": 75},
    ]
    result = build_editorial_package(items, limit=2)
    assert result["engine"] == "OUSSAMA Editorial Intelligence"
    assert result["policy"] == "decision_support_only"
    assert len(result["selected"]) == 2
    assert result["review_notice"]
    assert result["selected"][0]["dialogue_intelligence"]["cues"]


def test_empty_input_is_valid():
    result = build_editorial_package([])
    assert result["selected"] == []
    assert result["summary"]["input_count"] == 0


def test_editorial_pipeline_attaches_reviewable_moment_window():
    result = prepare_candidates([{
        "start": 12, "end": 20,
        "text": "علاش تغير القرار؟ بصح عندي الدليل. الخلاصة واضحة.",
        "score": 70,
    }])[0]
    window = result["moment_window"]
    assert window["status"] == "suggested"
    assert window["suggested_start"] == 9.5
    assert window["suggested_end"] == 23.5
    assert "suggested_timing_requires_audio_review" in result["editorial_review_flags"]


def test_invalid_timestamps_are_flagged_for_review():
    result = prepare_candidates([{
        "start": "not-a-time", "end": 12, "text": "هذا شرح واضح ومفصل للموضوع."
    }])[0]
    assert result["moment_window"]["status"] == "invalid_timestamps"
    assert "timing_needs_review_or_missing" in result["editorial_review_flags"]
