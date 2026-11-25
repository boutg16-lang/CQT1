"""Opening-hook analysis for OUSSAMA Cutter candidate clips.

The first seconds decide whether a Short survives the swipe. A candidate
window can score well overall and still OPEN with two seconds of
«يعني… طيب… أه» or with a mid-thought continuation («وبعدين معاه») that
means nothing to a fresh viewer. This module scores the OPENING of a
candidate from its transcript alone — deterministic, LLM-free,
Arabic-aware — and reports exactly what weakens the hook so the editor
review (or a human) can trim to the real moment.

Design notes:
- Pure heuristics, no model downloads, no network — same philosophy as
  ``scripts/audio_qc`` (measure, never mutate).
- Reuses the project's own Arabic normalisation and emotion lexicon, so
  the score it reports is consistent with the rest of the selection
  pipeline instead of being a second opinion written in a vacuum.
"""

from __future__ import annotations

import math
import re
from typing import Any, Dict, List, Optional

from scripts import arabic_text, clip_scoring

# Tokens that add zero information when they OPEN a clip. Conservative on
# purpose: words like «والله» carry emphasis and are content, not filler.
FILLER_OPENERS = frozenset(
    {
        "يعني",
        "طيب",
        "آه",
        "اه",
        "أه",
        "إيه",
        "ايه",
        "امم",
        "أمم",
        "همم",
        "ههم",
        "اوكي",
        "أوكي",
        "حسنا",
        "حسناً",
        "هم",
    }
)

# Standalone tokens that signal the window starts mid-conversation: the
# opener presupposes context the viewer does not have.
CONTINUATION_MARKERS = frozenset(
    {
        "و",
        "ف",
        "ثم",
        "بس",
        "لكن",
        "لكنه",
        "لأن",
        "لان",
        "عشان",
        "علشان",
        "كمان",
        "برضو",
        "كذلك",
        "أيضا",
        "ايضا",
        "بعدين",
        "وبعدين",
        "برضه",
    }
)

# Question words that make strong openers (curiosity gap in the viewer's
# own dialect — MSA and dialectal forms both count).
QUESTION_OPENERS = frozenset(
    {
        "ليش",
        "ليه",
        "لييه",
        "لماذا",
        "كيف",
        "كيفاش",
        "وش",
        "ويش",
        "ايش",
        "إيش",
        "شو",
        "شنو",
        "هل",
        "قديش",
        "كم",
        "مين",
        "من",
        "وين",
        "أين",
        "اين",
        "متى",
        "امتى",
        "علاش",
    }
)

# Second/first-person address pulls the viewer into the conversation.
PERSONAL_HOOK_TOKENS = frozenset(
    {
        "أنا",
        "انا",
        "انت",
        "إنت",
        "أنت",
        "انتي",
        "إنتي",
        "أنتي",
        "انتم",
        "إياك",
        "يا",
        "نفسك",
        "حالك",
        "عندك",
        "عندي",
        "قولي",
        "اسمع",
    }
)
# Greeting/intro openers (feat/pro-cutting item 4). A clip that OPENS with
# "السلام عليكم / أهلا بكم / مرحبا بكم" or an announcement ("في هذا الفيديو
# سوف...") burns the first 3 seconds on ceremony — measured losers did this
# while winners opened mid-argument. Tokens are orthography-normalized before
# membership, exactly like FILLER_OPENERS.
GREETING_OPENERS = frozenset(
    {
        "السلام",
        "عليكم",
        "اهلا",
        "أهلا",
        "سهلا",
        "مرحبا",
        "مرحبتين",
        "بكم",
        "بيكم",
        "زوار",
        "مشاهدين",
        "مشاهدي",
        "اصدقاء",
        "أصدقاء",
        "احبائي",
        "أحبائي",
        "اخواني",
        "إخواني",
    }
)

