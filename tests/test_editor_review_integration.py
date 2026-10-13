import json

from scripts import create_viral_segments as cvs


def _transcript():
    return [
        {"start": 0.0, "end": 10.0, "text": "مقدمة عادية عن الموضوع"},
        {"start": 11.0, "end": 25.0, "text": "ليش الناس تخاف؟ الجواب صادم فعلاً!"},
        {"start": 26.0, "end": 40.0, "text": "تفاصيل إضافية تكمّل القصة"},
        {"start": 45.0, "end": 60.0, "text": "خاتمة هادئة بلا لحظة قوية"},
    ]


def _raw():
    return [
        {"title": "لحظة قوية", "start_time": 11, "end_time": 40, "score": 95},
        {"title": "لحظة ضعيفة", "start_time": 45, "end_time": 60, "score": 40},
    ]


def test_process_segments_honours_editor_rejection():
    def fake_llm(prompt):
        reviews = [{"id": "c0", "verdict": "keep", "hook_score": 92.0},
                   {"id": "c1", "verdict": "reject", "reason": "بلا لحظة"}]
        return json.dumps({"reviews": reviews})

    result = cvs.process_segments(
        _raw(), _transcript(), 15, 90, editor_review_llm=fake_llm)

    titles = [seg.get("title") for seg in result["segments"]]
    assert "لحظة قوية" in titles
    assert "لحظة ضعيفة" not in titles
    rejected = result["editor_rejected"]
    assert len(rejected) == 1
    assert rejected[0]["editor_review"]["verdict"] == "reject"
    assert result["editor_review_summary"]["reject"] == 1


def test_process_segments_editor_trim_is_applied():
    def fake_llm(prompt):
        return json.dumps({"reviews": [
            {"id": "c0", "verdict": "trim", "trim_start": 15.0, "trim_end": 35.0},
            {"id": "c1", "verdict": "keep"},
        ]})

    result = cvs.process_segments(
        _raw(), _transcript(), 15, 90, editor_review_llm=fake_llm)
    first = result["segments"][0]
    assert first["start_time"] == 15.0
    assert first["end_time"] == 35.0
    assert first["editor_review"]["trimmed_from"]


def test_process_segments_without_review_call_unchanged():
    result = cvs.process_segments(_raw(), _transcript(), 15, 90)
    assert len(result["segments"]) == 2
    assert "editor_review_summary" not in result
    assert "editor_rejected" not in result


def test_process_segments_editor_failure_is_fail_open():
    def broken_llm(prompt):
        raise RuntimeError("backend exploded")

    result = cvs.process_segments(
        _raw(), _transcript(), 15, 90, editor_review_llm=broken_llm)
    assert len(result["segments"]) == 2  # selection preserved
    assert result["editor_review_summary"]["errors"]


def test_editor_review_call_respects_env_flag(monkeypatch):
    monkeypatch.delenv("VIRALCUTTER_EDITOR_REVIEW", raising=False)
    assert cvs._editor_review_call("gemini", "key", "model") is None

    monkeypatch.setenv("VIRALCUTTER_EDITOR_REVIEW", "1")
    assert callable(cvs._editor_review_call("gemini", "key", "model"))
    assert callable(cvs._editor_review_call("g4f", None, "model"))
    # manual/local backends have no programmatic review call
    assert cvs._editor_review_call("manual", None, "model") is None
    assert cvs._editor_review_call("local", None, "model") is None


def test_editor_review_call_uses_backend(monkeypatch):
    monkeypatch.setenv("VIRALCUTTER_EDITOR_REVIEW", "true")
    calls = []
    monkeypatch.setattr(cvs, "call_gemini",
                        lambda prompt, key, model_name=None: calls.append(prompt) or "{}")
    review_call = cvs._editor_review_call("gemini", "k", "m")
    review_call("prompt-1")
    assert calls == ["prompt-1"]
