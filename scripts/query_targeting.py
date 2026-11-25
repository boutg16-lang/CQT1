# -*- coding: utf-8 -*-
"""Query-targeted cutting — اختيار المقاطع حسب استعلام البحث.

``--target-query "..."`` (and the WebUI field) makes the cutter pick the
segment that is genuinely ABOUT the query instead of the generically
strongest one, and titles it with the query leading so the Short can rank
for that exact search (measured gap: the channel ranked #1 for the speaker's
name but not for "مصطفى بونيف تبون"-style queries).

Three rules, in order:

1. **Match semantically** — a segment matches when the query's words appear
   in the words actually spoken inside its cut window. Arabic orthography
   variants (أ/ا, ة/ه, tashkeel, tatweel…) compare equal via
   ``scripts.segment_titles._normalized_match_text``. When the optional
   embeddings backend (``scripts.semantic_embeddings``) is configured, a
   cosine similarity signal refines the lexical score — but the lexical
   ground truth always gates: a segment whose transcript never mentions the
   query is never selected, whatever the embedding says.
2. **Refuse unrelated segments** — when the best segment's relevance is
   below ``min_relevance`` the tool returns nothing and reports a refusal.
   No misleading titles: if the video never discusses the query, no clip is
   cut for it.
3. **Query-led titles** — the winning title starts with the query itself and
   is scored with ``scripts.seo_titles.score_title``. The pattern reuses the
   v8.11 funnel presets from ``scripts.seo_titles``; nothing here duplicates
   that scoring logic.

Pure stdlib; the embeddings path degrades to lexical matching offline.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import os
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.segment_titles import (
    _normalized_match_text,
    _title_quality_score,
    _window_text_from_transcript,
)

logger = logging.getLogger(__name__)

# A segment below this relevance is refused even when it is the best one.
DEFAULT_MIN_RELEVANCE = 0.4

# Function words that carry no topic signal when matching a query.
_QUERY_STOPWORDS = frozenset(
    {
        "في",
        "من",
        "على",
        "عن",
        "الى",
        "إلى",
        "مع",
        "هذا",
        "هذه",
        "التي",
        "الذي",
        "ما",
        "هل",
        "و",
        "أو",
        "او",
        "the",
        "a",
        "an",
        "of",
        "in",
        "on",
        "to",
        "is",
        "are",
    }
)


def query_tokens(query: str) -> List[str]:
    """Content tokens of the query, orthography-normalized, stopwords out."""
    tokens = [
        token
        for token in _normalized_match_text(query).split()
        if token and token not in _QUERY_STOPWORDS
    ]
    return tokens


def _segment_text(segment: Dict[str, Any], window_text: Optional[str]) -> str:
    """The ground-truth words of a segment (real window text first)."""
    if window_text:
        return str(window_text)
    return str(
        segment.get("transcript_text")
        or segment.get("hook_text")
        or segment.get("caption")
        or segment.get("title")
        or ""
    )


def query_relevance(
    query: str,
    segment: Dict[str, Any],
    *,
    window_text: Optional[str] = None,
    semantic_similarity: Optional[float] = None,
) -> float:
    """0..1: how much of the query the segment's real words actually cover.

    ``semantic_similarity`` (optional, 0..1) adds at most a 0.1 bonus when
    the lexical evidence already exists; it can never rescue a segment with
    zero lexical overlap into a "match" — that is the anti-misleading-titles
    guarantee. ``window_text`` (the actual cut-window words, e.g. from
    ``_window_text_from_transcript``) overrides the stored transcript text.
    """
    tokens = query_tokens(query)
    if not tokens:
        return 0.0
    text = _normalized_match_text(_segment_text(segment, window_text))
    if not text:
        return 0.0
    text_tokens = set(text.split())
    matched = 0
    for token in tokens:
        if " " in token:
            # multiword token can only come from a quoted phrase; substring
            # match on the normalized text is the correct test there
            if token in text:
                matched += 1
        elif token in text_tokens:
            matched += 1
    lexical = matched / len(tokens)
    if semantic_similarity and lexical > 0:
        bonus = min(0.1, max(0.0, float(semantic_similarity)) * 0.1)
        return min(1.0, lexical + bonus)
    return lexical


def _semantic_similarities(
    query: str, texts: List[str], allow_auto: bool = False
) -> List[Optional[float]]:
    """Cosine similarity of each text against the query (None when offline)."""
    try:
        from scripts import semantic_embeddings

        vectors = semantic_embeddings.embed_texts(
            [query] + list(texts), allow_auto=allow_auto
        )
        if not vectors or len(vectors) != len(texts) + 1:
            return [None] * len(texts)
        query_vector = vectors[0]
        out = []
        for vector in vectors[1:]:
            try:
                out.append(max(0.0, min(1.0, semantic_embeddings.similarity(
                    query_vector, vector
                ))))
            except Exception:
                out.append(None)
        return out
    except Exception:
        return [None] * len(texts)


def rank_segments_for_query(
    query: str,
    segments: List[Dict[str, Any]],
    *,
    min_relevance: float = DEFAULT_MIN_RELEVANCE,
    allow_auto_embeddings: bool = False,
) -> Dict[str, Any]:
    """Rank every segment by genuine query relevance.

    Returns ``{"query", "min_relevance", "candidates": [...], "best": ...,
    "refused": bool, "refusal_reason"}``. ``best`` is the winning segment
    dict annotated with ``query_targeting`` metadata, or None when refused.
    The input list is never mutated; annotation happens on copies.
    """
    query = str(query or "").strip()
    if not query:
        return {"query": "", "refused": True, "refusal_reason": "empty_query"}
    texts = [_segment_text(seg, None) for seg in segments]
    similarities = _semantic_similarities(query, texts, allow_auto_embeddings)

    candidates = []
    for index, segment in enumerate(segments):
        window_text = _window_text_from_transcript(
            segment.get("transcript") or [],
            segment.get("start_time"),
            segment.get("end_time"),
            segment.get("words"),
        ) or None
        relevance = query_relevance(
            query,
            segment,
            window_text=window_text,
            semantic_similarity=similarities[index]
            if index < len(similarities)
            else None,
        )
        entry = dict(segment)
        entry["_query_relevance"] = round(float(relevance), 3)
        entry["_query_window_text"] = window_text or ""
        candidates.append(entry)

    candidates.sort(
        key=lambda item: (
            item["_query_relevance"],
            float(item.get("selection_score") or 0.0),
        ),
        reverse=True,
    )
    best = candidates[0] if candidates else None
    if best is None or best["_query_relevance"] < float(min_relevance):
        return {
            "query": query,
            "min_relevance": min_relevance,
            "candidates": candidates,
            "best": None,
            "refused": True,
            "refusal_reason": "no_segment_about_query",
        }
    return {
        "query": query,
        "min_relevance": min_relevance,
        "candidates": candidates,
        "best": best,
        "refused": False,
        "refusal_reason": "",
    }


def build_query_title(
    query: str,
    segment: Optional[Dict[str, Any]] = None,
    *,
    keywords: Optional[List[str]] = None,
    prefer_template: str = "",
    winners: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Query-led title candidates ranked by ``seo_titles.score_title``.

    Grounded by construction: the question/quote part is taken VERBATIM from
    the segment's own transcript words (never generated), so the title cannot
    claim anything the clip does not say. The funnel suffix comes from the
    ``seo_titles`` presets ("{hook} | الفيديو الكامل").
    """
    from scripts import seo_titles

    query = str(query or "").strip()
    if not query:
        return {"title": "", "score": 0.0, "pattern": "", "candidates": []}
    search_keywords = list(keywords or [])
    if query not in search_keywords:
        search_keywords.insert(0, query)

    candidates: List[Dict[str, Any]] = []
    for pattern_key, pattern in (
        ("query_question", seo_titles.TITLE_PRESETS["query_question"]),
        ("funnel_full", seo_titles.TITLE_PRESETS["funnel_full"]),
    ):
        if pattern_key == "query_question":
            question = _grounded_question(query, segment)
            if not question:
                continue
            title = pattern.format(query=query, question=question)
        else:
            # The funnel preset keeps the measured-winner shape, but the hook
            # slot still leads with the QUERY itself (item 3 contract: the
            # title must start with the query). The transcript quote is only
            # a grounding reference — never the title head.
            hook = query
            title = pattern.format(hook=hook)
        title = re.sub(r"\s+", " ", title).strip()
        if not title:
            continue
        candidates.append(
            {
                "title": title,
                "pattern": pattern_key,
                **seo_titles.score_title(title, search_keywords),
            }
        )
    # Item 8 (feat/pro-cutting): past WINNING TITLES reused as templates — the
    # winner's measured suffix is kept verbatim, the hook slot becomes the new
    # query. Each candidate is factually validated against THIS clip's own
    # transcript, so an inherited tail the clip does not back is dropped.
    candidate_titles = {c["title"].casefold() for c in candidates}
    for item in seo_titles.titles_from_winners(
        [w for w in (winners or []) if str(w or "").strip()][:4],
        query,
        keywords=search_keywords,
        limit=4,
        transcript_text=_segment_text(segment or {}, None) or None,
    ):
        if item["title"].casefold() in candidate_titles:
            continue
        candidates.append(item)
    candidates.sort(key=lambda item: item["score"], reverse=True)
    # Item 6: when the channel's own insights proved a title template
    # winning, prefer it — but only when it still leads with the query and
    # does not score in the gutter (a winning pattern cannot rescue a bad
    # title).
    preferred = str(prefer_template or "").strip()
    if preferred:
        for index, candidate in enumerate(candidates):
            if candidate.get("pattern") == preferred and candidate["score"] >= 40.0:
                if candidates[0] is not candidate:
                    candidates.insert(0, candidates.pop(index))
                break
    # Item 3 contract: the shipped title ALWAYS leads with the query.
    candidates = [c for c in candidates if c["title"].startswith(query)] or candidates
    if not candidates:
        fallback = "{} | الفيديو الكامل".format(query)
        return {
            "title": fallback,
            "score": seo_titles.score_title(fallback, search_keywords)["score"],
            "pattern": "funnel_full",
            "candidates": [],
        }
    return {
        "title": candidates[0]["title"],
        "score": candidates[0]["score"],
        "breakdown": candidates[0].get("breakdown"),
        "pattern": candidates[0]["pattern"],
        "candidates": candidates,
    }


