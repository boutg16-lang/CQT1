# -*- coding: utf-8 -*-
"""Direct tests for scripts/segment_validator.py.

Covers the final-segment gate: timestamp/duration rules, media bounds,
transcript and truncation checks, edge silence, title and safety status,
duplication, the batch API, annotation and publish-block reasons.
Pure stdlib; the validator never raises and never mutates its input.
"""

import copy

from scripts import segment_validator as sv


def _valid_segment(**overrides):
    """A segment that passes every check with default options."""
    segment = {
        "start_time": 10.0,
        "end_time": 40.0,
        "transcript_text": "one two three four five six seven",
        "title_validation": {"status": "verified"},
    }
    segment.update(overrides)
    return segment


def _codes(report):
    return [item["code"] for item in report["errors"]]


# ---------------------------------------------------------------------------
# Coercion helpers
# ---------------------------------------------------------------------------


def test_finite_number_accepts_numbers_and_numeric_strings():
    assert sv._finite_number(3) == 3.0
    assert sv._finite_number(2.5) == 2.5
    assert sv._finite_number(" 42 ") == 42.0
    assert sv._finite_number("-1.5") == -1.5


def test_finite_number_rejects_bool_none_nan_inf_and_junk():
    assert sv._finite_number(True) is None
    assert sv._finite_number(False) is None
    assert sv._finite_number(None) is None
    assert sv._finite_number("") is None
    assert sv._finite_number("   ") is None
    assert sv._finite_number("abc") is None
    assert sv._finite_number(float("nan")) is None
    assert sv._finite_number(float("inf")) is None
    assert sv._finite_number([1]) is None


def test_time_seconds_numbers_and_clock_strings():
    assert sv._time_seconds(90) == 90.0
    assert sv._time_seconds("90") == 90.0
    assert sv._time_seconds("01:30") == 90.0
    assert sv._time_seconds("01:02:03") == 3723.0
    assert sv._time_seconds("01:02:03.5") == 3723.5
    assert sv._time_seconds("not a time") is None
    assert sv._time_seconds(None) is None
    assert sv._time_seconds(True) is None


def test_is_true_booleanish_values():
    assert sv._is_true(True) is True
    assert sv._is_true(1) is True
    assert sv._is_true("yes") is True
    assert sv._is_true("TRUE") is True
    assert sv._is_true("on") is True
    assert sv._is_true(False) is False
    assert sv._is_true(0) is False
    assert sv._is_true(2) is False
    assert sv._is_true("no") is False
    assert sv._is_true(None) is False


# ---------------------------------------------------------------------------
# validate_final_segment: happy path and timestamps
# ---------------------------------------------------------------------------


def test_valid_segment_passes_with_duration_and_checks():
    report = sv.validate_final_segment(_valid_segment())
    assert report["ok"] is True
    assert report["errors"] == []
    assert report["duration"] == 30.0
    assert report["checks"]["start_time_numeric"] is True
    assert report["checks"]["end_time_numeric"] is True
    assert report["checks"]["end_after_start"] is True
    assert report["checks"]["title_status"] == "verified"


def test_clock_string_timestamps_are_accepted():
    report = sv.validate_final_segment(
        _valid_segment(start_time="00:00:10", end_time="00:00:40")
    )
    assert report["ok"] is True
    assert report["duration"] == 30.0


def test_missing_and_invalid_timestamps():
    assert "missing_start_time" in _codes(
        sv.validate_final_segment(_valid_segment(start_time=None))
    )
    segment = _valid_segment()
    del segment["start_time"]
    assert "missing_start_time" in _codes(sv.validate_final_segment(segment))
    assert "missing_end_time" in _codes(
        sv.validate_final_segment(_valid_segment(end_time=None))
    )
    assert "invalid_start_time" in _codes(
        sv.validate_final_segment(_valid_segment(start_time="soon"))
    )
    assert "invalid_end_time" in _codes(
        sv.validate_final_segment(_valid_segment(end_time=float("nan")))
    )


