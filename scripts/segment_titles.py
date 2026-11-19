"""Title relevance for viral segments.

Extracted verbatim from ``scripts/create_viral_segments.py`` (step 3 of the
incremental monolith split). Pure, stdlib-only helpers that score and pick
titles against the real transcript words inside a cut window:

* ``_window_text_from_transcript`` — the words actually spoken in a window.
* ``_title_content_relevance`` — fraction of title words present in the clip.
* ``_title_quality_score`` — small deterministic readability heuristic.
* ``_choose_recommended_title`` — picks the strongest safe-looking candidate.

The helpers depend on ``_normalized_match_text`` (Arabic-orthography-blind
match text), which lives here too with its fallback table, exactly as it did
in the original module. ``scripts/create_viral_segments`` re-exports every
name in this module, so existing import paths keep working unchanged.
"""

import re

# Arabic normalization lives canonically in scripts/arabic_text; imported
# defensively so a missing optional dependency never blocks the callers.
try:
    from scripts import arabic_text as _arabic_text
except Exception:
    _arabic_text = None

# Arabic orthography unification table (ord -> replacement, None = delete).
# Canonical implementation: scripts/arabic_text.py. The historical private
# names (`_normalize_arabic_orthography`, `_normalized_match_text`) live
# here now (moved verbatim from scripts/create_viral_segments.py) and
# are re-exported there so existing callers/tests keep working unchanged.
# Arabic text is routinely written with several spelling variants of the same
# letters: the hamza carriers (أ إ آ ٱ) are the same consonant as plain ا in
# almost every context, ؤ/ئ are و/ي with a hamza seat, ة and ه both mark the
# feminine ending, and tatweel (U+0640) is a scribal stretch mark with no
# phonetic value. Tashkeel/diacritics (U+064B..U+065F) and the superscript
# alef (U+0670) are optional decorations that never change the word.
# Deliberately NOT mapped: alif maqsura ى stays distinct from ي so pairs like
# على (on) / علي (a name) never collide, and ي itself is untouched.
if _arabic_text is not None:
    _ARABIC_ORTHOGRAPHY_TABLE = _arabic_text._ARABIC_ORTHOGRAPHY_TABLE
else:
    _ARABIC_ORTHOGRAPHY_TABLE = {
        0x0623: 0x0627,  # أ hamza above    -> ا
        0x0625: 0x0627,  # إ hamza below    -> ا
        0x0622: 0x0627,  # آ madda          -> ا
        0x0671: 0x0627,  # ٱ wasla          -> ا
        0x0624: 0x0648,  # ؤ hamza on waw   -> و
        0x0626: 0x064A,  # ئ hamza on yaa   -> ي
        0x0629: 0x0647,  # ة ta marbuta     -> ه
        0x0640: None,  # tatweel (stretch mark) — deleted
        0x0670: None,  # superscript alef — deleted
    }
    _ARABIC_ORTHOGRAPHY_TABLE.update(dict.fromkeys(range(1611, 1632)))


def _normalize_arabic_orthography(text):
    """Unify common Arabic spelling variants so matching is orthography-blind.

    Delegates to scripts.arabic_text.normalize_arabic_orthography (canonical).
    Translates hamza carriers (أ إ آ ٱ -> ا, ؤ -> و, ئ -> ي) and the ta
    marbuta (ة -> ه), and removes tatweel (U+0640), the tashkeel block
    (U+064B..U+065F) and the superscript alef (U+0670). Alif maqsura (ى) is
    NOT folded into ي and ي is not folded anywhere, so words like على/علي
    remain distinguishable.

    Pure function with no state: non-Arabic text (English included) passes
    through byte-identical, so this never alters Latin/ASCII output.
    """
    if _arabic_text is not None:
        return _arabic_text.normalize_arabic_orthography(text)
    if text is None:
        return ""
    return str(text).translate(_ARABIC_ORTHOGRAPHY_TABLE)


def _normalized_match_text(value):
    """Lowercase alphanumeric-only text for transcript alignment.

    Arabic orthography is unified first (hamza carriers, ta marbuta, tatweel,
    tashkeel) so spelling variants such as ``الأمور``/``الامور`` compare
    equal, while distinct words such as ``على``/``علي`` stay distinct (alif
    maqsura is not folded). English/Latin output is byte-identical to the
    legacy pipeline: the normalization table only touches Arabic code points.
    """
    return re.sub(
        r"[^\w\s]", "", _normalize_arabic_orthography(str(value or "").lower())
    ).strip()