# Announcement openers: a promise ABOUT the video instead of the content
# itself. Matched as prefixes of the normalized opening text.
INTRO_ANNOUNCEMENT_PREFIXES = (
    "في هذا الفيديو",
    "في هذا المقطع",
    "في هذا الحلقة",
    "في هده الحلقة",
    "في حلقة اليوم",
    "في هذا الاشرط",
    "اليوم سنتعرف",
    "اليوم سنتحدث",
    "اليوم سنشرح",
    "سوف نتعرف",
    "سوف نتحدث",
    "سوف نشرح",
    "سنتعرف في",
    "سنشرح في",
    "سنرى في",
    "قبل ما نبدأ",
    "قبل ان نبدأ",
    "دعوني اشرح",
    "نبدأ في هذا",
    "هذا الفيديو عن",
    "هذا المقطع عن",
)

# The hook score below which an opening needs a trim/alternative start.
# Live recordings are held to a STRICTER threshold (item 4/7): a live cut
# competes with Studio-cut Shorts only through local editing, so a weak
# opening cannot be waved through.
HOOK_PASS_SCORE = 60.0
LIVE_HOOK_PASS_SCORE = 70.0
LIVE_PENALTY_MULTIPLIER = 1.25

_GREETING_RUN_MAX = 4

_NUMBER_RE = re.compile(r"[0-9٠-٩۱۲۳۴۵۶۷۸۹]+")
_WS_RE = re.compile(r"\s+")
_PUNCT_STRIP = "\t .,،;:!؟?…\"'()[]{}<>«»ـ"


def _tokens(text: str) -> List[str]:
    normalized = arabic_text.normalize_arabic_orthography(str(text or ""))
    return [token for token in _WS_RE.split(normalized.strip()) if token]


def _first_sentence(text: str) -> str:
    parts = re.split(r"[.!؟?\n]+", str(text or ""), maxsplit=1)
    return (parts[0] if parts else "").strip()


def _norm_word(word: str) -> str:
    value = arabic_text.normalize_arabic_orthography(str(word or ""))
    return value.strip(_PUNCT_STRIP).strip()


def detect_greeting_intro(text: str) -> Dict[str, Any]:
    """Detect a greeting or "about this video" announcement at the START.

    Returns a JSON-safe report with ``is_greeting`` (welcome words first),
    ``is_intro_announcement`` (promise about the video instead of content),
    ``intro_word_count`` — how many leading tokens belong to the ceremony —
    and the matched ``label`` for the review UI. Deterministic, LLM-free.
    """
    raw = str(text or "").strip()
    tokens = _tokens(raw)
    words = [_norm_word(token) for token in tokens]
    is_greeting = False
    greeting_run = 0
    for word in words[:_GREETING_RUN_MAX]:
        if word in GREETING_OPENERS:
            greeting_run += 1
        else:
            break
    if greeting_run:
        is_greeting = True

    is_intro = False
    intro_words = 0
    opening = " ".join(words[:14]).strip()
    for prefix in INTRO_ANNOUNCEMENT_PREFIXES:
        if opening.startswith(prefix):
            is_intro = True
            intro_words = len(prefix.split())
            break
    if is_greeting:
        # the whole first sentence is ceremony when it opens with a welcome
        first_break = re.search(r"[.!؟?\n]", raw)
        ceremony_text = raw[: first_break.start()] if first_break else raw
        greeting_run = len(_tokens(ceremony_text)) or greeting_run
    # The ceremony extends through the first sentence (the whole greeting
    # formula / announcement clause), not just the matched prefix —
    # «السلام عليكم ورحمة الله» or «في هذا الفيديو سنتحدث عن…».
    first_sentence = _first_sentence(raw)
    sentence_words = [_norm_word(t) for t in _tokens(first_sentence)]
    if is_greeting or is_intro:
        intro_word_count = max(greeting_run, intro_words, len(sentence_words))
    else:
        intro_word_count = max(greeting_run, intro_words)
    label = ""
    if is_greeting and is_intro:
        label = "greeting_and_announcement"
    elif is_greeting:
        label = "greeting"
    elif is_intro:
        label = "intro_announcement"
    return {
        "is_greeting": is_greeting,
        "is_intro_announcement": is_intro,
        "intro_word_count": int(intro_word_count),
        "label": label,
    }


