# -*- coding: utf-8 -*-
"""Transcript-click cutting for the review tab (round 2 item 8).

The reviewer sees the SOURCE transcript (TSV/SRT — the Whisper artifacts; the
item-3b YouTube-caption path writes the same artifacts), clicks a sentence row
to load its [start, end] into the cut fields, optionally types a target query
for the title, and appends the cut to ``viral_segments.txt`` with
``origin="manual_cut"``.

Every cut runs the same gauntlet as the automatic pipeline:
``query_targeting.manual_clip_proposal`` (which validates via
``segment_validator.validate_final_segment``, hook-scores via
``hook_analyzer.analyze_opening`` and titles via ``seo_titles``/the query-led
title). A cut that fails validation is refused with explicit reasons instead
of shipping a broken segment.

Whisper fallback: when no caption-derived transcript exists,
``segment_transcript.load_transcript`` reads ``input.tsv`` / ``input.srt``
(WhisperX artifacts) — the same timestamps the Short editor consumes.
"""

from __future__ import annotations

import os
import sys
from typing import Any, Dict, List, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import segment_transcript
from scripts import transcript_window
from scripts import query_targeting

from webui import segments_review

HEADERS = ["#", "Start", "End", "Sentence"]

__all__ = ["HEADERS", "transcript_rows", "sentence_rows", "append_manual_cut"]


def _window_text(segments: List[Dict[str, Any]], start: float, end: float) -> str:
    """Transcript text overlapping the selected window (ordered, deduped)."""
    parts: List[str] = []
    for seg in segments or []:
        try:
            seg_start = float(seg.get("start", seg.get("start_time", 0)) or 0)
            seg_end = float(seg.get("end", seg.get("end_time", 0)) or 0)
        except (TypeError, ValueError):
            continue
        text = str(seg.get("text") or "").strip()
        if not text or seg_end <= start or seg_start >= end:
            continue
        parts.append(text)
    return " ".join(parts).strip()


def _load_transcript(project_path: str) -> List[Dict[str, Any]]:
    """Parsed transcript segments; ``[]`` when the project has none."""
    try:
        return segment_transcript.load_transcript(str(project_path or "")) or []
    except (ValueError, OSError, TypeError):
        return []


def transcript_rows(project_path: str, max_rows: int = 600) -> List[List[Any]]:
    """Raw transcript rows: [index, start, end, text] from Whisper artifacts."""
    segments = _load_transcript(str(project_path or ""))
    rows: List[List[Any]] = []
    for seg in segments or []:
        try:
            start = float(seg.get("start", seg.get("start_time", 0)) or 0)
            end = float(seg.get("end", seg.get("end_time", 0)) or 0)
        except (TypeError, ValueError):
            continue
        text = str(seg.get("text") or "").strip()
        if not text:
            continue
        rows.append([len(rows) + 1, round(start, 2), round(end, 2), text[:160]])
        if len(rows) >= max_rows:
            break
    return rows


def sentence_rows(project_path: str, max_rows: int = 400) -> List[List[Any]]:
    """Sentence-level rows with absolute [start, end] times.

    One row per sentence inside each transcript segment. Word clocks from
    ``input.json`` (WhisperX) are used when they cover the segment; otherwise
    each sentence owns the slice its characters occupy inside the segment
    window (``query_targeting.sentences_with_bounds``).
    """
    segments = _load_transcript(str(project_path or ""))
    if not segments:
        return []
    words = transcript_window.load_word_timings(str(project_path or ""))
    rows: List[Dict[str, Any]] = []
    for seg in segments or []:
        try:
            start = float(seg.get("start", seg.get("start_time", 0)) or 0)
            end = float(seg.get("end", seg.get("end_time", 0)) or 0)
        except (TypeError, ValueError):
            continue
        text = str(seg.get("text") or "").strip()
        if not text or end <= start:
            continue
        seg_words = [
            word
            for word in words
            if isinstance(word, dict)
            and float(word.get("start") or 0) >= start - 0.05
            and float(word.get("end") or 0) <= end + 0.05
        ]
        # Sentence bounds: character-proportional inside the segment window,
        # sharpened to the real word clocks when WhisperX word timings cover
        # the segment (speech ends where the last word ends, not where the
        # TSV row does — trailing silence stays out of the cut).
        sentences = query_targeting._sentences_with_bounds(text)
        covered = bool(seg_words) and sum(
            len(part.split()) for _, _, part in sentences
        ) <= len(seg_words)
        cursor = 0
        for order, (_, _, sentence_text) in enumerate(sentences):
            sentence_text = str(sentence_text or "").strip()
            if not sentence_text:
                continue
            if covered:
                count = len(sentence_text.split())
                s = float(seg_words[cursor].get("start") or start)
                e = float(
                    seg_words[min(cursor + count, len(seg_words)) - 1].get("end")
                    or end
                )
                cursor += count
            else:
                total_chars = sum(len(p) for _, _, p in sentences) or 1
                char_pos = sum(len(p) for _, _, p in sentences[:order])
                s = start + (end - start) * (char_pos / total_chars)
                e = start + (end - start) * ((char_pos + len(sentence_text)) / total_chars)
            rows.append(
                {
                    "order": len(rows) + 1,
                    "start_time": round(max(start, s), 3),
                    "end_time": round(min(end, e) if e > s else e, 3),
                    "text": sentence_text[:160],
                }
            )
            if len(rows) >= max_rows:
                return rows
    return rows


def append_manual_cut(
    project_path: str,
    start: float,
    end: float,
    query: str = "",
) -> Tuple[Dict[str, Any] | None, List[str]]:
    """Validate + append one transcript-selected cut to viral_segments.txt.

    Returns ``(proposal, [])`` on success or ``(None, reasons)`` when the
    window is not shippable. The proposal carries ``origin="manual_cut"`` so
    the review table can tell manual cuts from AI selections.
    """
    project_path = str(project_path or "")
    if not project_path or not os.path.isdir(project_path):
        return None, ["no_project"]
    try:
        start = float(start)
        end = float(end)
    except (TypeError, ValueError):
        return None, ["invalid_window"]
    if end <= start:
        return None, ["invalid_window"]
    segments = _load_transcript(project_path)
    text = _window_text(segments, start, end)
    if not text:
        return None, ["no_transcript"]
    result = query_targeting.manual_clip_proposal(
        {
            "start_time": round(start, 3),
            "end_time": round(end, 3),
            "duration": round(end - start, 3),
            "transcript_text": text,
        },
        str(query or "").strip(),
        min_duration=5.0,
        max_duration=90.0,
    )
    if not result.get("ok"):
        reasons = [str(reason) for reason in result.get("reasons") or []]
        return None, reasons or ["invalid_window"]
    proposal = result["proposal"]

    payload = segments_review._read_payload(project_path) or {"segments": []}
    existing = payload.get("segments")
    if not isinstance(existing, list):
        existing = []
    existing.append(proposal)
    payload["segments"] = segments_review._renumber_candidate_ranks(existing)
    segments_review._write_payload(project_path, payload)
    return proposal, []


def _seed_segments(project_path: str) -> None:
    """Write an empty viral_segments.txt payload (test helper)."""
    segments_review._write_payload(project_path, {"segments": []})


def _load_payload(project_path: str):
    """Read the full viral_segments.txt payload dict (test helper)."""
    return segments_review._read_payload(project_path)
