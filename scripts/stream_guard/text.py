"""
تطبيع النص العربي + تجريد السوابق واللواحق.

الهدف: مطابقة الكلمات المفتاحية مهما اختلف التشكيل أو الإملاء أو اللواحق.
Arabic text normalization + lightweight stemming for keyword matching.
"""
import re

# ----------------------------------------------------------------------------
# التطبيع
# ----------------------------------------------------------------------------

_DIACRITICS = re.compile(r"[\u0610-\u061A\u064B-\u065F\u0670\u06D6-\u06ED\u0640]")
_NON_WORD = re.compile(r"[^\w\s]", re.UNICODE)
_MULTI_CHAR = re.compile(r"(.)\1{2,}")          # ككككككك -> كك
_TATWEEL_REPEAT = re.compile(r"\s+")

# حروف يُشيع الخطأ فيها
_CHAR_MAP = {
    "أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا",
    "ى": "ي", "ئ": "ي", "ي": "ي",
    "ؤ": "و",
    "ة": "ه",
    "گ": "ك", "ک": "ك", "ہ": "ه", "ە": "ه",
    "ﻻ": "لا",
    "ۆ": "و", "ڤ": "ف", "پ": "ب", "چ": "تش", "ژ": "ز",
}


def normalize(text):
    """يرجع النص مطبّعًا (نفس عدد الكلمات قدر الإمكان، بلا تشكيل)."""
    if not text:
        return ""
    t = text.replace("\u0640", "")            # تطويل
    t = _DIACRITICS.sub("", t)                # تشكيل
    for a, b in _CHAR_MAP.items():
        t = t.replace(a, b)
    t = _NON_WORD.sub(" ", t)
    t = _MULTI_CHAR.sub(r"\1\1", t)           # تطويل الحروف للتمديد
    t = _TATWEEL_REPEAT.sub(" ", t)
    return t.strip().lower()


# ----------------------------------------------------------------------------
# التجريد (سوابق/لواحق شائعة)
# ----------------------------------------------------------------------------

PREFIXES = [
    "وال", "فال", "بال", "كال", "لال", "ال", "ول", "فل", "بل", "كل", "لل",
    "و", "ف", "ب", "ل", "ك", "س",
]

SUFFIXES = [
    "ها", "هم", "هن", "كم", "كن", "نا", "ات", "ين", "ون", "ية", "يه",
    "وا", "ني", "ه", "ي", "ا", "ت", "ن",
]

MIN_STEM = 3        # السوابق
SUFFIX_MIN = 4      # الجذوع المشتقّة من اللواحق

# سوابق ضيّقة: تُستعمل لأسماء المجموعات المحميّة (لتجنّب "سنصار" -> "نصار")
TIGHT_PREFIXES = ["وال", "فال", "بال", "كال", "لل", "ال", "ول", "فل", "بل",
                  "كل", "و", "ف", "ب", "ل", "ك"]


def stems(token, prefixes=None):
    """يرجع مجموعة الجذوع المحتملة لكلمة مطبّعة."""
    prefixes = prefixes or PREFIXES
    out = {token}
    queue = [token]
    for p in prefixes:
        if token.startswith(p) and len(token) - len(p) >= MIN_STEM:
            queue.append(token[len(p):])
    for b in list(queue):
        out.add(b)
        for s in SUFFIXES:
            if b.endswith(s) and len(b) - len(s) >= SUFFIX_MIN:
                out.add(b[: -len(s)])
    # مرة ثانية على النواتج (سابقة + لاحقة معًا)
    for b in list(out):
        for s in SUFFIXES:
            if b.endswith(s) and len(b) - len(s) >= SUFFIX_MIN:
                out.add(b[: -len(s)])
    # الكلمة الأصلية تُقبل دائمًا؛ الجذوع المشتقّة لا تقل عن MIN_STEM
    return {s for s in out if s == token or len(s) >= MIN_STEM}


def tokens(text):
    """يرجع [(الكلمة المطبّعة, موضع البداية, موضع النهاية)]."""
    out = []
    for m in re.finditer(r"\S+", text):
        out.append((m.group(0), m.start(), m.end()))
    return out


def stems_tight(token):
    """جذوع بسوابق ضيّقة — تُستعمل لأسماء المجموعات المحميّة."""
    return stems(token, TIGHT_PREFIXES)