def suggest_alternative_start(text: str, intro_word_count: int = 0) -> str:
    """The first strong sentence AFTER the greeting/intro ceremony.

    A sentence is "strong" when it carries a hook signal (question, number,
    direct address) or is a full content sentence (>= 6 words). '' when the
    text is ceremony-only — the caller then trims to the first content word.
    """
    raw = str(text or "").strip()
    if not raw:
        return ""
    words = [_norm_word(token) for token in _tokens(raw)]
    skip = max(0, int(intro_word_count or 0))
    sentences = []
    start_word = 0
    cursor = 0
    for part in re.split(r"([.!؟?\n]+)", raw):
        if not part:
            continue
        if re.fullmatch(r"[.!؟?\n]+", part):
            cursor += len(part.split()) if False else 0
            continue
        count = len([w for w in _tokens(part)])
        sentences.append((start_word, start_word + count, part.strip()))
        start_word += count
    # ^ word counting via _tokens keeps parity with the normalized sequence
    for begin, _end, sentence in sentences:
        if begin < skip:
            # the sentence STARTS inside the greeting/intro ceremony — skip it
            continue
        sentence_text = re.sub(r"\s+", " ", sentence).strip(" \t،؛:|-")
        if len(sentence_text) < 12:
            continue
        sentence_words = [_norm_word(t) for t in _tokens(sentence_text)]
        first = sentence_words[0] if sentence_words else ""
        strong = (
            first in QUESTION_OPENERS
            or bool(_NUMBER_RE.search(sentence_text))
            or any(w in PERSONAL_HOOK_TOKENS for w in sentence_words)
            or len(sentence_words) >= 6
        )
        if strong:
            return sentence_text
    return ""


def leading_filler_trim(
    word_timings: List[Dict[str, Any]],
    *,
    max_scan: int = 6,
    max_filler: int = 4,
) -> Optional[Dict[str, Any]]:
    """Detect a filler prefix in TIMED words and return a trim plan (or None).

    ``word_timings`` are the window's words (``[{start, end, word}]``) in
    chronological order. A trim is proposed only when 1..``max_filler``
    leading words are filler — longer runs are usually mid-sentence
    hesitation, not a clean removable prefix — and a real content word
    follows to open on. Punctuation and Arabic orthography variants are
    normalised before matching, so «يعني،» and «إيه» count too.
    """
    words = [w for w in (word_timings or []) if isinstance(w, dict)]
    if not words:
        return None

    # WhisperX can emit malformed or out-of-order word boundaries after a
    # fallback/alignment edge case. Never turn those into an edit decision:
    # validate the complete sequence before proposing a destructive trim.
    normalized: List[Dict[str, Any]] = []
    previous_start = -1.0
    for word in words:
        try:
            start = float(word.get("start"))
            end = float(word.get("end"))
        except (TypeError, ValueError):
            return None
        if not (math.isfinite(start) and math.isfinite(end)):
            return None
        if start < 0.0 or end <= start or start < previous_start:
            return None
        previous_start = start
        normalized.append({**word, "start": start, "end": end})

    filler: List[Dict[str, Any]] = []
    for word in normalized[: max(1, int(max_scan))]:
        if _norm_word(word.get("word")) in FILLER_OPENERS:
            filler.append(word)
        else:
            break
    if not filler or len(filler) > int(max_filler) or len(normalized) <= len(filler):
        return None

    next_word = normalized[len(filler)]
    filler_end = float(filler[-1]["end"])
    next_start = float(next_word["start"])
    # Do not cut into the first content word when alignments overlap. A
    # 50 ms tolerance absorbs harmless rounding while rejecting real overlap.
    if next_start < filler_end - 0.05:
        return None
    return {
        "trim_words": len(filler),
        "filler_words": [str(w.get("word") or "") for w in filler],
        "filler_end": filler_end,
        "new_start": next_start,
        "next_word": str(next_word.get("word") or ""),
    }


def drop_leading_filler_tokens(text: str, count: int) -> str:
    """Drop up to ``count`` leading tokens when they are ALL filler openers.

    Used after a word-timing trim: the coarse transcript text still covers
    the cut-off filler, so the stored text is aligned with the real cut.
    Tokens that are not filler stop the drop — the function never eats
    content words.
    """
    tokens = str(text or "").split()
    if not tokens:
        return str(text or "")
    drop = 0
    for token in tokens[: max(0, int(count))]:
        if _norm_word(token) in FILLER_OPENERS:
            drop += 1
        else:
            break
    return " ".join(tokens[drop:]) if drop else str(text)


