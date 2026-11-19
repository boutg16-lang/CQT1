# -*- coding: utf-8 -*-
"""Direct tests for scripts/seo_titles.py.

SEO title scoring, template generation and publish-time hints. The only
network call (fetch_suggestions) is mocked; everything else is pure.
"""

import datetime as dt
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import seo_titles as st  # noqa: E402

# ---------------------------------------------------------------------------
# Suggest-response parsing helpers
# ---------------------------------------------------------------------------

def test_parse_suggest_response_plain_json():
    data = st._parse_suggest_response('["topic", ["a", "b"], []]')
    assert data[1] == ["a", "b"]


def test_parse_suggest_response_wrapped():
    raw = 'window.google.ac.h(["topic", ["a", "b"], []])'
    assert st._parse_suggest_response(raw)[1] == ["a", "b"]


def test_parse_suggest_response_empty_and_invalid():
    assert st._parse_suggest_response("") == []
    assert st._parse_suggest_response("not json at all") == []
    assert st._parse_suggest_response(None) == []


def test_suggestion_text_list_item():
    assert st._suggestion_text(["make money", 0, [512]]) == "make money"


def test_suggestion_text_stringified_list():
    assert st._suggestion_text("['make money', 0, [512]]") == "make money"
    assert st._suggestion_text('["make money", 0]') == "make money"


def test_suggestion_text_plain_string():
    assert st._suggestion_text("  plain topic  ") == "plain topic"


# ---------------------------------------------------------------------------
# fetch_suggestions (network mocked)
# ---------------------------------------------------------------------------

class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def read(self, _n):
        return self._payload.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_fetch_suggestions_dedupes_and_limits(monkeypatch):
    payload = json.dumps(["topic", ["Alpha", "alpha", "Beta", "Gamma"], []])
    monkeypatch.setattr(
        st.urllib.request, "urlopen",
        lambda req, timeout=None: _FakeResponse(payload))
    result = st.fetch_suggestions("topic", max_results=2)
    assert result == ["Alpha", "Beta"]


def test_fetch_suggestions_network_failure_returns_empty(monkeypatch):
    def _boom(req, timeout=None):
        raise ConnectionError("offline")

    monkeypatch.setattr(st.urllib.request, "urlopen", _boom)
    assert st.fetch_suggestions("topic") == []


def test_fetch_suggestions_wrapped_response(monkeypatch):
    payload = 'window.google.ac.h(["t", ["one", "two"], []])'
    monkeypatch.setattr(
        st.urllib.request, "urlopen",
        lambda req, timeout=None: _FakeResponse(payload))
    assert st.fetch_suggestions("t") == ["one", "two"]


def test_fetch_suggestions_garbage_response_returns_empty(monkeypatch):
    monkeypatch.setattr(
        st.urllib.request, "urlopen",
        lambda req, timeout=None: _FakeResponse("garbage"))
    assert st.fetch_suggestions("t") == []


# ---------------------------------------------------------------------------
# Scoring internals
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("length,expected", [
    (35, 25.0), (70, 25.0), (20, 15.0), (34, 15.0), (71, 15.0),
    (90, 15.0), (10, 8.0), (19, 8.0), (91, 8.0), (110, 8.0),
    (9, 0.0), (111, 0.0),
])
def test_len_score_boundaries(length, expected):
    assert st._len_score("x" * length) == expected


def test_hook_score_question_digit_hook_word():
    # "؟" +8, digit +6, hook word "كيف" +7 = 21.
    assert st._hook_score("كيف تكسب المال في 2024؟") == 21.0


def test_hook_score_exclamation_and_penalty():
    assert st._hook_score("Wow!") == 4.0
    # Three "!" -> +4 then -6, floored at 0.
    assert st._hook_score("Wow!!!") == 0.0


def test_hook_score_no_signals():
    assert st._hook_score("plain title here") == 0.0


def test_keyword_score_no_keywords():
    assert st._keyword_score("anything", []) == 5.0


def test_keyword_score_hits():
    assert st._keyword_score("المال والعمل", ["المال"]) == 8.0
    assert st._keyword_score("المال والعمل", ["المال", "العمل"]) == 11.5
    # Cap at 15 even with many hits.
    assert st._keyword_score("a b c d", ["a", "b", "c", "d"]) == 15.0
    assert st._keyword_score("nothing here", ["zzz"]) == 0.0


def test_penalty_score_all_caps():
    assert st._penalty_score("SHOUTY TITLE HERE") == 15.0
    # Mixed case is not all-caps.
    assert st._penalty_score("Normal Title Here") == 0.0


def test_penalty_score_repeated_words_and_length():
    assert st._penalty_score("money money money money") == 15.0
    assert st._penalty_score("x" * 111) == 10.0


def test_score_title_empty():
    result = st.score_title("")
    assert result["score"] == 0.0
    assert result["breakdown"] == {"length": 0, "hook": 0,
                                   "keywords": 0, "penalty": 0}


def test_score_title_known_value():
    # Verified empirically: length 25 + hook 21 + keywords 8 - penalty 0.
    result = st.score_title("كيف تكسب المال من الإنترنت في 2024؟", ["المال"])
    assert result["score"] == 54.0
    assert result["breakdown"] == {"length": 25.0, "hook": 21.0,
                                   "keywords": 8.0, "penalty": 0.0}


def test_score_title_breakdown_sums_to_score():
    result = st.score_title("HOW TO MAKE MONEY FAST!!! money money", ["money"])
    b = result["breakdown"]
    expected = max(0.0, min(100.0, b["length"] + b["hook"]
                            + b["keywords"] - b["penalty"]))
    assert result["score"] == round(expected, 1)
    assert 0.0 <= result["score"] <= 100.0