def test_end_not_after_start_is_two_errors():
    report = sv.validate_final_segment(_valid_segment(start_time=40.0, end_time=40.0))
    assert "end_not_after_start" in _codes(report)
    assert "non_positive_duration" in _codes(report)
    assert report["ok"] is False
    assert report["duration"] == 0.0


def test_non_dict_segment_never_raises():
    for bad in (None, "text", 42, ["x"]):
        report = sv.validate_final_segment(bad)
        assert report["ok"] is False
        assert "missing_start_time" in _codes(report)


# ---------------------------------------------------------------------------
# Duration limits and media bounds
# ---------------------------------------------------------------------------


def test_duration_below_min_error():
    report = sv.validate_final_segment(_valid_segment(end_time=15.0), min_duration=10.0)
    assert "duration_below_min" in _codes(report)
    assert report["checks"]["duration_within_min"] is False


def test_duration_below_min_transcript_limited_is_warning_only():
    report = sv.validate_final_segment(
        _valid_segment(end_time=15.0, transcript_limited=True), min_duration=10.0
    )
    assert report["ok"] is True
    assert [w["code"] for w in report["warnings"]] == [
        "duration_below_min_transcript_limited"
    ]


def test_duration_above_max_is_error():
    report = sv.validate_final_segment(_valid_segment(), max_duration=20.0)
    assert "duration_above_max" in _codes(report)
    assert report["checks"]["duration_within_max"] is False


def test_media_bounds_reject_out_of_range_window():
    report = sv.validate_final_segment(_valid_segment(), media_duration=30.0)
    assert "out_of_media_bounds" in _codes(report)
    report = sv.validate_final_segment(_valid_segment(), media_duration=60.0)
    assert report["ok"] is True
    assert report["checks"]["within_media_bounds"] is True
    report = sv.validate_final_segment(
        _valid_segment(start_time=-1.0, end_time=20.0), media_duration=60.0
    )
    assert "out_of_media_bounds" in _codes(report)


def test_media_bounds_ignored_without_positive_media_duration():
    for media in (None, 0, -5, "junk"):
        report = sv.validate_final_segment(_valid_segment(), media_duration=media)
        assert report["ok"] is True
        assert report["checks"]["within_media_bounds"] is None


def test_negative_option_values_fall_back_to_defaults():
    report = sv.validate_final_segment(
        _valid_segment(), min_duration=-5, max_duration=-1, min_lexical_words=-2
    )
    assert report["ok"] is True


# ---------------------------------------------------------------------------
# Transcript, truncation, edge silence
# ---------------------------------------------------------------------------


def test_empty_transcript_is_fatal_error():
    assert "empty_transcript" in _codes(
        sv.validate_final_segment(_valid_segment(transcript_text="   "))
    )
    assert "empty_transcript" in _codes(
        sv.validate_final_segment({"start_time": 0, "end_time": 5})
    )


def test_transcript_fallback_chain_window_analysis_then_text():
    segment = {
        "start_time": 0,
        "end_time": 5,
        "window_analysis": {"text": "a b c d"},
        "title_validation": {"status": "verified"},
    }
    assert sv.validate_final_segment(segment)["ok"] is True
    segment = {
        "start_time": 0,
        "end_time": 5,
        "text": "a b c d",
        "title_validation": {"status": "verified"},
    }
    assert sv.validate_final_segment(segment)["ok"] is True
    # Explicit argument wins over every stored field.
    assert (
        sv.validate_final_segment(
            {"start_time": 0, "end_time": 5, "transcript_text": ""},
            transcript_text="a b c d",
            require_title=False,
        )["ok"]
        is True
    )


def test_short_transcript_is_warning_not_error():
    report = sv.validate_final_segment(
        _valid_segment(transcript_text="two words"), min_lexical_words=3
    )
    assert report["ok"] is True
    assert [w["code"] for w in report["warnings"]] == ["short_transcript"]


def test_sentence_truncation_from_window_analysis():
    report = sv.validate_final_segment(
        _valid_segment(window_analysis={"starts_mid_sentence": True})
    )
    assert "sentence_truncation" in _codes(report)
    assert report["checks"]["no_sentence_truncation"] is False