def analyze_opening(
    text: str,
    *,
    max_opening_words: int = 14,
    source_is_live: bool = False,
) -> Dict[str, Any]:
    """Score the opening of a clip from its transcript text.

    Returns a JSON-safe report: booleans for each hook signal, a 0-100
    ``hook_score``, human-readable Arabic notes for the review UI, and
    ``trim_words`` — how many leading tokens a trim should drop to reach
    the real opening (0 when the clip already opens clean).
    """
    raw = str(text or "").strip()
    if not raw:
        return {
            "hook_score": 0.0,
            "opens_with_filler": False,
            "filler_word": None,
            "opens_mid_thought": False,
            "continuation_marker": None,
            "has_question_hook": False,
            "has_number_hook": False,
            "has_personal_hook": False,
            "emotional_charge": 0.0,
            "trim_words": 0,
            "opens_with_greeting": False,
            "is_intro_announcement": False,
            "greeting_label": "",
            "intro_word_count": 0,
            "suggested_start": "",
            "hook_threshold": LIVE_HOOK_PASS_SCORE if source_is_live else HOOK_PASS_SCORE,
            "passes_hook_threshold": False,
            "source_is_live": bool(source_is_live),
            "notes": ["لا يوجد نص افتتاحي لتحليله"],
        }

    tokens = _tokens(raw)
    opening = tokens[: max(1, int(max_opening_words))]
    # Normalize each token with the SAME helper the trimmer uses, so trailing
    # punctuation («يعني،») cannot defeat the membership checks below while
    # the raw tokens stay available for display fields.
    words = [_norm_word(token) for token in tokens]
    first_word = words[0] if words else ""
    first_sentence = _first_sentence(raw)

    notes: List[str] = []

    # --- filler prefix: count ALL leading filler tokens, not just the first
    trim_words = 0
    for word in words:
        if word in FILLER_OPENERS:
            trim_words += 1
        else:
            break
    filler_word = tokens[0] if trim_words else None
    opens_with_filler = bool(trim_words)

    opens_mid_thought = first_word in CONTINUATION_MARKERS
    has_question_hook = first_word in QUESTION_OPENERS or (
        "؟" in first_sentence and len(first_sentence.split()) <= 12
    )
    has_number_hook = bool(_NUMBER_RE.search(first_sentence))
    opening_joined = " ".join(words[: len(opening)])
    has_personal_hook = any(
        token in PERSONAL_HOOK_TOKENS for token in words[: len(opening)]
    )
    emotional_charge = clip_scoring.emotional_value_heuristic(opening_joined)

    score = 50.0
    if has_question_hook:
        score += 18.0
        notes.append("افتتاحية سؤال — خطاف فضول مباشر")
    if has_number_hook:
        score += 10.0
        notes.append("رقم في الجملة الأولى — وعد محدد للمشاهد")
    if has_personal_hook:
        score += 8.0
        notes.append("مخاطبة مباشرة — تدخل المشاهد في الحوار فوراً")
    score += min(20.0, emotional_charge * 0.2)
    if opens_with_filler:
        score -= min(24.0, 12.0 + 6.0 * trim_words)
        notes.append(
            "تبدأ بحشو («{}» ×{}) — اقصِ أول {} كلمة للوصول إلى اللحظة الحقيقية".format(
                filler_word, trim_words, trim_words
            )
        )
    if opens_mid_thought:
        score -= 12.0
        notes.append(
            "تبدأ منتصف فكرة («{}») — المشاهد الجديد يفتقد السياق".format(first_word)
        )

    # --- greeting / intro announcement penalty (feat/pro-cutting item 4)
    ceremony = detect_greeting_intro(raw)
    is_greeting = bool(ceremony.get("is_greeting"))
    is_intro = bool(ceremony.get("is_intro_announcement"))
    intro_word_count = int(ceremony.get("intro_word_count") or 0)
    suggested_start = ""
    if is_greeting or is_intro:
        base_penalty = 20.0 if (is_greeting and is_intro) else (15.0 if is_greeting else 12.0)
        penalty = base_penalty * (LIVE_PENALTY_MULTIPLIER if source_is_live else 1.0)
        score -= penalty
        skip_words = max(trim_words, intro_word_count)
        suggested_start = suggest_alternative_start(raw, skip_words)
        if is_greeting:
            notes.append(
                "تبدأ بترحيب/تحية («{}») — ابدأ من الجملة القوية التالية{}: {}".format(
                    ceremony.get("label") or "greeting",
                    " (البث المباشر عتبة أكثر صرامة)" if source_is_live else "",
                    suggested_start or "—",
                )
            )
        if is_intro:
            notes.append(
                "تبدأ بإعلان عن الفيديو («في هذا…») — قصّها وادخل في الموضوع مباشرة"
            )

    hook_threshold = LIVE_HOOK_PASS_SCORE if source_is_live else HOOK_PASS_SCORE
    if not notes:
        notes.append("افتتاحية نظيفة بلا إشارات قوة واضحة")
    if trim_words and not opens_mid_thought:
        # a filler prefix that continues mid-sentence is a double fault; make
        # sure it is reported even when the first token is not a marker
        pass

    final_score = round(max(0.0, min(100.0, score)), 1)
    return {
        "hook_score": final_score,
        "opens_with_filler": opens_with_filler,
        "filler_word": filler_word,
        "opens_mid_thought": opens_mid_thought,
        "continuation_marker": first_word if opens_mid_thought else None,
        "has_question_hook": has_question_hook,
        "has_number_hook": has_number_hook,
        "has_personal_hook": has_personal_hook,
        "emotional_charge": emotional_charge,
        "trim_words": trim_words,
        "opens_with_greeting": is_greeting,
        "is_intro_announcement": is_intro,
        "greeting_label": str(ceremony.get("label") or ""),
        "intro_word_count": intro_word_count,
        "suggested_start": suggested_start,
        "hook_threshold": hook_threshold,
        "passes_hook_threshold": final_score >= hook_threshold,
        "source_is_live": bool(source_is_live),
        "notes": notes,
    }


