# -*- coding: utf-8 -*-
"""
SEO title & keywords engine (v7.22) — عناوين SEO ذكية للمقاطع القصيرة.

What it does
------------
1. **YouTube autocomplete fetch** — uses YouTube's public suggestion API
   (``suggestqueries.google.com``, no key needed) to find what people
   actually search around a topic:
   ``python scripts/seo_titles.py suggest --topic "كيف تكسب المال"``
2. **Title scoring** — a transparent heuristic score (0-100) rewarding
   hook strength, keyword presence, length in the 35-70 char sweet spot,
   question/urgency patterns, and number presence; punishing ALL-CAPS,
   clickbait repetition and keyword stuffing.
3. **Title generation** — deterministic template-based candidates from a
   topic + keywords + angle, ranked by the same scorer.
4. **Best publish-time hints** — a small built-in table of engagement
   windows (conservative, editable) so the scheduler has a sane default.

Everything is offline-safe: the suggest fetch degrades to a keyword-only
result when the network is unavailable.
"""

from __future__ import annotations

import argparse
import json
import re
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any

SUGGEST_URL = "https://suggestqueries.google.com/complete/search"
FETCH_TIMEOUT = 8
MAX_SUGGESTIONS = 10

# Conservative engagement windows (local time). Tune per channel: these are
# defaults, not gospel — the scheduler combines them with the user's own
# chosen window when provided.
BEST_TIMES_BY_PLATFORM = {
    "youtube": {"weekday": [(17, 21)], "weekend": [(10, 13), (17, 21)]},
    "tiktok": {"weekday": [(12, 14), (19, 23)], "weekend": [(10, 12), (19, 23)]},
    "reels": {"weekday": [(12, 14), (18, 21)], "weekend": [(11, 13), (18, 21)]},
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# Autocomplete
# ---------------------------------------------------------------------------


def fetch_suggestions(
    topic: str,
    language: str = "ar",
    region: str = "",
    max_results: int = MAX_SUGGESTIONS,
) -> list[str]:
    """Query YouTube's public autocomplete API.

    Returns a de-duplicated list of suggestion strings. On any network /
    parse failure returns [] (callers treat that as "offline, use topic").
    """
    params = {
        "client": "youtube",
        "ds": "yt",
        "hl": language or "ar",
        "q": topic,
    }
    if region:
        params["gl"] = region
    url = "{}?{}".format(SUGGEST_URL, urllib.parse.urlencode(params))
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as resp:
            raw = resp.read(512 * 1024).decode("utf-8", errors="replace")
        data = _parse_suggest_response(raw)
        suggestions = data[1] if isinstance(data, list) and len(data) > 1 else []
        seen = set()
        out = []
        for item in suggestions:
            text = _suggestion_text(item)
            key = text.casefold()
            if text and key not in seen:
                seen.add(key)
                out.append(text)
            if len(out) >= max_results:
                break
        return out
    except Exception:
        return []


def _suggestion_text(item) -> str:
    """Extract the plain suggestion string from either a list item or the
    stringified form YouTube sometimes returns (``"['make money', 0, ...]"``)."""
    if isinstance(item, list) and item:
        return str(item[0]).strip()
    text = str(item).strip()
    if text.startswith("[") and text.endswith("]") and text[1:2] in ("'", '"'):
        # stringified list: ['make money', 0, [512]]
        inner = (
            text[2 : text.rfind("'")] if text[1:2] == "'" else text[2 : text.rfind('"')]
        )
        return inner.strip()
    return text


def _parse_suggest_response(raw: str):
    """Parse the autocomplete response — plain JSON OR the
    ``window.google.ac.h([...])`` wrapper YouTube sometimes returns."""
    text = str(raw).strip()
    if not text:
        return []
    if text.startswith("window.google.ac.h("):
        start = text.find("(")
        end = text.rfind(")")
        if start != -1 and end > start:
            text = text[start + 1 : end]
    try:
        return json.loads(text)
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

# Hook words matched on WORD boundaries (substring matching used to reward
# "how" inside "show" or "why" inside "anywhere"). Includes Moroccan Darija
# variants (كيفاش/علاش/شنو/واش...) so dialect content scores like MSA.
_HOOK_WORDS_RE = re.compile(
    r"(?<!\w)(?:كيف|كيفاش|لماذا|علاش|ما هو|شنو|اشنو|واش|شحال|طريقة|أفضل|سر|خطأ|جديد"
    r"|how|why|best|secret|mistake|trick|tips)(?!\w)"
)


def _len_score(title: str) -> float:
    n = len(title)
    if 35 <= n <= 70:
        return 25.0
    if 20 <= n < 35 or 70 < n <= 90:
        return 15.0
    if 10 <= n < 20 or 90 < n <= 110:
        return 8.0
    return 0.0


def _hook_score(title: str) -> float:
    score = 0.0
    if title.endswith(("?", "؟")):
        score += 8.0
    elif title.endswith("!"):
        score += 4.0
    if re.search(r"\d", title):
        score += 6.0
    if _HOOK_WORDS_RE.search(title.casefold()):
        score += 7.0
    if title.count("!") > 2 or title.count("؟") > 2:
        score -= 6.0
    return max(0.0, score)


def _keyword_score(title: str, keywords: list[str]) -> float:
    if not keywords:
        return 5.0
    folded = title.casefold()
    hits = sum(1 for k in keywords if k and k.casefold() in folded)
    if hits == 0:
        return 0.0
    if hits == 1:
        return 8.0
    return min(15.0, 8.0 + (hits - 1) * 3.5)


def _penalty_score(title: str) -> float:
    penalty = 0.0
    if title.isupper() and any(c.isalpha() for c in title):
        penalty += 15.0
    # keyword stuffing: repeated words
    words = re.findall(r"\w+", title.casefold())
    repeats = len(words) - len(set(words))
    if repeats > 1:
        penalty += 5.0 * min(3, repeats)
    if len(title) > 110:
        penalty += 10.0
    return penalty


def score_title(title: str, keywords: list[str] | None = None) -> dict[str, Any]:
    """Return a transparent 0-100 score with a breakdown."""
    title = str(title or "").strip()
    if not title:
        return {
            "score": 0.0,
            "breakdown": {"length": 0, "hook": 0, "keywords": 0, "penalty": 0},
        }
    keywords = [str(k) for k in (keywords or []) if str(k).strip()]
    length = _len_score(title)
    hook = _hook_score(title)
    keywords_score = _keyword_score(title, keywords)
    penalty = _penalty_score(title)
    total = round(max(0.0, min(100.0, length + hook + keywords_score - penalty)), 1)
    return {
        "score": total,
        "breakdown": {
            "length": round(length, 1),
            "hook": round(hook, 1),
            "keywords": round(keywords_score, 1),
            "penalty": round(penalty, 1),
        },
    }


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------

# Topic-only templates must not invent numbers or promises. The old set
# shipped "{topic} في {number} خطوات" (a random 3-10, presented as fact) and
# clickbait hooks ("لا أحد يخبرك بهذا", "النتيجة صدمتني") which the WebUI
# offered as one-click publish titles for clips those claims do not support.
_TEMPLATES = [
    "{topic} — {hook}",
    "طريقة {topic} خطوة بخطوة",
    "{topic}: {benefit}",
    "لماذا {topic} مهم؟",
    "{topic} بأبسط طريقة",
    "نصائح عملية عن {topic}",
    "ما الذي يجب أن تعرفه عن {topic}؟",
    "تجربة عملية مع {topic}",
]

_HOOKS = ["شرح واضح ومباشر", "تجربة عملية", "نصائح مفيدة", "خطوات بسيطة", "دليل مختصر"]
# Channel title presets (feat/pro-cutting item 8). Measured winners on the
# Mostefa Bounif channel opened mid-argument with a hook; the funnel preset
# appends the full-video marker so every Short drives viewers to the source.
TITLE_PRESETS = {
    "funnel_full": "{hook} | الفيديو الكامل",
    "query_question": "{query}: {question}",
}
_BENEFITS = [
    "وفّر وقتك وجهدك",
    "النتائج من أول مرة",
    "بلا أخطاء شائعة",
    "بأبسط طريقة ممكنة",
]


def _topic_token(topic: str) -> str:
    return re.sub(r"[\?؟!\s]+$", "", str(topic or "").strip()) or "هذا الموضوع"# Item 8: past-winning titles reused as templates. The winner's measured
# suffix (everything after the first separator) is kept VERBATIM — only the
# hook/topic slot becomes the new query. No invented claims.
# Item 8 (feat/pro-cutting): reuse of past WINNING titles as templates. The
# hook/topic slot is rebuilt from the NEW query; everything after the first
# separator stays VERBATIM from the measured winner — the suffix is real
# content that already earned views, never a fabricated claim.
WINNER_SEPARATORS = (":", " | ", "|", " — ", "—", "–")
_WINNER_MIN_TAIL = 12
_WINNER_MAX_TAIL = 70


def winner_to_template(title: str) -> str | None:
    """Turn a past winning title into ``{hook}<sep> <measured tail>``.

    Returns None when the title has no reusable separator/tail (short shouts,
    single-clause titles) — never fabricates a template. """
    text = re.sub(r"\s+", " ", str(title or "")).strip()
    if not text:
        return None
    for separator in WINNER_SEPARATORS:
        index = text.find(separator)
        if index <= 0:
            continue
        tail = text[index + len(separator):].strip(" \t\u060c؛:-")
        if _WINNER_MIN_TAIL <= len(tail) <= _WINNER_MAX_TAIL:
            return "{hook}" + separator + " " + tail
    return None


def titles_from_winners(
    winning_titles: list[str],
    hook: str,
    keywords: list[str] | None = None,
    *,
    limit: int = 3,
    transcript_text: str | None = None,
) -> list[dict[str, Any]]:
    """Ranked candidates built from past winners' measured shapes.

    Each candidate: new query as the hook + the winner's verbatim suffix,
    scored by ``score_title`` and (when a transcript is supplied) validated
    with ``title_factual`` — rejected claims never ship.
    """
    hook = str(hook or "").strip()
    if not hook:
        return []
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for winner in winning_titles or []:
        template = winner_to_template(winner)
        if not template:
            continue
        title = re.sub(r"\s+", " ", template.format(hook=hook)).strip()
        key = title.casefold()
        if not title or key in seen:
            continue
        seen.add(key)
        entry: dict[str, Any] = {
            "title": title,
            "pattern": "winner:" + template,
            **score_title(title, keywords),
        }
        if transcript_text:
            try:
                from scripts import title_factual

                validation = title_factual.validate_title_vs_clip(
                    title, transcript_text, "auto"
                )
                entry["validation"] = validation
                if validation.get("status") == "rejected":
                    continue
            except Exception:
                pass
        out.append(entry)
    out.sort(key=lambda item: item["score"], reverse=True)
    return out[: max(1, int(limit))]

def _topic_token(topic: str) -> str:
    return re.sub(r"[\?؟!\s]+$", "", str(topic or "").strip()) or "هذا الموضوع"


def generate_titles(
    topic: str,
    keywords: list[str] | None = None,
    count: int = 6,
    *,
    transcript_text: str | None = None,
    content_language: str = "auto",
) -> list[dict[str, Any]]:
    """Generate ranked title candidates from a topic + optional keywords.

    v7.41 — pass ``transcript_text`` (the EXACT clip-window transcript) to get
    GROUNDED titles only: every candidate is factually validated and any title
    that invents a number, name or claim not present in the clip is dropped.
    Without it the function stays a pure brainstorming template (the caller is
    then responsible for validating before showing/publishing).
    """
    count = max(0, min(20, int(count)))
    if count == 0:
        return []
    keywords = [str(k).strip() for k in (keywords or []) if str(k).strip()]
    if transcript_text is not None:
        from scripts import title_factual

        raw = str(transcript_text or "").strip()
        if not raw:
            return []
        parts = re.split(r"(?<=[.!?؟؛])\s+|[\r\n]+", raw)
        grounded_candidates = []
        seen_grounded = set()
        for part in parts:
            title = re.sub(r"\s+", " ", part).strip(" \t\r\n-–—•،؛:|")
            title = title.rstrip(".!?؟؛،: ")
            if len(title) < 12:
                continue
            if len(title) > title_factual.PUBLISH_TITLE_LIMIT:
                shortened = []
                for word in title.split():
                    candidate = " ".join(shortened + [word])
                    if len(candidate) > title_factual.PUBLISH_TITLE_LIMIT:
                        break
                    shortened.append(word)
                title = " ".join(shortened).rstrip("،؛: -")
            if len(title) < 12:
                continue
            key = (
                title_factual.arabic_text.to_search(title)
                if hasattr(title_factual, "arabic_text")
                else title.casefold()
            )
            if not key or key in seen_grounded:
                continue
            seen_grounded.add(key)
            validation = title_factual.validate_title_vs_clip(
                title, raw, content_language
            )
            if validation.get("status") == "rejected":
                continue
            grounded_candidates.append(
                {
                    "title": title,
                    **score_title(title, keywords),
                    "validation": validation,
                    "grounded": True,
                    "title_source": "exact_clip_transcript",
                }
            )
        grounded_candidates.sort(
            key=lambda item: (
                item["validation"].get("status") == "verified",
                item["score"],
                item["validation"].get("factual_consistency", 0),
            ),
            reverse=True,
        )
        return grounded_candidates[:count]

    topic = _topic_token(topic)
    candidates = []
    for i, template in enumerate(_TEMPLATES):
        title = template.format(
            topic=topic,
            hook=_HOOKS[i % len(_HOOKS)],
            benefit=_BENEFITS[i % len(_BENEFITS)],
        )
        title = re.sub(r"\s+", " ", title).strip()
        if title.casefold().startswith("كيف كيف"):
            continue
        candidates.append({"title": title, **score_title(title, keywords)})
    # de-duplicate by title
    seen = set()
    unique = []
    for item in candidates:
        key = item["title"].casefold()
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)

    unique.sort(key=lambda item: item["score"], reverse=True)
    return unique[:count]