def test_sentence_and_word_truncation_from_quality_flags():
    report = sv.validate_final_segment(
        _valid_segment(quality_flags=["ends mid-sentence", "MID_WORD cut"])
    )
    assert "sentence_truncation" in _codes(report)
    assert "word_truncation" in _codes(report)
    assert report["checks"]["no_word_truncation"] is False


def test_quality_flags_accept_string_or_tuple():
    report = sv.validate_final_segment(_valid_segment(quality_flags="mid_word"))
    assert "word_truncation" in _codes(report)
    report = sv.validate_final_segment(
        _valid_segment(quality_flags=("clean", "word_boundary"))
    )
    assert "word_truncation" in _codes(report)


def test_incomplete_ending_is_warning():
    report = sv.validate_final_segment(
        _valid_segment(window_analysis={"ends_incomplete": True})
    )
    assert report["ok"] is True
    assert [w["code"] for w in report["warnings"]] == ["incomplete_ending"]


def test_excessive_edge_silence_is_error_and_near_limit_warns():
    report = sv.validate_final_segment(
        _valid_segment(window_analysis={"leading_silence": 3.0})
    )
    assert "excessive_edge_silence" in _codes(report)
    assert report["checks"]["edge_silence_ok"] is False
    report = sv.validate_final_segment(
        _valid_segment(window_analysis={"trailing_silence": 1.5})
    )
    assert report["ok"] is True
    assert [w["code"] for w in report["warnings"]] == ["edge_silence_warning"]


# ---------------------------------------------------------------------------
# Title, safety, duplication
# ---------------------------------------------------------------------------


def test_title_missing_rejected_and_review():
    segment = _valid_segment()
    del segment["title_validation"]
    assert "title_missing" in _codes(sv.validate_final_segment(segment))
    report = sv.validate_final_segment(
        _valid_segment(
            title_validation={"status": "rejected", "reasons": ["made up fact"]}
        )
    )
    assert "title_rejected" in _codes(report)
    assert "made up fact" in report["errors"][0]["message"]
    report = sv.validate_final_segment(
        _valid_segment(title_validation={"status": "needs_review"})
    )
    assert report["ok"] is True
    assert [w["code"] for w in report["warnings"]] == ["title_needs_review"]
    # require_title=False turns a missing title into a pass.
    assert (
        sv.validate_final_segment(_valid_segment(), require_title=False)["ok"] is True
    )


def test_title_status_derived_from_factual_validation_block():
    verified = sv.validate_final_segment(
        _valid_segment(title_validation={"matches_transcript": True})
    )
    assert verified["ok"] is True
    assert verified["checks"]["title_status"] == "verified"
    hallucinated = sv.validate_final_segment(
        _valid_segment(
            title_validation={
                "contains_hallucinated_fact": True,
                "hallucinated_facts": ["Paris is in Italy"],
            }
        )
    )
    assert "title_rejected" in _codes(hallucinated)
    assert "Paris is in Italy" in hallucinated["errors"][0]["message"]
    clickbait = sv.validate_final_segment(
        _valid_segment(
            title_validation={"matches_transcript": True, "excessive_clickbait": True}
        )
    )
    assert clickbait["checks"]["title_status"] == "review"


def test_title_validation_read_from_title_data_fallback():
    segment = _valid_segment()
    segment["title_validation"] = None
    segment["title_data"] = {"validation": {"status": "verified"}}
    report = sv.validate_final_segment(segment)
    assert report["checks"]["title_status"] == "verified"
    assert report["ok"] is True


def test_safety_blocked_is_error_clean_and_unknown_pass():
    assert "safety_blocked" in _codes(
        sv.validate_final_segment(_valid_segment(safety_status="blocked"))
    )
    assert "safety_blocked" in _codes(
        sv.validate_final_segment(_valid_segment(safety={"status": "UNSAFE"}))
    )
    assert (
        sv.validate_final_segment(_valid_segment(safety_status="approved"))["ok"]
        is True
    )
    report = sv.validate_final_segment(_valid_segment(safety_status="weird"))
    assert report["ok"] is True
    assert report["checks"]["safety_ok"] is None