def _grounded_question(query: str, segment: Optional[Dict[str, Any]]) -> str:
    """A question sentence from the segment transcript that contains query
    words — verbatim transcript words only, trimmed to title length."""
    text = _segment_text(segment or {}, None)
    if not text:
        return ""
    query_tokens_set = set(query_tokens(query))
    best_sentence = ""
    best_score = -1.0
    for sentence in re.split(r"[.!؟?\n]+", text):
        sentence = re.sub(r"\s+", " ", sentence).strip(" \t،؛:|-")
        if not sentence or len(sentence) < 12:
            continue
        sentence_tokens = set(_normalized_match_text(sentence).split())
        overlap = len(sentence_tokens & query_tokens_set)
        if not overlap:
            continue
        score = overlap + _title_quality_score(sentence) / 100.0
        if score > best_score:
            best_score = score
            best_sentence = sentence
    if not best_sentence:
        return ""
    if not best_sentence.endswith(("؟", "?")):
        best_sentence += "؟"
    if len(best_sentence) > 60:
        best_sentence = best_sentence[:60].rsplit(" ", 1)[0].rstrip("،؛: -") + "؟"
    return best_sentence


def apply_target_query(
    segments: List[Dict[str, Any]],
    query: str,
    result: Optional[Dict[str, Any]] = None,
    *,
    min_relevance: float = DEFAULT_MIN_RELEVANCE,
    project_folder: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Filter ``segments`` in place semantics: return the kept list.

    Kept segments are annotated (``query``, ``query_relevance``,
    ``query_title_data``) and get the query-led title as ``title`` /
    ``recommended_title`` (the previous title is preserved in
    ``original_title``). Dropped segments stay listed in
    ``result["target_query"]["dropped"]`` when ``result`` is given.
    """
    query = str(query or "").strip()
    report = rank_segments_for_query(query, segments, min_relevance=min_relevance)
    kept: List[Dict[str, Any]] = []
    dropped: List[Dict[str, Any]] = []
    title_data: Dict[str, Any] = {}
    preferred_template = ""
    winner_titles: List[str] = []
    if project_folder:
        try:
            from scripts import performance_weights

            insights = performance_weights._load(project_folder)
            preferred_template = performance_weights.preferred_title_template(insights)
            # Item 8: literal past-winning titles (highest measured views)
            # become reusable templates for the query-led title.
            winner_titles = [
                str(row.get("title") or "").strip()
                for row in (
                    (insights.get("content_insights") or {}).get("top_titles") or []
                )
                if str(row.get("title") or "").strip()
            ]
        except Exception:
            preferred_template = ""
            winner_titles = []
    if not report.get("refused"):
        best = report["best"]
        title_data = build_query_title(
            query,
            best,
            prefer_template=preferred_template,
            winners=winner_titles,
        )
        for candidate in report["candidates"]:
            if candidate is best or (
                candidate.get("start_time") == best.get("start_time")
                and candidate.get("end_time") == best.get("end_time")
            ):
                entry = {
                    key: value
                    for key, value in candidate.items()
                    if not key.startswith("_query_window_text")
                }
                entry["query"] = query
                entry["query_relevance"] = candidate["_query_relevance"]
                entry["original_title"] = str(
                    candidate.get("recommended_title") or candidate.get("title") or ""
                )
                if title_data.get("title"):
                    entry["title"] = title_data["title"]
                    entry["recommended_title"] = title_data["title"]
                entry["query_title_data"] = {
                    k: v for k, v in title_data.items() if k != "candidates"
                }
                kept.append(entry)
            elif candidate["_query_relevance"] >= min_relevance:
                # genuinely about the query too — keep it ranked behind best
                entry = dict(candidate)
                entry["query"] = query
                entry["query_relevance"] = candidate["_query_relevance"]
                kept.append(entry)
            else:
                dropped.append(
                    {
                        "title": str(candidate.get("title") or ""),
                        "start_time": candidate.get("start_time"),
                        "end_time": candidate.get("end_time"),
                        "query_relevance": candidate["_query_relevance"],
                    }
                )
    else:
        for candidate in report.get("candidates", []):
            dropped.append(
                {
                    "title": str(candidate.get("title") or ""),
                    "start_time": candidate.get("start_time"),
                    "end_time": candidate.get("end_time"),
                    "query_relevance": candidate["_query_relevance"],
                }
            )
    if result is not None:
        result["target_query"] = {
            "query": query,
            "min_relevance": min_relevance,
            "refused": bool(report.get("refused")),
            "refusal_reason": report.get("refusal_reason", ""),
            "kept": len(kept),
            "dropped": dropped,
            "title": title_data.get("title", ""),
            "title_score": title_data.get("score", 0.0),
        }
    if not kept:
        logger.warning(
            "Target query '%s': no segment is genuinely about it — refusing "
            "to cut (no misleading titles).",
            query,
        )
    return kept


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Rank viral segments against a search query (--target-query engine)."
    )
    parser.add_argument("--segments-json", required=True, help="viral_segments.txt JSON path")
    parser.add_argument("--query", required=True)
    parser.add_argument(
        "--min-relevance", type=float, default=DEFAULT_MIN_RELEVANCE
    )
    args = parser.parse_args(argv)
    with open(args.segments_json, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    segments = data.get("segments", []) if isinstance(data, dict) else []
    report = rank_segments_for_query(
        args.query, segments, min_relevance=args.min_relevance
    )
    best = report.get("best")
    title = build_query_title(args.query, best) if best else {}
    print(
        json.dumps(
            {
                "query": args.query,
                "refused": report.get("refused"),
                "refusal_reason": report.get("refusal_reason", ""),
                "best": (
                    {
                        "title": best.get("title"),
                        "start_time": best.get("start_time"),
                        "end_time": best.get("end_time"),
                        "relevance": best.get("_query_relevance"),
                    }
                    if best
                    else None
                ),
                "query_title": title.get("title", ""),
                "query_title_score": title.get("score", 0.0),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


__all__ = [
    "DEFAULT_MIN_RELEVANCE",
    "apply_target_query",
    "build_query_title",
    "query_relevance",
    "query_tokens",
    "rank_segments_for_query",
]