def best_time_windows(platform: str = "youtube") -> dict[str, Any]:
    """Return the built-in engagement windows for a platform."""
    resolved = platform if platform in BEST_TIMES_BY_PLATFORM else "youtube"
    data = BEST_TIMES_BY_PLATFORM[resolved]
    return {"platform": resolved, **data}


def suggest_next_slots(
    platform: str = "youtube", count: int = 5, from_time=None
) -> list[str]:
    """Return the next ``count`` ISO slots inside the platform's windows.

    ``from_time`` (datetime, timezone-aware) defaults to now. Slots are
    hourly starts inside the best windows, skipping ones in the past.
    """
    import datetime as dt

    data = BEST_TIMES_BY_PLATFORM.get(platform, BEST_TIMES_BY_PLATFORM["youtube"])
    now = from_time or dt.datetime.now(dt.timezone.utc)
    slots = []
    # scan the next 14 days
    for day_offset in range(14):
        day = now + dt.timedelta(days=day_offset)
        is_weekend = day.weekday() >= 5
        windows = data.get("weekend" if is_weekend else "weekday", [])
        for start_hour, _end_hour in windows:
            slot = day.replace(hour=start_hour, minute=0, second=0, microsecond=0)
            if slot <= now:
                continue
            slots.append(slot.isoformat())
            if len(slots) >= count:
                return slots
    return slots


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command")

    sug = sub.add_parser("suggest", help="fetch YouTube autocomplete suggestions")
    sug.add_argument("topic")
    sug.add_argument("--lang", default="ar")
    sug.add_argument("--region", default="")

    gen = sub.add_parser("generate", help="generate ranked SEO titles")
    gen.add_argument("topic")
    gen.add_argument("--keyword", action="append", default=[])
    gen.add_argument("--count", type=int, default=6)

    score = sub.add_parser("score", help="score a title")
    score.add_argument("title")
    score.add_argument("--keyword", action="append", default=[])

    slots = sub.add_parser("slots", help="suggest next publish-time slots")
    slots.add_argument(
        "--platform", default="youtube", choices=["youtube", "tiktok", "reels"]
    )
    slots.add_argument("--count", type=int, default=5)

    args = parser.parse_args(argv)
    if args.command == "suggest":
        result = fetch_suggestions(args.topic, args.lang, args.region)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.command == "generate":
        result = generate_titles(args.topic, args.keyword, args.count)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.command == "score":
        result = score_title(args.title, args.keyword)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.command == "slots":
        print(
            json.dumps(
                suggest_next_slots(args.platform, args.count),
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