def _window_text_from_transcript(transcript_segments, start_time, end_time, words=None):
    """The real words spoken inside ``[start_time, end_time)``.

    Joins (space-separated) the text of every transcript line whose span
    overlaps the half-open window (``line.start < end_time`` AND
    ``line.end > start_time``), normalized for matching. This is the ground
    truth for title relevance: an LLM caption that hallucinates content never
    present in the actual cut footage can no longer inflate the score, and a
    title about mid-clip content finally counts because the mid-clip words
    really are in the window.

    Pure helper: returns '' when the transcript is empty, when the window
    cannot be parsed as numbers, or when no line overlaps.
    """
    if not transcript_segments:
        return ""
    try:
        window_start = float(start_time)
        window_end = float(end_time)
    except (TypeError, ValueError):
        return ""
    overlapping = []
    if words:
        overlapping_words = []
        for word in words:
            if not isinstance(word, dict):
                continue
            try:
                word_start = float(word.get("start"))
                word_end = float(word.get("end"))
            except (TypeError, ValueError):
                continue
            if word_start < window_end and word_end > window_start:
                text = str(word.get("word") or word.get("text") or "").strip()
                if text:
                    overlapping_words.append(text)
        if overlapping_words:
            return _normalized_match_text(" ".join(overlapping_words))
    for line in transcript_segments:
        if not isinstance(line, dict):
            continue
        try:
            line_start = float(line.get("start", 0.0) or 0.0)
            line_end = float(line.get("end", line_start) or line_start)
        except (TypeError, ValueError):
            continue
        if line_start < window_end and line_end > window_start:
            overlapping.append(str(line.get("text", "")))
    return _normalized_match_text(" ".join(overlapping))


def _title_content_relevance(title, segment, window_text=None):
    """Fraction of title words that actually appear in the clip's content.

    Catches titles that are pure bait unrelated to what the segment says.

    When ``window_text`` is provided and non-empty (the real transcript words
    inside the final cut window, e.g. from ``_window_text_from_transcript``)
    it is the primary evidence: words found there count at full weight, and
    words that appear ONLY in the LLM's own start_text/end_text/caption count
    at half weight — a hallucinated caption can no longer make an unrelated
    title score 1.0. The result is capped at 1.0.

    When ``window_text`` is None or empty the legacy formula (LLM text only,
    full weight) is used unchanged, so results stay byte-identical to
    previous releases.
    """
    title_tokens = set(_normalized_match_text(title).split())
    if not title_tokens:
        return 0.0
    if window_text:
        window_words = set(_normalized_match_text(str(window_text)).split())
        llm_words = set(
            _normalized_match_text(
                " ".join(
                    [
                        segment.get("start_text", ""),
                        segment.get("end_text", ""),
                        segment.get("caption", ""),
                    ]
                )
            ).split()
        )
        if not window_words and not llm_words:
            return 0.0
        llm_only_words = llm_words - window_words
        matched_window = len(title_tokens & window_words)
        matched_llm_only = len(title_tokens & llm_only_words)
        return min(1.0, (matched_window + 0.5 * matched_llm_only) / len(title_tokens))
    content = _normalized_match_text(
        " ".join(
            [
                segment.get("start_text", ""),
                segment.get("end_text", ""),
                segment.get("caption", ""),
            ]
        )
    )
    if not content:
        return 0.0
    content_tokens = set(content.split())
    return len(title_tokens & content_tokens) / len(title_tokens)


def _title_quality_score(title):
    """Small deterministic quality heuristic for title review, not a safety verdict."""
    text = str(title or "").strip()
    if not text:
        return 0.0
    score = 55.0
    if 18 <= len(text) <= 72:
        score += 20.0
    elif len(text) > 110:
        score -= 15.0
    if text.endswith(("!", "؟", "?")):
        score += 5.0
    if text.count("!") > 2 or text.count("؟") > 2 or text.count("?") > 2:
        score -= 12.0
    if text.isupper() and any(char.isalpha() for char in text):
        score -= 20.0
    # Word-salad check: a title that repeats the same common word several
    # times reads like keyword stuffing ("حرب حرب الكوكايين تطيح تطيح").
    words = [w for w in re.split(r"\W+", text.casefold()) if w]
    if len(words) >= 4:
        counts = {}
        for word in words:
            if len(word) > 2:
                counts[word] = counts.get(word, 0) + 1
        max_repeat = max(counts.values()) if counts else 0
        if max_repeat >= 2:
            score -= 10.0 * (max_repeat - 1)
    # A question title with no repeated words reads like a crafted
    # curiosity gap rather than a keyword pile-up.
    if len(words) >= 6 and len(set(words)) == len(words) and text.endswith(("؟", "?")):
        score += 3.0
    return round(max(0.0, min(100.0, score)), 1)


def _choose_recommended_title(segment, window_text=None):
    """Select the strongest safe-looking title candidate for default publishing.

    Safety filtering still runs later; this helper only ranks readability and
    does not approve a title for publication. ``window_text`` (optional) is
    the real transcript content of the final cut window: when given, content
    relevance is measured against the actual footage words instead of only
    the LLM's own start/end/caption text.
    """
    candidates = [segment.get("title", "")]
    alternatives = segment.get("alt_titles") or []
    if isinstance(alternatives, str):
        alternatives = [alternatives]
    candidates.extend(alternatives)
    clean = []
    seen = set()
    for candidate in candidates:
        value = str(candidate or "").strip()
        key = value.casefold()
        if value and key not in seen:
            seen.add(key)
            clean.append(value)
    if not clean:
        return "Viral Segment"

    def rank(item):
        index, value = item
        quality = _title_quality_score(value)
        relevance = _title_content_relevance(value, segment, window_text)
        return (quality + 12.0 * relevance, -index)

    return max(enumerate(clean), key=rank)[1]