def test_score_title_none_keywords():
    result = st.score_title("كيف تكسب المال؟", None)
    assert result["breakdown"]["keywords"] == 5.0


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------

def test_generate_titles_sorted_by_score_desc():
    titles = st.generate_titles("تعلم البرمجة", count=6)
    assert titles
    scores = [item["score"] for item in titles]
    assert scores == sorted(scores, reverse=True)
    assert all("title" in item and "score" in item for item in titles)


def test_generate_titles_count_clamped():
    assert st.generate_titles("topic", count=0) == []
    assert st.generate_titles("topic", count=-3) == []
    # At most 8 templates exist, even when asking for 20.
    assert len(st.generate_titles("topic", count=20)) <= 8
    assert len(st.generate_titles("topic", count=3)) == 3


def test_generate_titles_empty_topic_fallback():
    titles = st.generate_titles("", count=6)
    assert titles
    assert all("هذا الموضوع" in item["title"] for item in titles)


def test_generate_titles_topic_token_strips_trailing_punctuation():
    assert st._topic_token("كيف؟؟ ") == "كيف"
    assert st._topic_token("") == "هذا الموضوع"


def test_generate_titles_no_duplicate_titles():
    titles = st.generate_titles("تعلم البرمجة", count=8)
    seen = [item["title"].casefold() for item in titles]
    assert len(seen) == len(set(seen))


def test_generate_titles_grounded_mode():
    transcript = ("اليوم نتحدث عن كيف تكسب المال من الإنترنت. "
                  "المال مهم جدا في حياتنا اليومية.")
    titles = st.generate_titles("المال", transcript_text=transcript, count=6)
    assert titles
    for item in titles:
        assert item["grounded"] is True
        assert item["title_source"] == "exact_clip_transcript"
        assert item["validation"]["status"] != "rejected"
        # Every grounded title comes from the transcript itself.
        assert len(item["title"]) >= 12


def test_generate_titles_grounded_mode_rejects_invented_numbers():
    transcript = "اليوم نتحدث عن كيف تكسب المال من الإنترنت بسهولة."
    titles = st.generate_titles(
        "المال", transcript_text="ربحت 99999 دولار في يوم واحد. " + transcript,
        count=6)
    # The 99999 sentence is a candidate only if it survives validation; any
    # title that invents a number not in the *window* text is dropped. Here
    # the number IS in the window, so candidates are grounded either way.
    assert all(item["grounded"] for item in titles)


def test_generate_titles_grounded_empty_transcript():
    assert st.generate_titles("topic", transcript_text="   ") == []
    assert st.generate_titles("topic", transcript_text="short") == []


def test_generate_titles_grounded_short_sentences_skipped():
    # No sentence reaches 12 chars -> no candidates.
    assert st.generate_titles("topic", transcript_text="قصير. جدا.") == []


# ---------------------------------------------------------------------------
# Publish-time hints
# ---------------------------------------------------------------------------

def test_best_time_windows_known_platform():
    result = st.best_time_windows("tiktok")
    assert result["platform"] == "tiktok"
    assert result["weekday"] == [(12, 14), (19, 23)]


def test_best_time_windows_unknown_platform_falls_back_to_youtube():
    result = st.best_time_windows("unknown")
    assert result["platform"] == "youtube"


def test_suggest_next_slots_weekday():
    # 2026-01-05 is a Monday; YouTube weekday window starts 17:00.
    start = dt.datetime(2026, 1, 5, 10, 0, tzinfo=dt.timezone.utc)
    slots = st.suggest_next_slots("youtube", 2, start)
    assert slots == ["2026-01-05T17:00:00+00:00",
                     "2026-01-06T17:00:00+00:00"]


def test_suggest_next_slots_skips_past_slots():
    start = dt.datetime(2026, 1, 5, 18, 30, tzinfo=dt.timezone.utc)
    slots = st.suggest_next_slots("youtube", 1, start)
    # 17:00 today is past; next slot is tomorrow 17:00.
    assert slots == ["2026-01-06T17:00:00+00:00"]


def test_suggest_next_slots_weekend_has_two_windows():
    # 2026-01-10 is a Saturday; YouTube weekend windows (10,13) and (17,21).
    start = dt.datetime(2026, 1, 10, 9, 0, tzinfo=dt.timezone.utc)
    slots = st.suggest_next_slots("youtube", 2, start)
    assert slots == ["2026-01-10T10:00:00+00:00",
                     "2026-01-10T17:00:00+00:00"]


def test_suggest_next_slots_unknown_platform_uses_youtube():
    start = dt.datetime(2026, 1, 5, 10, 0, tzinfo=dt.timezone.utc)
    assert st.suggest_next_slots("nope", 1, start) == [
        "2026-01-05T17:00:00+00:00"]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def test_main_score_command(capsys):
    assert st.main(["score", "كيف تكسب المال؟", "--keyword", "المال"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["score"] > 0


def test_main_generate_command(capsys):
    assert st.main(["generate", "تعلم البرمجة", "--count", "2"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert len(out) == 2


def test_main_slots_command(capsys):
    assert st.main(["slots", "--platform", "tiktok", "--count", "1"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert len(out) == 1


def test_main_suggest_command_mocked(monkeypatch, capsys):
    monkeypatch.setattr(st, "fetch_suggestions", lambda *a, **k: ["a", "b"])
    assert st.main(["suggest", "topic"]) == 0
    assert json.loads(capsys.readouterr().out) == ["a", "b"]


def test_main_no_command_returns_2():
    assert st.main([]) == 2
