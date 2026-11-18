"""Tests for scripts/segment_json.py (extracted from create_viral_segments).

The helpers are exercised through the NEW module path; identity tests
below assert the old import path re-exports the very same callables.
"""

from scripts import create_viral_segments as cvs
from scripts.segment_json import _extract_segments_json, clean_json_response


def test_reexports_are_the_same_callables():
    assert cvs.clean_json_response is clean_json_response
    assert cvs._extract_segments_json is _extract_segments_json


def test_clean_json_response_plain_json():
    parsed = clean_json_response('{"segments": [{"start_time": 1, "title": "a"}]}')
    assert parsed == {"segments": [{"start_time": 1, "title": "a"}]}


def test_clean_json_response_code_fences_and_prefix():
    response = (
        "Here are your segments:\n"
        "```json\n"
        '{"segments": [{"start_time": 5, "title": "b"}]}\n'
        "```"
    )
    assert clean_json_response(response)["segments"][0]["title"] == "b"


def test_clean_json_response_recovers_dict_shaped_segments():
    payload = '{"segments": {"1": {"start_time": 2}, "0": {"start_time": 1}}}'
    parsed = clean_json_response(payload)
    assert [item["start_time"] for item in parsed["segments"]] == [1, 2]


def test_clean_json_response_empty_and_malformed():
    assert clean_json_response("") == {"segments": []}
    assert clean_json_response("no json here at all") == {"segments": []}
    assert clean_json_response('{"segments": {}}') == {"segments": []}


def test_clean_json_response_non_string_input():
    assert clean_json_response(None) == {"segments": []}
    assert clean_json_response(123) == {"segments": []}


def test_clean_json_response_over_escaped_payload():
    # Double-escaped/manual-paste payload: the legacy fallback unescapes
    # \" -> " and retries.
    payload = '{\\"segments\\": [{\\"start_time\\": 3, \\"title\\": \\"c\\"}]}'
    parsed = clean_json_response(payload)
    assert parsed["segments"] == [{"start_time": 3, "title": "c"}]


def test_clean_json_response_keeps_escaped_newlines_intact():
    # A valid payload whose title contains an escaped newline must parse
    # as-is; normalising \\n to a raw newline would corrupt strict JSON.
    payload = '{"segments": [{"start_time": 1, "title": "line\\nbreak"}]}'
    parsed = clean_json_response(payload)
    assert parsed["segments"][0]["title"] == "line\nbreak"


def test_extract_segments_json_strips_think_tags():
    payload = (
        "<think>reasoning about segments</think>\n"
        '{"segments": [{"start_time": 7, "title": "t"}]}'
    )
    assert _extract_segments_json(payload)["segments"][0]["start_time"] == 7


def test_extract_segments_json_recovers_truncated_list():
    payload = (
        '{"segments": [{"start_time": 1, "title": "a"}, '
        '{"start_time": 2, "title": "b"}, {"start_time": 3'
    )
    parsed = _extract_segments_json(payload)
    assert [item["title"] for item in parsed["segments"]] == ["a", "b"]


def test_extract_segments_json_dict_shaped_numeric_keys():
    payload = '{"segments": {"0": {"title": "a"}, "1": {"title": "b"}}}'
    parsed = _extract_segments_json(payload)
    assert [item["title"] for item in parsed["segments"]] == ["a", "b"]


def test_extract_segments_json_no_segments_keyword():
    assert _extract_segments_json("this is not json at all") == {"segments": []}
    assert _extract_segments_json("") == {"segments": []}


def test_extract_segments_json_rejects_non_dict_segment_values():
    assert _extract_segments_json('{"segments": {"meta": "nope"}}') == {"segments": []}