def test_duplicate_flags_are_errors():
    assert "semantic_duplicate" in _codes(
        sv.validate_final_segment(_valid_segment(semantic_duplicate=True))
    )
    assert "semantic_duplicate" in _codes(
        sv.validate_final_segment(_valid_segment(duplicate_of="clip_001"))
    )
    assert "temporal_duplicate" in _codes(
        sv.validate_final_segment(_valid_segment(temporal_duplicate="yes"))
    )
    report = sv.validate_final_segment(_valid_segment())
    assert report["checks"]["duplicate_ok"] is True


# ---------------------------------------------------------------------------
# Batch API, annotation, publish reasons
# ---------------------------------------------------------------------------


def test_validate_final_segments_batch_and_invalid_entries():
    batch = sv.validate_final_segments(
        [
            _valid_segment(),
            _valid_segment(end_time=5.0),  # end before start
            "not a dict",
            None,
        ]
    )
    assert batch["ok"] is False
    assert batch["invalid_indices"] == [1, 2, 3]
    assert batch["error_count"] >= 3
    assert batch["results"][2]["errors"][0]["code"] == "invalid_segment"
    assert len(batch["results"]) == 4


def test_validate_final_segments_accepts_dict_and_none():
    assert sv.validate_final_segments(None) == {
        "ok": True,
        "results": [],
        "invalid_indices": [],
        "error_count": 0,
    }
    batch = sv.validate_final_segments({"a": _valid_segment()})
    assert batch["ok"] is True
    assert len(batch["results"]) == 1


def test_is_publishable_segment_predicate():
    assert sv.is_publishable_segment(_valid_segment()) is True
    assert sv.is_publishable_segment(_valid_segment(end_time=5.0)) is False


def test_annotate_segment_validation_copies_and_flags():
    payload = {
        "segments": [
            _valid_segment(),
            _valid_segment(end_time=5.0, title="bad"),
        ]
    }
    original = copy.deepcopy(payload)
    annotated = sv.annotate_segment_validation(payload)
    assert payload == original  # input never mutated
    good, bad = annotated["segments"]
    assert good["requires_review"] is False
    assert good["export_blocked"] is False
    assert good["publish_blocked_reason"] == ""
    assert bad["requires_review"] is True
    assert bad["export_blocked"] is True
    assert bad["publish_blocked_reason"] == "manual_review_required"
    assert bad["final_validation"]["ok"] is False


def test_annotate_manual_approval_clears_review_but_not_fatal():
    payload = {"segments": [_valid_segment(end_time=5.0, manual_review_approved=True)]}
    annotated = sv.annotate_segment_validation(payload)
    assert annotated["segments"][0]["requires_review"] is False
    assert annotated["segments"][0]["export_blocked"] is True


def test_annotate_non_dict_payload_is_returned_unchanged():
    assert sv.annotate_segment_validation(None) is None
    assert sv.annotate_segment_validation({"segments": "nope"}) == {"segments": "nope"}


def test_publish_block_reasons():
    assert sv.publish_block_reasons(_valid_segment()) == []
    assert sv.publish_block_reasons(None) == ["invalid_segment"]
    assert sv.publish_block_reasons({"export_blocked": True, **_valid_segment()}) == [
        "export_blocked"
    ]
    reasons = sv.publish_block_reasons(_valid_segment(end_time=5.0))
    assert "end_not_after_start" in reasons
    assert "non_positive_duration" in reasons
    # A non-fatal error (word truncation) blocks auto-publish with the
    # generic manual-review reason; a review-only title status alone does
    # not, because warnings never fail a segment.
    truncated = _valid_segment(quality_flags=["mid_word"])
    assert sv.publish_block_reasons(truncated) == ["manual_review_required"]
    flagged = _valid_segment(requires_review=True)
    assert sv.publish_block_reasons(flagged) == ["manual_review_required"]


def test_input_segment_is_never_mutated():
    segment = _valid_segment(quality_flags=["mid_word"])
    snapshot = copy.deepcopy(segment)
    sv.validate_final_segment(segment)
    sv.annotate_segment_validation({"segments": [segment]})
    assert segment == snapshot
