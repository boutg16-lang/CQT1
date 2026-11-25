# -*- coding: utf-8 -*-
"""Self-certification preflight — الإجابة المبدئية على استبيان يوتيوب.

Before a Short is uploaded, YouTube's self-certification questionnaire asks
the CREATOR whether the content contains each policy category. Answering it
wrong is how channels earn strikes — so this module pre-answers it from the
content itself: a deterministic local scan of the clip's transcript, title
and caption, mapped onto the questionnaire categories.

The YouTube Data API has NO field for self-certification (it is Studio-only),
so the module:
  1. computes the answer set (per category: contains / signals / confidence),
  2. flags ``needs_manual_confirmation`` whenever any signal matched — the
     creator must open the Studio editor and confirm or correct,
  3. provides ``studio_selfcert_url(video_id)`` — the deep link that opens
     the right Studio page for that manual confirmation.

Reuses scripts/safety_filter's multilingual blocklist for policy signals
(hate/harassment/violence/sexual/self-harm/...) and adds light topic regexes
for categories the blocklist does not cover. 100% local, no LLM, fail-safe:
a scan error must never block an upload.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from typing import Any, Dict, List

sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))

from scripts import safety_filter  # noqa: E402

# The self-certification questionnaire categories (Studio order, stable ids).
CATEGORIES = (
    "adult",
    "dangerous_acts",
    "hateful",
    "harassment",
    "child_safety",
    "violent_graphic",
    "suicide_self_harm",
    "spam_deceptive",
    "controversial",
    "terrorism",
)

# safety_filter blocklist category -> questionnaire category.
_BLOCKLIST_CATEGORY_MAP = {
    "hate_slur": "hateful",
    "hate_dehumanize": "hateful",
    "harassment": "harassment",
    "violence_threat": "violent_graphic",
    "profanity": "hateful",
    "sexual_explicit": "adult",
    "self_harm": "suicide_self_harm",
    "scam_fraud": "spam_deceptive",
    "gambling": "spam_deceptive",
    "misinformation": "spam_deceptive",
    "ai_deception": "spam_deceptive",
}

# Light topic regexes for categories the blocklist does not cover well.
# Deliberately narrow: a false "yes" costs the creator a manual check, a
# missed "yes" costs a strike — so only strong surface signals count.
_TOPIC_PATTERNS: Dict[str, tuple] = {
    "child_safety": (
        re.compile(r"(طفل|أطفال|اطفال|قاصر|بيبي|child|minors?)", re.IGNORECASE),
    ),
    "terrorism": (
        re.compile(
            r"(داعش|القاعدة|تنظيم.? الدولة|isis|al.?qaeda|boko haram|تنظيم.? القاعدة)",
            re.IGNORECASE,
        ),
    ),
    "dangerous_acts": (
        re.compile(
            r"(تحدي|تحدّي|خطر على الحياة|لا تحاول|do not try|extreme challenge)",
            re.IGNORECASE,
        ),
    ),
    "controversial": (
        re.compile(
            r"(الإجهاض|اجهاض|الحرب الأهلية|الانفصال|عقد بين|سحر|شعوذة|abortion|civil war)",
            re.IGNORECASE,
        ),
    ),
    "spam_deceptive": (
        re.compile(
            r"(رابط في الوصف|اشترك وربح|اربح المال|كسب سريع|get rich|free money|clickbait)",
            re.IGNORECASE,
        ),
    ),
}

# Blocklist severities that count as real signals for the questionnaire.
_SIGNAL_SEVERITIES = ("high", "medium")
_LOW_SEVERITY_WEIGHT = 0.35
_MATCH_WEIGHT = {"high": 1.0, "medium": 0.7, "low": _LOW_SEVERITY_WEIGHT}
_CONFIRM_THRESHOLD = 0.7


def studio_selfcert_url(video_id: str) -> str:
    """Studio deep-link that opens the video editor (self-cert section)."""
    video_id = str(video_id or "").strip()
    if not video_id:
        return ""
    return "https://studio.youtube.com/video/{}/edit".format(video_id)


def scan_text(text: str) -> List[Dict[str, Any]]:
    """All policy signals in *text* — blocklist matches + topic regexes."""
    raw = str(text or "")
    signals: List[Dict[str, Any]] = []
    try:
        for match in safety_filter.find_matches(raw, min_severity="low"):
            category = _BLOCKLIST_CATEGORY_MAP.get(match.get("category") or "")
            if not category:
                continue
            signals.append(
                {
                    "category": category,
                    "term": str(match.get("term") or match.get("raw") or ""),
                    "severity": str(match.get("severity") or "low"),
                    "source": "safety_filter",
                }
            )
    except Exception:
        pass
    for category, patterns in _TOPIC_PATTERNS.items():
        for pattern in patterns:
            found = pattern.search(raw)
            if found:
                signals.append(
                    {
                        "category": category,
                        "term": found.group(0),
                        "severity": "medium",
                        "source": "topic_pattern",
                    }
                )
    return signals


def self_certify(
    transcript: str = "",
    *,
    title: str = "",
    caption: str = "",
) -> Dict[str, Any]:
    """Pre-answer the questionnaire from the clip's own content.

    Returns a JSON-safe report:
      ``categories``   — per category: contains / confidence / signals
      ``recommended``  — the checkbox set the creator should confirm
      ``needs_manual_confirmation`` — True when any signal matched (Studio)
      ``clean``        — True when no category matched at all
    """
    signals = scan_text(" \n".join([title, caption, transcript]))
    by_category: Dict[str, List[Dict[str, Any]]] = {}
    for signal in signals:
        by_category.setdefault(signal["category"], []).append(signal)

    categories: Dict[str, Dict[str, Any]] = {}
    for name in CATEGORIES:
        matched = by_category.get(name) or []
        weight = 0.0
        seen_terms = []
        for signal in matched:
            weight = max(weight, _MATCH_WEIGHT.get(signal["severity"], 0.0))
            term = signal["term"]
            if term and term not in seen_terms:
                seen_terms.append(term)
        contains = bool(matched) and weight >= _CONFIRM_THRESHOLD
        categories[name] = {
            "contains": contains,
            "confidence": round(weight, 2),
            "signals": seen_terms[:5],
            "signal_count": len(matched),
        }

    any_signal = bool(signals)
    return {
        "basis": "local_transcript_scan",
        "categories": categories,
        "recommended": {
            "contains_none": not any_signal,
            "contains_yes": [name for name in CATEGORIES if categories[name]["contains"]],
        },
        "needs_manual_confirmation": any_signal,
        "clean": not any_signal,
    }


def record_answer(report: Dict[str, Any], video_id: str) -> Dict[str, Any]:
    """Attach the Studio deep-link for manual confirmation to a report."""
    out = dict(report)
    out["studio_url"] = studio_selfcert_url(video_id)
    return out


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--text", default="", help="Transcript text to scan")
    parser.add_argument("--title", default="")
    parser.add_argument("--caption", default="")
    parser.add_argument("--file", default="", help="Read transcript from a file")
    parser.add_argument("--video-id", default="", help="Attach Studio deep-link")
    args = parser.parse_args(argv)
    text = args.text
    if args.file:
        with open(args.file, "r", encoding="utf-8") as handle:
            text = handle.read()
    report = self_certify(text, title=args.title, caption=args.caption)
    if args.video_id:
        report = record_answer(report, args.video_id)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
