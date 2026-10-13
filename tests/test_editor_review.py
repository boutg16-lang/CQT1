import json

from scripts import editor_review


def _segment(index, score=80.0):
    return {
        "title": "مقطع %d" % index,
        "recommended_title": "عنوان %d" % index,
        "start_time": 10.0 + index * 100.0,
        "end_time": 70.0 + index * 100.0,
        "duration": 60.0,
        "score": score,
        "transcript_text": "ليش الناس تسأل؟ لأن الجواب يغير كل شيء في القصة.",
    }


def test_build_prompt_lists_every_candidate():
    candidates = [("c0", _segment(0)), ("c1", _segment(1))]
    prompt = editor_review.build_editor_prompt(candidates)
    assert "المقطع c0" in prompt and "المقطع c1" in prompt
    assert "عنوان 0" in prompt
    assert "keep|trim|reject" in prompt


def test_parse_response_tolerates_fences_and_prose():
    response = "أكيد! هذه المراجعة:\n```json\n" + json.dumps({"reviews": [
        {"id": "c0", "verdict": "keep", "hook_score": 88, "reason": "خطاف قوي"},
        {"id": "c1", "verdict": "reject", "title_verdict": "oversells",
         "reason": "العنوان يعد بما لا يوجد"},
    ]}) + "\n```\nانتهى."
    reviews = editor_review.parse_editor_response(response)
    assert len(reviews) == 2
    assert reviews[0]["verdict"] == "keep"
    assert reviews[0]["hook_score"] == 88.0
    assert reviews[1]["title_verdict"] == "oversells"


def test_parse_response_drops_invalid_reviews():
    response = json.dumps({"reviews": [
        {"id": "c0", "verdict": "maybe"},            # unknown verdict
        {"id": "", "verdict": "keep"},               # no id
        {"verdict": "keep"},                         # missing id
        "garbage",                                   # not a dict
        {"id": "c2", "verdict": "trim", "trim_start": "oops",
         "hook_score": 900},                         # bad values dropped
    ]})
    reviews = editor_review.parse_editor_response(response)
    assert len(reviews) == 1
    assert reviews[0]["id"] == "c2"
    assert "trim_start" not in reviews[0]
    assert "hook_score" not in reviews[0]


def test_parse_response_handles_garbage():
    assert editor_review.parse_editor_response("") == []
    assert editor_review.parse_editor_response("لا يوجد JSON هنا") == []
    assert editor_review.parse_editor_response('{"other": 1}') == []


def test_apply_verdicts_keep_trim_reject():
    segments = [_segment(0), _segment(1), _segment(2)]
    reviews = [
        {"id": "c0", "verdict": "keep", "hook_score": 90.0},
        {"id": "c1", "verdict": "trim", "trim_start": 130.0, "trim_end": 160.0,
         "reason": "اللحظة تبدأ متأخرة"},
        {"id": "c2", "verdict": "reject", "reason": "بلا payoff"},
    ]
    result = editor_review.apply_editor_reviews(segments, reviews)
    annotated = result["segments"]

    assert annotated[0]["editor_review"]["verdict"] == "keep"
    assert annotated[0]["start_time"] == 10.0  # untouched

    assert annotated[1]["start_time"] == 130.0
    assert annotated[1]["end_time"] == 160.0
    assert annotated[1]["duration"] == 30.0
    assert annotated[1]["editor_review"]["trimmed_from"] == [110.0, 170.0]

    assert annotated[2]["editor_review"]["verdict"] == "reject"
    assert annotated[2]["editor_review"]["reason"] == "بلا payoff"
    # rejected segments stay in the payload — deletion is the caller's call
    assert len(annotated) == 3
    assert result["summary"] == {
        "reviewed": 3, "keep": 1, "trim": 1, "reject": 1, "title_warnings": 0}
    # input segments were not mutated
    assert "editor_review" not in segments[1]


def test_trim_is_clamped_inside_original_window():
    segments = [_segment(0)]
    reviews = [{"id": "c0", "verdict": "trim", "trim_start": 0.0, "trim_end": 9999.0}]
    result = editor_review.apply_editor_reviews(segments, reviews)
    entry = result["segments"][0]
    assert entry["start_time"] == 10.0   # clamped to original start
    assert entry["end_time"] == 70.0     # clamped to original end
    assert entry["editor_review"]["verdict"] == "trim"


def test_impossible_trim_falls_back_to_keep():
    segments = [_segment(0)]
    reviews = [{"id": "c0", "verdict": "trim", "trim_start": 69.0, "trim_end": 70.0}]
    result = editor_review.apply_editor_reviews(segments, reviews, min_duration=15.0)
    record = result["segments"][0]["editor_review"]
    assert record["verdict"] == "keep"
    assert "trim_fallback" in record
    assert result["segments"][0]["start_time"] == 10.0  # untouched


def test_title_warning_is_counted():
    segments = [_segment(0), _segment(1)]
    reviews = [
        {"id": "c0", "verdict": "keep", "title_verdict": "oversells"},
        {"id": "c1", "verdict": "keep", "title_verdict": "honest"},
    ]
    result = editor_review.apply_editor_reviews(segments, reviews)
    assert result["summary"]["title_warnings"] == 1
    assert result["segments"][0]["editor_review"]["title_warning"] is True


def test_editor_review_segments_end_to_end_with_fake_llm():
    segments = [_segment(index, score=90.0 - index) for index in range(7)]
    calls = []

    def fake_llm(prompt):
        calls.append(prompt)
        # every prompt names its candidates; answer keep for even, reject for odd
        reviews = []
        for index in range(7):
            if "المقطع c{}".format(index) in prompt:
                verdict = "keep" if index % 2 == 0 else "reject"
                reviews.append({"id": "c{}".format(index), "verdict": verdict,
                                "reason": "سبب"})
        return json.dumps({"reviews": reviews})

    result = editor_review.editor_review_segments(
        segments, fake_llm, batch_size=3, max_candidates=7)
    summary = result["summary"]
    assert len(calls) == 3  # ceil(7 / 3)
    assert summary["candidates"] == 7
    assert summary["reviewed"] == 7
    assert summary["keep"] == 4 and summary["reject"] == 3
    assert summary["errors"] == []
    # segments were re-sorted by score before review: c0 has the top score
    assert result["segments"][0]["score"] == 90.0


def test_editor_review_segments_caps_candidates():
    segments = [_segment(index) for index in range(20)]
    reviewed_ids = set()

    def fake_llm(prompt):
        for index in range(20):
            if "المقطع c{}".format(index) in prompt:
                reviewed_ids.add(index)
        return json.dumps({"reviews": []})

    result = editor_review.editor_review_segments(segments, fake_llm, max_candidates=5)
    assert result["summary"]["candidates"] == 5
    assert result["summary"]["skipped"] == 15
    assert max(reviewed_ids) < 5  # only the top 5 were sent


def test_editor_review_segments_never_raises_on_backend_failure():
    segments = [_segment(0), _segment(1)]

    def broken_llm(prompt):
        raise RuntimeError("backend exploded")

    result = editor_review.editor_review_segments(segments, broken_llm)
    assert result["summary"]["errors"]
    assert result["summary"]["reviewed"] == 0
    assert len(result["segments"]) == 2  # pipeline output preserved
