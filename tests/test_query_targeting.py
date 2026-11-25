# -*- coding: utf-8 -*-
"""Tests for query-targeted cutting (feat/pro-cutting item 3).

scripts/query_targeting must: match a search query against the real
transcript words of candidate segments (Arabic-orthography-blind), pick the
strongest genuinely-about-the-query segment, REFUSE when nothing matches (no
misleading titles), and lead the publish title with the query — scored with
scripts/seo_titles.score_title. Offline only: embeddings are never enabled.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import query_targeting as qt  # noqa: E402
from scripts import seo_titles  # noqa: E402


def _segment(idx, transcript_text, start=0.0, end=30.0, score=50.0):
    return {
        "index": idx,
        "title": "مقطع {}".format(idx),
        "start_time": start,
        "end_time": end,
        "transcript_text": transcript_text,
        "selection_score": score,
    }


ABOUT_QUERY = (
    "مصطفى بونيف يتحدث عن تبون والقرار العسكري في الجزائر. "
    "الرئيس تبون اتخذ قرارات صعبة في الجزائر."
)
OTHER = "في هذا المقطع نتحدث عن كأس العالم وكأس العالم لكرة القدم في قطر."

# ---------------------------------------------------------------------------
# query_tokens / relevance
# ---------------------------------------------------------------------------


class TestQueryTokens:
    def test_content_tokens_only(self):
        tokens = qt.query_tokens("هل مصطفى بونيف في تبون؟")
        assert "مصطفى" in tokens  # tashkeel/tatweel folded; ى kept distinct by design
        assert "تبون" in tokens
        assert "هل" not in tokens and "في" not in tokens

    def test_empty_query(self):
        assert qt.query_tokens("") == []


class TestQueryRelevance:
    def test_full_overlap(self):
        seg = _segment(0, ABOUT_QUERY)
        assert qt.query_relevance("مصطفى بونيف تبون", seg) == 1.0

    def test_orthography_blind(self):
        seg = _segment(0, ABOUT_QUERY)
        # أ/ا (hamza carrier) يُوحَّد قبل المطابقة — "الجزاير" تطابق "الجزائر"
        assert qt.query_relevance("الجزاير اتخذ قرارات", seg) == 1.0

    def test_zero_overlap_is_zero_even_with_semantics(self):
        seg = _segment(0, OTHER)
        # semantic similarity can never rescue zero lexical overlap
        assert qt.query_relevance("مصطفى بونيف تبون", seg, semantic_similarity=1.0) == 0.0

    def test_semantics_add_small_bonus_on_lexical_match(self):
        seg = _segment(0, ABOUT_QUERY)
        # partial lexical overlap (2 of 4 tokens) + full semantic agreement
        base = qt.query_relevance("بونيف تبون الجزائر قطر", seg)
        boosted = qt.query_relevance(
            "بونيف تبون الجزائر قطر", seg, semantic_similarity=1.0
        )
        assert boosted > base and boosted <= 1.0

    def test_window_text_overrides_transcript(self):
        seg = _segment(0, OTHER)
        assert (
            qt.query_relevance("بونيف تبون", seg, window_text="بونيف و تبون معا")
            == 1.0
        )


# ---------------------------------------------------------------------------
# Ranking + refusal
# ---------------------------------------------------------------------------


class TestRankSegments:
    def test_best_segment_wins(self):
        segs = [_segment(0, OTHER, score=90.0), _segment(1, ABOUT_QUERY, score=40.0)]
        report = qt.rank_segments_for_query("مصطفى بونيف تبون", segs)
        assert report["refused"] is False
        # the segment ABOUT the query wins even with a lower generic score
        assert report["best"]["index"] == 1
        assert report["best"]["_query_relevance"] >= qt.DEFAULT_MIN_RELEVANCE

    def test_refuse_when_nothing_matches(self):
        segs = [_segment(0, OTHER), _segment(1, "طبخ الباولو باللحم المفروم")]
        report = qt.rank_segments_for_query("مصطفى بونيف تبون", segs)
        assert report["refused"] is True
        assert report["refusal_reason"] == "no_segment_about_query"
        assert report["best"] is None

    def test_input_not_mutated(self):
        segs = [_segment(0, OTHER, start=0.0, end=30.0), _segment(1, ABOUT_QUERY, start=60.0, end=90.0)]
        before = [dict(s) for s in segs]
        qt.rank_segments_for_query("بونيف تبون", segs)
        assert segs == before

    def test_empty_query_refused(self):
        report = qt.rank_segments_for_query("", [_segment(0, ABOUT_QUERY)])
        assert report["refused"] is True


# ---------------------------------------------------------------------------
# Query-led title
# ---------------------------------------------------------------------------


class TestBuildQueryTitle:
    def test_title_leads_with_query_and_is_scored(self):
        seg = _segment(0, ABOUT_QUERY)
        data = qt.build_query_title("مصطفى بونيف تبون", seg)
        assert data["title"].startswith("مصطفى بونيف تبون")
        assert data["score"] == data["score"]  # numeric
        assert "score" in data and data["score"] > 0
        # scored by the SAME engine, not a local re-implementation
        assert data["score"] == seo_titles.score_title(
            data["title"], ["مصطفى بونيف تبون"]
        )["score"]

    def test_grounded_question_comes_from_transcript(self):
        seg = _segment(0, ABOUT_QUERY)
        data = qt.build_query_title("بونيف تبون", seg)
        question_candidates = [
            c["title"] for c in data.get("candidates", []) if c["pattern"] == "query_question"
        ]
        for title in question_candidates:
            # the question part after the query must be verbatim transcript words
            body = title.split(":", 1)[1].strip().rstrip("؟")
            body_tokens = [t for t in body.split() if t]
            transcript = qt._normalized_match_text(ABOUT_QUERY).split()
            assert all(
                qt._normalized_match_text(tok) in transcript for tok in body_tokens
            ), title

    def test_fallback_without_segment(self):
        data = qt.build_query_title("مصطفى بونيف تبون", None)
        assert data["title"].startswith("مصطفى بونيف تبون")
        assert "| الفيديو الكامل" in data["title"]

    def test_empty_query_empty_title(self):
        assert qt.build_query_title("", None)["title"] == ""


# ---------------------------------------------------------------------------
# apply_target_query (the pipeline hook)
# ---------------------------------------------------------------------------


class TestApplyTargetQuery:
    def test_keeps_only_segments_about_the_query(self):
        segs = [_segment(0, OTHER, start=0.0, end=30.0), _segment(1, ABOUT_QUERY, start=60.0, end=90.0)]
        result = {}
        kept = qt.apply_target_query(segs, "مصطفى بونيف تبون", result)
        assert [s["index"] for s in kept] == [1]
        report = result["target_query"]
        assert report["refused"] is False
        assert len(report["dropped"]) == 1
        assert report["dropped"][0]["title"] == "مقطع 0"

    def test_winner_gets_query_title_and_annotation(self):
        segs = [_segment(1, ABOUT_QUERY)]
        kept = qt.apply_target_query(segs, "مصطفى بونيف تبون", {})
        entry = kept[0]
        assert entry["query"] == "مصطفى بونيف تبون"
        assert entry["title"].startswith("مصطفى بونيف تبون")
        assert entry["recommended_title"] == entry["title"]
        assert entry["original_title"] == "مقطع 1"
        assert entry["query_title_data"]["score"] > 0

    def test_refusal_drops_everything(self):
        segs = [_segment(0, OTHER)]
        result = {}
        kept = qt.apply_target_query(segs, "مصطفى بونيف تبون", result)
        assert kept == []
        assert result["target_query"]["refused"] is True
        assert result["target_query"]["refusal_reason"] == "no_segment_about_query"

    def test_multiple_genuine_segments_kept(self):
        second = "تبون صرح بالحوار مع بونيف في اجتماع خاص."
        segs = [_segment(0, ABOUT_QUERY), _segment(1, second, score=30.0)]
        kept = qt.apply_target_query(segs, "بونيف تبون", {})
        assert len(kept) == 2
        # best (higher relevance + score) first
        assert kept[0]["index"] == 0
