# -*- coding: utf-8 -*-
"""Editorial package planner for turning ranked clips into a coherent series.

This is an offline, deterministic post-selection layer. It never edits or
removes source candidates: it returns selected clips plus explicit exclusion
reasons so an editor can override every decision.
"""
from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path
from typing import Any

_WORD_RE = re.compile(r"[\w\u0600-\u06ff]+", re.UNICODE)


def _number(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if number == number and abs(number) != float("inf") else default
    except (TypeError, ValueError):
        return default


def _tokens(segment: dict) -> set[str]:
    text = str(segment.get("transcript_text") or segment.get("text") or "")
    return {word.casefold() for word in _WORD_RE.findall(text) if len(word) > 2}


def _similarity(left: dict, right: dict) -> float:
    a, b = _tokens(left), _tokens(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _topic(segment: dict) -> str:
    value = str(segment.get("topic") or segment.get("theme") or "").strip()
    return value.casefold() or "غير مصنف"


def plan_package(segments: list[dict], *, limit: int = 5,
                 max_per_topic: int = 2, min_similarity: float = 0.72,
                 min_gap_seconds: float = 0.0) -> dict:
    """Select a diverse clip package without mutating input.

    Candidates are considered by editorial score, then virality score, then
    original order. Same-source clips that overlap or sit closer than the
    requested gap are not both selected. Similar transcript windows are
    capped to reduce repetitive Shorts/Reels batches.
    """
    if limit < 1 or max_per_topic < 1:
        raise ValueError("limit and max_per_topic must be >= 1")
    if not isinstance(segments, list):
        raise TypeError("segments must be a list")
    try:
        min_similarity = float(min_similarity)
        min_gap_seconds = float(min_gap_seconds)
    except (TypeError, ValueError) as exc:
        raise ValueError("similarity and gap thresholds must be numeric") from exc
    if not math.isfinite(min_similarity) or not 0.0 <= min_similarity <= 1.0:
        raise ValueError("min_similarity must be finite and between 0 and 1")
    if not math.isfinite(min_gap_seconds) or min_gap_seconds < 0:
        raise ValueError("min_gap_seconds must be finite and >= 0")
    candidates = [(i, dict(item)) for i, item in enumerate(segments)
                  if isinstance(item, dict)]
    candidates.sort(key=lambda pair: (
        _number(pair[1].get("selection_score"), _number(pair[1].get("score"))),
        _number(pair[1].get("score")), -pair[0]), reverse=True)

    selected: list[dict] = []
    excluded: list[dict] = []
    topic_counts: dict[str, int] = {}

    for original_index, candidate in candidates:
        if len(selected) >= limit:
            excluded.append({"candidate": candidate, "reason": "package_limit"})
            continue
        topic = _topic(candidate)
        if topic_counts.get(topic, 0) >= max_per_topic:
            excluded.append({"candidate": candidate, "reason": "topic_cap"})
            continue

        duplicate_reason = None
        for prior in selected:
            candidate_source = candidate.get("source_id") or candidate.get("source")
            prior_source = prior.get("source_id") or prior.get("source")
            same_source = (candidate_source is not None
                           and str(candidate_source).strip() != ""
                           and candidate_source == prior_source)
            start = _number(candidate.get("start"), -1)
            end = _number(candidate.get("end"), -1)
            prior_start = _number(prior.get("start"), -1)
            prior_end = _number(prior.get("end"), -1)
            if same_source and min(start, end, prior_start, prior_end) >= 0:
                gap = max(prior_start - end, start - prior_end, 0.0)
                if gap == 0.0:
                    duplicate_reason = "source_time_overlap"
                    break
                if min_gap_seconds and gap < min_gap_seconds:
                    duplicate_reason = "source_time_too_close"
                    break
            if _similarity(candidate, prior) >= min_similarity:
                duplicate_reason = "transcript_near_duplicate"
                break
        if duplicate_reason:
            excluded.append({"candidate": candidate, "reason": duplicate_reason})
            continue

        candidate["package_order"] = len(selected) + 1
        candidate["package_topic"] = topic
        candidate["package_source_index"] = original_index
        selected.append(candidate)
        topic_counts[topic] = topic_counts.get(topic, 0) + 1

    return {
        "schema_version": 1,
        "selected": selected,
        "excluded": excluded,
        "summary": {
            "input_count": len(candidates),
            "selected_count": len(selected),
            "excluded_count": len(excluded),
            "topic_count": len(topic_counts),
            "topics": topic_counts,
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a diverse editorial clip package")
    parser.add_argument("input", type=Path, help="JSON file containing a list or {segments: [...]}")
    parser.add_argument("--output", type=Path, default=Path("clip_package.json"))
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--max-per-topic", type=int, default=2)
    parser.add_argument("--min-similarity", type=float, default=0.72)
    parser.add_argument("--min-gap-seconds", type=float, default=0.0)
    args = parser.parse_args(argv)
    try:
        payload = json.loads(args.input.read_text(encoding="utf-8-sig"))
        segments = payload.get("segments", []) if isinstance(payload, dict) else payload
        if not isinstance(segments, list):
            raise ValueError("input JSON must be a list or contain a segments list")
        result = plan_package(segments, limit=args.limit,
                              max_per_topic=args.max_per_topic,
                              min_similarity=args.min_similarity,
                              min_gap_seconds=args.min_gap_seconds)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(result["summary"], ensure_ascii=False))
        return 0
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        parser.error(str(exc))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
