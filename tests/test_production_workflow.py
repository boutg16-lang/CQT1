from scripts.production_workflow import run_workflow


def test_workflow_returns_review_gate_and_does_not_auto_publish():
    result = run_workflow([
        {"id": "a", "start": 1, "end": 18, "text": "واش السبب؟ عندي الدليل والخلاصة واضحة.",
         "score": 90, "topic": "سياسة", "speaker_id": "S1"},
        {"id": "b", "start": 20, "end": 36, "text": "موضوع آخر فيه تفاصيل مهمة جدا ونقاش جديد.",
         "score": 80, "topic": "اقتصاد", "speaker_id": "S2"},
    ])
    assert result["summary"]["selected_count"] == 2
    assert result["summary"]["publish_approved_count"] == 0
    assert result["release_gate"]["automatic_publish_allowed"] is False
    assert all(x["publication_status"] == "human_review_required" for x in result["selected"])


def test_workflow_detects_continuous_speaker_context():
    result = run_workflow([
        {"id": "a", "start": 1, "end": 12, "text": "هذا موضوع أول مهم جدا للنقاش.",
         "score": 90, "topic": "أ", "speaker_id": "S1"},
        {"id": "b", "start": 13, "end": 25, "text": "وهذه تتمة كلام المتحدث في نفس النقاش.",
         "score": 80, "topic": "ب", "speaker_id": "S1"},
    ])
    second = next(x for x in result["selected"] if x["id"] == "b")
    assert second["speaker_turn_context"]["continues_previous_speaker"] is True


def test_workflow_rejects_bad_duration_configuration():
    try:
        run_workflow([], min_duration=20, max_duration=10)
    except ValueError:
        pass
    else:
        raise AssertionError("invalid duration range should fail")


def test_invalid_timestamp_candidate_is_reported_without_crashing():
    result = run_workflow([{
        "id": "bad", "start": "not-a-time", "end": 12,
        "text": "هذا شرح واضح ومفصل للموضوع.", "score": 100,
        "topic": "موضوع"
    }])
    selected = result["selected"][0]
    assert selected["moment_window"]["status"] == "invalid_timestamps"
    assert selected["final_validation"]["ok"] is False
    assert result["release_gate"]["status"] == "blocked"
