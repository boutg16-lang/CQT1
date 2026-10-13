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

import re
from typing import Any, Dict, List, Optional

from scripts import arabic_text, clip_scoring

# Tokens that add zero information when they OPEN a clip. Conservative on
# purpose: words like «والله» carry emphasis and are content, not filler.
FILLER_OPENERS = frozenset({
    "يعني", "طيب", "آه", "اه", "أه", "إيه", "ايه", "امم", "أمم", "همم",
    "ههم", "اوكي", "أوكي", "حسنا", "حسناً", "هم",
})

# Standalone tokens that signal the window starts mid-conversation: the
# opener presupposes context the viewer does not have.
CONTINUATION_MARKERS = frozenset({
    "و", "ف", "ثم", "بس", "لكن", "لكنه", "لأن", "لان", "عشان", "علشان",
    "كمان", "برضو", "كذلك", "أيضا", "ايضا", "بعدين", "وبعدين", "برضه",
})

# Question words that make strong openers (curiosity gap in the viewer's
# own dialect — MSA and dialectal forms both count).
QUESTION_OPENERS = frozenset({
    "ليش", "ليه", "لييه", "لماذا", "كيف", "كيفاش", "وش", "ويش", "ايش",
    "إيش", "شو", "شنو", "هل", "قديش", "كم", "مين", "من", "وين", "أين",
    "اين", "متى", "امتى", "علاش",
})

# Second/first-person address pulls the viewer into the conversation.
PERSONAL_HOOK_TOKENS = frozenset({
    "أنا", "انا", "انت", "إنت", "أنت", "انتي", "إنتي", "أنتي", "انتم",
    "إياك", "يا", "نفسك", "حالك", "عندك", "عندي", "قولي", "اسمع",
})

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
    filler: List[Dict[str, Any]] = []
    for word in words[: max(1, int(max_scan))]:
        if _norm_word(word.get("word")) in FILLER_OPENERS:
            filler.append(word)
        else:
            break
    if not filler or len(filler) > int(max_filler) or len(words) <= len(filler):
        return None
    next_word = words[len(filler)]
    return {
        "trim_words": len(filler),
        "filler_words": [str(w.get("word") or "") for w in filler],
        "filler_end": float(filler[-1].get("end") or 0.0),
        "new_start": float(next_word.get("start") or 0.0),
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


def analyze_opening(text: str, *, max_opening_words: int = 14) -> Dict[str, Any]:
    """Score the opening of a clip from its transcript text.

    Returns a JSON-safe report: booleans for each hook signal, a 0-100
    ``hook_score``, human-readable Arabic notes for the review UI, and
    ``trim_words`` — how many leading tokens a trim should drop to reach
    the real opening (0 when the clip already opens clean).
    """
    raw = str(text or "").strip()
    if not raw:
        return {
            "hook_score": 0.0, "opens_with_filler": False, "filler_word": None,
            "opens_mid_thought": False, "continuation_marker": None,
            "has_question_hook": False, "has_number_hook": False,
            "has_personal_hook": False, "emotional_charge": 0.0,
            "trim_words": 0, "notes": ["لا يوجد نص افتتاحي لتحليله"],
        }

    tokens = _tokens(raw)
    opening = tokens[:max(1, int(max_opening_words))]
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
    has_question_hook = (
        first_word in QUESTION_OPENERS
        or ("؟" in first_sentence and len(first_sentence.split()) <= 12)
    )
    has_number_hook = bool(_NUMBER_RE.search(first_sentence))
    opening_joined = " ".join(words[:len(opening)])
    has_personal_hook = any(token in PERSONAL_HOOK_TOKENS for token in words[:len(opening)])
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
                filler_word, trim_words, trim_words))
    if opens_mid_thought:
        score -= 12.0
        notes.append(
            "تبدأ منتصف فكرة («{}») — المشاهد الجديد يفتقد السياق".format(first_word))
    if not notes:
        notes.append("افتتاحية نظيفة بلا إشارات قوة واضحة")
    if trim_words and not opens_mid_thought:
        # a filler prefix that continues mid-sentence is a double fault; make
        # sure it is reported even when the first token is not a marker
        pass

    return {
        "hook_score": round(max(0.0, min(100.0, score)), 1),
        "opens_with_filler": opens_with_filler,
        "filler_word": filler_word,
        "opens_mid_thought": opens_mid_thought,
        "continuation_marker": first_word if opens_mid_thought else None,
        "has_question_hook": has_question_hook,
        "has_number_hook": has_number_hook,
        "has_personal_hook": has_personal_hook,
        "emotional_charge": emotional_charge,
        "trim_words": trim_words,
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
    report = analyze_opening(text)
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
    annotated.sort(key=lambda item: item["opening_analysis"]["hook_score"], reverse=True)
    return annotated


__all__ = [
    "CONTINUATION_MARKERS", "FILLER_OPENERS", "PERSONAL_HOOK_TOKENS",
    "QUESTION_OPENERS", "analyze_opening", "analyze_segment_opening",
    "drop_leading_filler_tokens", "leading_filler_trim", "rank_openings",
]
