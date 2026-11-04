# -*- coding: utf-8 -*-
"""Evidence-based Arabic dialogue cues for editorial review.

This module is intentionally heuristic: it labels observable linguistic cues,
not truth, sentiment, virality, or policy violations. Every cue includes the
matched phrase so an editor can verify it in context.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

CUE_PATTERNS = {
    "question": [r"\b(?:لماذا|كيف|متى|أين|هل|ماذا|من|واش|علاش|كيفاش|وين|شكون)\b"],
    "challenge": [r"\b(?:لكن|ولكن|غير أن|بالمقابل|هذا غير صحيح|لا أتفق|ماشي صحيح|بصح)\b"],
    "rebuttal": [r"\b(?:الرد هو|أجيبك|اسمح لي|الحقيقة أن|نرد عليك|خليني نوضح)\b"],
    "revelation": [r"\b(?:لأول مرة|المفاجأة|ما لا تعرفونه|الحقيقة|كشف|وثيقة|الدليل|نعطيك معلومة)\b"],
    "conclusion": [r"\b(?:الخلاصة|في النهاية|إذن|وبالتالي|لهذا السبب|باختصار|النتيجة)\b"],
    "call_to_action": [r"\b(?:اشتركوا|لا تنسوا|تابعونا|شاركوا|اكتبوا في التعليقات)\b"],
}
WORD_RE = re.compile(r"[\w\u0600-\u06ff]+", re.UNICODE)


def analyze_dialogue(text: str) -> dict[str, Any]:
    """Return explainable discourse cues for Arabic and common Maghrebi phrasing."""
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    normalized = re.sub(r"\s+", " ", text).strip()
    cues: list[dict[str, Any]] = []
    for label, patterns in CUE_PATTERNS.items():
        matches: list[str] = []
        for pattern in patterns:
            for match in re.finditer(pattern, normalized, flags=re.IGNORECASE):
                phrase = match.group(0)
                if phrase not in matches:
                    matches.append(phrase)
        if matches:
            cues.append({"type": label, "evidence": matches[:8], "count": len(matches)})
    words = WORD_RE.findall(normalized)
    # A cautious completeness proxy: explicit terminal punctuation or a
    # discourse conclusion. It must never be treated as semantic proof.
    has_terminal = bool(re.search(r"[.!؟?…]$", normalized))
    has_conclusion = any(c["type"] == "conclusion" for c in cues)
    completeness = "strong_signal" if has_terminal and has_conclusion else (
        "possible_signal" if has_terminal or has_conclusion else "unknown")
    return {
        "schema_version": 1,
        "language_scope": "Arabic / common Maghrebi cues",
        "word_count": len(words),
        "cues": cues,
        "dialogue_shape": [c["type"] for c in cues],
        "ending_completeness_proxy": completeness,
        "limitations": [
            "Heuristic phrase matching; dialect coverage is incomplete.",
            "A cue does not establish factual accuracy, intent, virality, or policy violation.",
            "Review surrounding audio/video before selecting or publishing a clip.",
        ],
    }


def analyze_segments(segments: list[dict]) -> list[dict]:
    """Copy candidates and attach explainable dialogue analysis; never mutate input."""
    if not isinstance(segments, list):
        raise TypeError("segments must be a list")
    output = []
    for item in segments:
        if not isinstance(item, dict):
            continue
        candidate = dict(item)
        text = candidate.get("transcript_text") or candidate.get("text") or ""
        candidate["dialogue_intelligence"] = analyze_dialogue(str(text))
        output.append(candidate)
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Annotate Arabic dialogue cues in clip JSON")
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path, default=Path("dialogue_analysis.json"))
    args = parser.parse_args(argv)
    try:
        payload = json.loads(args.input.read_text(encoding="utf-8-sig"))
        segments = payload.get("segments", []) if isinstance(payload, dict) else payload
        result = {"schema_version": 1, "segments": analyze_segments(segments)}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Analyzed {len(result['segments'])} segments -> {args.output}")
        return 0
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        parser.error(str(exc))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
