"""Context-aware offline safety heuristics for hate and incitement.

This module complements the literal blocklist.  It does not claim to replace
human review or a platform policy classifier: it looks for high-signal
combinations such as a target-group reference plus an exclusion/dehumanization
verb, or a violent imperative plus a collective target.  Counter-speech and
news/educational framing are treated as review signals rather than automatic
allowance, so creators still get a conservative publish decision.
"""

import re
from typing import Any, Dict, List, Optional

from scripts.safety_filter import normalize_text


def _normalized_alternation(phrases):
    """Build a regex alternation from literal phrases, matched post-normalization.

    ``analyze_text`` matches against ``normalize_text(text)`` — which folds
    alef/ya/taa-marbuta, strips the definite article and removes punctuation.
    Patterns written in raw orthography therefore never matched (e.g. the
    flagship phrase "هؤلاء الناس لا يستحقون الحياة" normalized to
    "هولاء ناس لا يستحقون حياه" and silently returned ``allow``).  Every
    alternative is normalized with the very same helper before being escaped,
    so the patterns and the text live in the same orthographic space.
    """
    parts = []
    seen = set()
    for phrase in phrases:
        norm = normalize_text(phrase)
        if not norm or norm in seen:
            continue
        seen.add(norm)
        parts.append(re.escape(norm))
    # Longest first: the most specific phrasing wins over its substrings.
    parts.sort(key=len, reverse=True)
    return r"(?:" + "|".join(parts) + r")"


_GROUP_PHRASES = (
    "مهاجر", "مهاجرين", "مهاجرون", "لاجئ", "لاجئين", "لاجئون", "اجنبي", "اجانب",
    "يهود", "مسلم", "مسلمين", "مسلمون", "مسيحي", "مسيحيين", "نصارى", "شيعي",
    "شيعة", "سني", "سنة", "ملحد", "ملحدين", "نساء", "نساءنا", "رجال", "مثليين",
    "مثليات", "ذوي اعاقه", "معاقين", "سود", "بيض", "افارقه", "افريقيين", "سعودي",
    "سعوديين", "خليجي", "خليجيين", "اماراتي", "اماراتيين", "كويتي", "كويتيين",
    "قطري", "قطريين", "سوري", "سوريين", "ايراني", "ايرانيين", "صومالي", "صوماليين",
    "صيني", "صينيين", "هندي", "هنود", "عرب", "عربي", "امازيغ", "امازيغي", "بدو",
    "غجر", "عرق", "جماعه", "طائفه", "قوم", "شعب", "هؤلاء الناس", "هذولا الناس",
    "these people", "those people", "immigrants", "refugees", "foreigners", "jews",
    "muslims", "christians", "women", "men", "gay people", "disabled people",
    "black people", "white people", "that group",
)

_VIOLENT_PHRASES = (
    "قتل", "اقتل", "اقتلو", "اقتلوهم", "اقتلهم", "نقتلهم", "نقتلك", "اذبح",
    "اذبحو", "اذبحوهم", "احرق", "احرقوهم", "نحرقهم", "ابيد", "أبيد", "ابيدوهم",
    "إبادة", "ابادة", "تصفية", "تهجير", "اطرد", "اطردوهم", "طهر", "تطهير عرقي",
    "اسحق", "دمر", "خليهم يموتوا", "خليهم يموتو", "ما يستاهلوش يعيشوا",
    "kill", "murder", "slaughter", "burn", "exterminate", "wipe out", "destroy",
    "deport",
)

_EXCLUSION_PHRASES = (
    "لا مكان ل",
    "لا يستحق الحياة", "لا يستحق العيش", "لا يستحق حياة", "لا يستحق عيش",
    "لا يستحقون الحياة", "لا يستحقون العيش", "لا يستحقون حياة", "لا يستحقون عيش",
    "ما يستاهل الحياة", "ما يستاهلوش الحياة", "ما يستاهل حياة", "ما يستاهلوش حياة",
    "يجب طرد", "اطردوا", "اخرجوا", "لا نريدهم", "ما نحبهمش", "تخلصوا من",
    "احرموا", "منعوا",
    "inferior", "subhuman", "should not exist", "do not belong", "send them away",
    "remove them",
)

_DEHUMANIZING_PHRASES = (
    "حشرات", "خنازير", "حيوانات", "طفيليات", "جرذان", "قمامة", "حثالة",
    "vermin", "pigs", "animals", "parasites", "cockroaches", "trash", "scum",
    "subhuman",
)

_COUNTER_SPEECH_PHRASES = (
    "ضد كراهيه", "لا اؤيد", "لا نؤيد", "ارفض", "ندين", "يدين", "ادانه",
    "خطاب كراهيه", "توعيه", "خبر", "اخبار", "وثائقي", "تاريخ", "تعليم",
    "نحذر من", "لا يجوز",
    "against hate", "do not support", "condemn", "condemning", "news",
    "documentary", "educational", "history", "warning", "not acceptable",
)