def analyze_segment_opening(segment: Dict[str, Any]) -> Dict[str, Any]:
    """Analyse a candidate segment dict (uses transcript_text / hook_text)."""
    text = (
        segment.get("transcript_text")
        or segment.get("hook_text")
        or segment.get("caption")
        or segment.get("title")
        or ""
    )
    source_is_live = bool(
        segment.get("source_is_live")
        or segment.get("is_live")
        or segment.get("live_source")
    )
    report = analyze_opening(text, source_is_live=source_is_live)
    report["segment_title"] = segment.get("recommended_title") or segment.get("title")
    return report


def rank_openings(segments: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Attach ``opening_analysis`` to every segment and return the list
    sorted by hook score (strongest opening first). Input is not mutated."""
    annotated: List[Dict[str, Any]] = []
    for segment in segments:
        entry = dict(segment)
        entry["opening_analysis"] = analyze_segment_opening(segment)
        annotated.append(entry)
    annotated.sort(
        key=lambda item: item["opening_analysis"]["hook_score"], reverse=True
    )
    return annotated


__all__ = [
    "CONTINUATION_MARKERS",
    "HOOK_PASS_SCORE",
    "LIVE_HOOK_PASS_SCORE",
    "LIVE_PENALTY_MULTIPLIER",
    "FILLER_OPENERS",
    "GREETING_OPENERS",
    "HOOK_PASS_SCORE",
    "INTRO_ANNOUNCEMENT_PREFIXES",
    "LIVE_HOOK_PASS_SCORE",
    "LIVE_PENALTY_MULTIPLIER",
    "PERSONAL_HOOK_TOKENS",
    "QUESTION_OPENERS",
    "analyze_opening",
    "analyze_segment_opening",
    "detect_greeting_intro",
    "detect_greeting_intro",
    "drop_leading_filler_tokens",
    "suggest_alternative_start",
    "leading_filler_trim",
    "rank_openings",
    "suggest_alternative_start",
]