_GROUPS = _normalized_alternation(_GROUP_PHRASES)
_VIOLENT_VERBS = _normalized_alternation(_VIOLENT_PHRASES)
_EXCLUSION_VERBS = _normalized_alternation(_EXCLUSION_PHRASES)
_DEHUMANIZING = _normalized_alternation(_DEHUMANIZING_PHRASES)
_COUNTER_SPEECH = _normalized_alternation(_COUNTER_SPEECH_PHRASES)


def _matched(pattern: str, text: str) -> bool:
    return re.search(pattern, text, flags=re.IGNORECASE) is not None


def _recommendation(action: str, category: Optional[str]) -> str:
    if action == "block":
        return "احذف المقطع أو أعد صياغته دون استهداف جماعة أو دعوة إلى العنف، ثم راجعه يدوياً قبل النشر."
    if action == "review":
        return "لا تنشر تلقائياً؛ راجع السياق والصوت كاملاً، واستبدل العبارة بصياغة تعليمية محايدة إن أمكن."
    return "لم تُرصد إشارة دلالية عالية الخطورة، لكن تبقى المراجعة البشرية مطلوبة قبل النشر."


def analyze_text(text: str) -> Dict[str, Any]:
    """Return a conservative, JSON-safe semantic verdict for one text."""
    normalized = normalize_text(text)
    if not normalized:
        return {
            "action": "allow",
            "confidence": 0.0,
            "category": None,
            "signals": [],
            "explanation": "empty_text",
            "recommendation": _recommendation("allow", None),
        }

    signals: List[str] = []
    has_group = _matched(_GROUPS, normalized)
    has_violence = _matched(_VIOLENT_VERBS, normalized)
    has_exclusion = _matched(_EXCLUSION_VERBS, normalized)
    has_dehumanizing = _matched(_DEHUMANIZING, normalized)
    counter = _matched(_COUNTER_SPEECH, normalized)

    if has_group:
        signals.append("protected_or_collective_target")
    if has_violence:
        signals.append("violent_or_coercive_verb")
    if has_exclusion:
        signals.append("exclusion_or_dehumanization_frame")
    if has_dehumanizing:
        signals.append("dehumanizing_comparison")
    if counter:
        signals.append("counter_speech_or_educational_context")

    if has_group and has_violence and not counter:
        return {
            "action": "block",
            "confidence": 0.96,
            "category": "hate_or_violence_incitement",
            "signals": signals,
            "explanation": "collective target combined with a violent or coercive call",
            "recommendation": _recommendation("block", "hate_or_violence_incitement"),
        }
    if has_group and (has_exclusion or has_dehumanizing) and not counter:
        return {
            "action": "block",
            "confidence": 0.93,
            "category": "hate_or_dehumanization",
            "signals": signals,
            "explanation": "collective target combined with exclusion or dehumanization",
            "recommendation": _recommendation("block", "hate_or_dehumanization"),
        }
    if (has_violence and has_exclusion) or (has_dehumanizing and has_exclusion) or (has_group and counter and (has_violence or has_dehumanizing or "قتل" in normalized)):
        return {
            "action": "review",
            "confidence": 0.72,
            "category": "context_required",
            "signals": signals,
            "explanation": "high-risk terms require human/contextual review",
            "recommendation": _recommendation("review", "context_required"),
        }
    if has_exclusion or has_violence:
        return {
            "action": "review",
            "confidence": 0.62,
            "category": "harassment_or_threat_context",
            "signals": signals,
            "explanation": "potentially harmful language without a clear target/context",
            "recommendation": _recommendation("review", "harassment_or_threat_context"),
        }
    if has_dehumanizing:
        return {
            "action": "allow",
            "confidence": 0.18,
            "category": None,
            "signals": signals,
            "explanation": "animal or degrading term without a protected-group target",
            "recommendation": _recommendation("allow", None),
        }
    return {
        "action": "allow",
        "confidence": 0.12,
        "category": None,
        "signals": signals,
        "explanation": "no high-signal semantic combination detected",
        "recommendation": _recommendation("allow", None),
    }


def analyze_segments(segments: List[dict]) -> List[dict]:
    """Analyze segment title/caption/text and return indexed verdicts."""
    results = []
    for index, segment in enumerate(segments or []):
        text = " ".join(
            str(segment.get(key, "") or "")
            for key in ("text", "title", "caption", "reasoning")
        ).strip()
        verdict = analyze_text(text)
        verdict["index"] = index
        verdict["title"] = segment.get("title", "")
        verdict["start_time"] = segment.get("start_time")
        verdict["end_time"] = segment.get("end_time")
        verdict["text_preview"] = text[:240]
        results.append(verdict)
    return results
