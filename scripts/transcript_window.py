# -*- coding: utf-8 -*-
"""transcript_window — exact transcript-window analysis and boundary repair.

Answers the questions the selection pipeline must know about every candidate
clip, using ONLY the real transcript inside the selected time window:

* exact text inside ``[start_time, end_time]`` (+ first/last spoken sentence,
  context before/after the window)
* does the clip start/end mid-sentence, with an Arabic connector opener, or
  on a dangling preposition/conjunction?
* how much silence leads/trails the cut, and how much of the window is speech?

…and repairs boundaries production-safely:

* word-level edge snapping when WhisperX word timings are available
  (never start/end inside a word), with a safe segment-level fallback;
* connector-start repair (walk back to include the antecedent sentence);
* dangling-ending repair (extend to the end of the current sentence);
* configurable pre-roll (0.20-0.50 s) / post-roll (0.30-0.80 s) that never
  push the clip past the configured max duration.

Pure stdlib, no I/O except ``load_word_timings`` — fully unit-testable.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
from bisect import bisect_left

from scripts import arabic_text

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Linguistic tables
# ---------------------------------------------------------------------------

# Sentence-terminal punctuation (Arabic + Latin). "…" ends a thought; the
# Arabic comma ، does NOT (it marks continuation, exactly like ",").
_TERMINAL_RE = re.compile(r"[.!?؟…]+\s*[\"'»)】]*\s*$")

# Isolated Arabic conjunctions/connectors a clip should not OPEN with unless
# the antecedent is included. Exact-token match only: the و/ف prefixes are
# far too common to flag as substrings (most Arabic sentences begin with them).
START_CONNECTORS_AR = {
    "لكن", "ولكن", "لكنه", "لكنها", "لكنني", "لكننا",
    "لذلك", "لذا", "ولذلك", "لهذا", "ولهذا",
    "لأن", "لان", "ولأن", "ولان",
    "وهذا", "وهذه", "وذلك", "وهو", "وهي", "وهم",
    "فهو", "فهي", "فهذا", "فهذه",
    "ثم", "فإذا", "فاذا", "فإن", "فان",
    "إذن", "اذن", "أيضا", "ايضا", "كذلك", "أيضاً",
    "غير", "حيث", "بينما", "عندما", "لما", "لمّا",
    "بصح", "باسكو", "دونك",
}

# English filler/conjunction openers that make weak hooks.
START_CONNECTORS_EN = {
    "um", "uh", "so", "and", "but", "or", "then", "because", "also",
    "well", "like", "actually", "basically",
}

# Words a clip must not END on: prepositions, conjunctions, articles,
# auxiliary verbs and relative pronouns (the idea is clearly unfinished).
END_INCOMPLETE_AR = {
    # prepositions
    "في", "على", "إلى", "الى", "عن", "من", "مع", "حتى", "منذ", "بين",
    "تحت", "فوق", "أمام", "امام", "وراء", "قبل", "بعد", "عند", "لدى",
    "نحو", "مثل", "خلال", "ضد", "دون", "بدون", "عبر", "حول",
    # conjunctions / relatives
    "و", "ف", "ثم", "أو", "او", "لكن", "ولكن", "لأن", "لان", "إذا",
    "اذا", "إن", "ان", "أن", "كي", "لكي", "حين", "عندما", "الذي",
    "التي", "الذين", "لذلك", "ب", "ل", "ك", "س",
}

END_INCOMPLETE_EN = {
    "the", "a", "an", "and", "or", "but", "so", "because", "if", "when",
    "then", "that", "which", "who", "whose", "to", "of", "in", "on", "at",
    "for", "with", "about", "into", "from", "by", "as", "is", "are", "was",
    "were", "be", "been", "have", "has", "had", "will", "would", "can",
    "could", "should", "shall", "may", "might", "must", "do", "does", "did",
}

# Openers that signal a reference to something said BEFORE the window
# (unresolved context): demonstratives/pronouns as the very first word.
UNRESOLVED_OPENERS_AR = {"هذا", "هذه", "ذلك", "تلك", "هؤلاء", "أولئك", "هو", "هي", "هم"}
UNRESOLVED_OPENERS_EN = {"he", "she", "it", "they", "this", "that", "these", "those", "him", "her"}

# A pause longer than this (seconds) between transcript lines marks a
# sentence boundary when punctuation is missing (Whisper often drops Arabic
# punctuation entirely).
SENTENCE_PAUSE_SECONDS = 0.60

# Shorter pauses before a discourse connector can mark a sentence boundary.
CONNECTOR_PAUSE_SECONDS = 0.18

# Strong clause openers used when one unpunctuated ASR block has no word times.
_SENTENCE_CONNECTORS_AR = {
    "لكن", "ولكن", "لذلك", "لذا", "لهذا", "لأن", "لان", "ثم", "بصح",
    "باسكو", "دونك", "بل", "مع ذلك", "في المقابل", "على العكس",
    "من جهة أخرى", "ومن جهة أخرى",
}

# How far (seconds) connector-start repair may walk back to include the
# antecedent sentence.
CONNECTOR_LOOKBACK_SECONDS = 8.0

# ---------------------------------------------------------------------------
# Pre-roll / post-roll configuration
# ---------------------------------------------------------------------------

def pre_post_roll_config():
    """Read pre/post-roll from the environment with documented bounds.

    * ``VIRALCUTTER_PRE_ROLL``  — seconds added BEFORE the cut start.
      Default 0.0 (disabled, keeps legacy byte-exact windows); when set, the
      value is clamped to the spec range [0.20, 0.50].
    * ``VIRALCUTTER_POST_ROLL`` — seconds added AFTER the cut end.
      Default 0.0; when set, clamped to [0.30, 0.80].

    Enabled values never let the clip exceed its max duration (the caller
    shrinks post-roll first, then pre-roll).
    """
    def _read(name, lo, hi):
        raw = os.getenv(name, "").strip()
        if not raw:
            return 0.0
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return 0.0
        if value <= 0.0:
            return 0.0
        return max(lo, min(hi, value))

    return {
        "pre_roll": _read("VIRALCUTTER_PRE_ROLL", 0.20, 0.50),
        "post_roll": _read("VIRALCUTTER_POST_ROLL", 0.30, 0.80),
    }


# ---------------------------------------------------------------------------
# Word-level timings (WhisperX input.json)
# ---------------------------------------------------------------------------

def load_word_timings(project_folder):
    """Load a flat word list [{start,end,word}] from the project transcript JSON.

    Reads ``<project_folder>/input.json`` (WhisperX format with per-word
    timestamps). Returns [] on any failure — callers fall back to
    segment-level timings.
    """
    if not project_folder:
        return []
    path = os.path.join(str(project_folder), "input.json")
    if not os.path.isfile(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError, TypeError) as exc:
        logger.warning(
            "Could not read WhisperX word timings from input.json (%s); using segment timings.",
            type(exc).__name__,
        )
        return []
    if not isinstance(data, dict):
        logger.warning("WhisperX input.json has no object at its root; using segment timings.")
        return []
    words = []
    malformed = 0
    for segment in (data.get("segments") or []):
        if not isinstance(segment, dict):
            malformed += 1
            continue
        for word in (segment.get("words") or []):
            if not isinstance(word, dict):
                malformed += 1
                continue
            try:
                w_start = float(word.get("start"))
                w_end = float(word.get("end"))
            except (TypeError, ValueError):
                malformed += 1
                continue
            text = str(word.get("word") or word.get("text") or "").strip()
            if not text or not math.isfinite(w_start) or not math.isfinite(w_end) or w_end < w_start:
                malformed += 1
                continue
            words.append({"start": w_start, "end": w_end, "word": text})
    if malformed:
        logger.warning(
            "Ignored %d malformed word timing record(s) from WhisperX input.json.",
            malformed,
        )
    words.sort(key=lambda item: item["start"])
    return words


# ---------------------------------------------------------------------------
# Sentence units
# ---------------------------------------------------------------------------

def _timed_word_events(transcript_segments, words=None):
    candidates = words
    if candidates is None:
        candidates = []
        for segment in transcript_segments or []:
            if isinstance(segment, dict):
                candidates.extend(segment.get("words") or [])
    events = []
    for item in candidates or []:
        if not isinstance(item, dict):
            continue
        try:
            start = float(item.get("start"))
            end = float(item.get("end"))
        except (TypeError, ValueError):
            continue
        text = str(item.get("word") or item.get("text") or "").strip()
        if not text or not math.isfinite(start) or not math.isfinite(end) or end < start:
            continue
        events.append({"start": start, "end": end, "text": text})
    events.sort(key=lambda item: (item["start"], item["end"]))
    return events


def _line_has_word_timing(line, word_events, word_starts):
    index = bisect_left(word_starts, line["start"])
    if index and word_events[index - 1]["end"] > line["start"]:
        return True
    return index < len(word_events) and word_events[index]["start"] < line["end"]


def _estimated_sentence_events(line):
    pieces = _split_sentence_pieces(line["text"])
    counts = [max(1, len(piece.split())) for piece, _ in pieces]
    total = sum(counts) or 1
    duration = line["end"] - line["start"]
    elapsed = 0
    events = []
    for (piece, connector_boundary), count in zip(pieces, counts, strict=True):
        piece_start = line["start"] + duration * elapsed / total
        elapsed += count
        piece_end = line["start"] + duration * elapsed / total
        events.append({
            "start": piece_start,
            "end": piece_end,
            "text": piece,
            "connector_boundary": connector_boundary,
        })
    return events


def _normalized_tokens(text):
    return [arabic_text.normalized_match_text(token)
            for token in str(text or "").split()
            if arabic_text.normalized_match_text(token)]


def _starts_with_connector(text):
    tokens = _normalized_tokens(text)
    if not tokens:
        return False
    if tokens[0] in {arabic_text.normalized_match_text(word)
                     for word in START_CONNECTORS_AR}:
        return True
    for phrase in _SENTENCE_CONNECTORS_AR:
        phrase_tokens = _normalized_tokens(phrase)
        if phrase_tokens and tokens[:len(phrase_tokens)] == phrase_tokens:
            return True
    return bool(tokens[0].lower() in START_CONNECTORS_EN)


def _split_connector_phrases(text):
    text = str(text or "")
    raw_tokens = text.split()
    normalized = [_normalized_tokens(token) for token in raw_tokens]
    tokens = [parts[0] if parts else "" for parts in normalized]
    offsets = []
    cursor = 0
    for token in raw_tokens:
        start = text.find(token, cursor)
        offsets.append(start)
        cursor = start + len(token)

    boundaries = []
    for index in range(3, len(tokens) - 1):
        for phrase in sorted(_SENTENCE_CONNECTORS_AR, key=len, reverse=True):
            phrase_tokens = _normalized_tokens(phrase)
            if (phrase_tokens and tokens[index:index + len(phrase_tokens)] == phrase_tokens
                    and len(tokens) - index - len(phrase_tokens) >= 2):
                boundaries.append(offsets[index])
                break
    if not boundaries:
        return [(text.strip(), False)]

    pieces = []
    cursor = 0
    for boundary in boundaries:
        piece = text[cursor:boundary].strip()
        if piece:
            pieces.append((piece, cursor != 0))
        cursor = boundary
    tail = text[cursor:].strip()
    if tail:
        pieces.append((tail, cursor != 0))
    return pieces


def _split_sentence_pieces(text):
    text = str(text or "").strip()
    if not text:
        return []
    terminal = set(".!?؟…")
    closers = set("\\\"'»)】")
    sentences = []
    cursor = 0
    index = 0
    while index < len(text):
        if text[index] not in terminal:
            index += 1
            continue
        if (text[index] == "." and index > 0 and index + 1 < len(text)
                and text[index - 1].isdigit() and text[index + 1].isdigit()):
            index += 1
            continue
        end = index + 1
        while end < len(text) and text[end] in terminal | closers:
            end += 1
        if end == len(text) or text[end].isspace():
            sentence = text[cursor:end].strip()
            if sentence:
                sentences.append(sentence)
            cursor = end
            index = end
        else:
            index += 1
    tail = text[cursor:].strip()
    if tail:
        sentences.append(tail)
    result = []
    for sentence in sentences or [text]:
        result.extend(_split_connector_phrases(sentence))
    return result


def _group_sentence_events(events):
    if not events:
        return []
    units = []
    current = None
    for index, event in enumerate(events):
        if current is None:
            current = dict(event)
            continue
        gap = event["start"] - current["end"]
        context_words = len(current["text"].split())
        preview = " ".join(item["text"] for item in events[index:index + 3])
        connector_pause = (gap >= CONNECTOR_PAUSE_SECONDS
                           and context_words >= 3
                           and _starts_with_connector(preview))
        split_before = bool(event.get("connector_boundary"))
        if (_TERMINAL_RE.search(current["text"])
                or gap >= SENTENCE_PAUSE_SECONDS
                or connector_pause
                or split_before):
            units.append({key: current[key] for key in ("start", "end", "text")})
            current = dict(event)
        else:
            current["end"] = max(current["end"], event["end"])
            current["text"] = (current["text"] + " " + event["text"]).strip()
    if current is not None:
        units.append({key: current[key] for key in ("start", "end", "text")})
    return units


def _line_has_word_timing(line, word_events, word_starts):
    index = bisect_left(word_starts, line["start"])
    if index and word_events[index - 1]["end"] > line["start"]:
        index -= 1
    return (index < len(word_events)
            and word_events[index]["start"] < line["end"]
            and word_events[index]["end"] > line["start"])


def _estimated_sentence_events(line):
    pieces = _split_sentence_pieces(line["text"])
    counts = [max(1, len(piece.split())) for piece, _ in pieces]
    total = sum(counts) or 1
    elapsed = 0
    duration = line["end"] - line["start"]
    events = []
    for (piece, connector_boundary), count in zip(pieces, counts, strict=True):
        piece_start = line["start"] + duration * elapsed / total
        elapsed += count
        piece_end = line["start"] + duration * elapsed / total
        events.append({
            "start": piece_start,
            "end": piece_end,
            "text": piece,
            "connector_boundary": connector_boundary,
        })
    return events


def split_sentence_units(transcript_segments, words=None):
    """Group ASR text into sentence-like units using words, pauses and clauses.

    Word timings (including WhisperX ``input.json`` words) are authoritative
    when supplied. Without them, punctuation and recognizable Arabic/English
    discourse connectors split even a single long ASR block; timestamps for
    multiple sentences inside one block are estimated by word count.
    """
    lines = []
    for segment in transcript_segments or []:
        if not isinstance(segment, dict):
            continue
        try:
            start = float(segment.get("start", 0.0) or 0.0)
            end = float(segment.get("end", start) or start)
        except (TypeError, ValueError):
            continue
        text = str(segment.get("text", "") or "").strip()
        if text:
            lines.append({"start": start, "end": max(start, end), "text": text})
    lines.sort(key=lambda item: item["start"])
    if not lines:
        return []

    word_events = _timed_word_events(transcript_segments, words)
    word_starts = [event["start"] for event in word_events]
    events = []
    seen_word_indexes = set()
    for line in lines:
        if not word_events or not _line_has_word_timing(line, word_events, word_starts):
            events.extend(_estimated_sentence_events(line))
            continue
        index = bisect_left(word_starts, line["start"])
        if index and word_events[index - 1]["end"] > line["start"]:
            index -= 1
        while index < len(word_events) and word_events[index]["start"] < line["end"]:
            event = word_events[index]
            if event["end"] > line["start"] and index not in seen_word_indexes:
                events.append(event)
                seen_word_indexes.add(index)
            index += 1
    events.sort(key=lambda item: (item["start"], item["end"]))
    return _group_sentence_events(events)


def _first_word(text):
    words = str(text or "").split()
    return arabic_text.normalized_match_text(words[0]) if words else ""


def _last_word(text):
    words = str(text or "").split()
    return arabic_text.normalized_match_text(words[-1]) if words else ""


def _words_normalized(text):
    return [w for w in (arabic_text.normalized_match_text(tok)
                        for tok in str(text or "").split()) if w]


# ---------------------------------------------------------------------------
# Window analysis
# ---------------------------------------------------------------------------

def analyze_window(transcript_segments, start_time, end_time, context_span=12.0, *, words=None):
    """Analyze the exact transcript content inside ``[start_time, end_time]``.

    Returns a plain dict (JSON-serializable) with the raw window text, the
    first/last spoken sentence, the context before/after the window, silence
    measurements and completeness flags. Never raises: malformed input yields
    an "empty" analysis with ``word_count == 0``.
    """
    result = {
        "text": "",
        "first_sentence": "",
        "last_sentence": "",
        "before_text": "",
        "after_text": "",
        "word_count": 0,
        "unique_ratio": 0.0,
        "starts_with_connector": False,
        "opens_with_unresolved_reference": False,
        "ends_incomplete": False,
        "starts_mid_sentence": False,
        "ends_mid_sentence": False,
        "ends_with_terminal_punctuation": False,
        "leading_silence": 0.0,
        "trailing_silence": 0.0,
        "speech_coverage": 0.0,
        "silence_ratio": 1.0,
        "complete": False,
    }
    try:
        start_time = float(start_time)
        end_time = float(end_time)
    except (TypeError, ValueError):
        return result
    if end_time <= start_time:
        return result

    lines = []
    for seg in (transcript_segments or []):
        if not isinstance(seg, dict):
            continue
        try:
            l_start = float(seg.get("start", 0.0) or 0.0)
            l_end = float(seg.get("end", l_start) or l_start)
        except (TypeError, ValueError):
            continue
        text = str(seg.get("text", "") or "").strip()
        if not text:
            continue
        lines.append({"start": l_start, "end": max(l_start, l_end), "text": text})
    lines.sort(key=lambda item: item["start"])
    if not lines:
        return result

    inside = [line for line in lines if line["start"] < end_time and line["end"] > start_time]
    word_events = _timed_word_events(transcript_segments, words)
    inside_words = [word for word in word_events
                    if word["start"] < end_time and word["end"] > start_time]
    if inside_words:
        before_words = [word for word in word_events
                        if word["end"] <= start_time
                        and word["end"] > start_time - context_span]
        after_words = [word for word in word_events
                       if word["start"] >= end_time
                       and word["start"] < end_time + context_span]
        text = " ".join(word["text"] for word in inside_words).strip()
        result["before_text"] = " ".join(word["text"] for word in before_words).strip()
        result["after_text"] = " ".join(word["text"] for word in after_words).strip()
        speech_items = inside_words
    else:
        before = [line for line in lines
                  if line["end"] <= start_time and line["end"] > start_time - context_span]
        after = [line for line in lines
                 if line["start"] >= end_time and line["start"] < end_time + context_span]
        text = " ".join(line["text"] for line in inside).strip()
        result["before_text"] = " ".join(line["text"] for line in before).strip()
        result["after_text"] = " ".join(line["text"] for line in after).strip()
        speech_items = inside
    result["text"] = text

    normalized_words = _words_normalized(text)
    result["word_count"] = len(normalized_words)
    result["unique_ratio"] = (
        len(set(normalized_words)) / len(normalized_words) if normalized_words else 0.0)

    if speech_items:
        result["leading_silence"] = round(
            max(0.0, speech_items[0]["start"] - start_time), 3)
        result["trailing_silence"] = round(
            max(0.0, end_time - speech_items[-1]["end"]), 3)
        speech = 0.0
        cursor = start_time
        for item in speech_items:
            seg_start = max(item["start"], start_time, cursor)
            seg_end = min(item["end"], end_time)
            if seg_end > seg_start:
                speech += seg_end - seg_start
                cursor = max(cursor, seg_end)
        coverage = speech / max(0.001, end_time - start_time)
        result["speech_coverage"] = round(min(1.0, coverage), 3)
        result["silence_ratio"] = round(1.0 - result["speech_coverage"], 3)

    # Sentence-level view (for first/last sentence + mid-sentence detection).
    units = split_sentence_units(lines, words=word_events if inside_words else None)
    inside_units = [u for u in units if u["start"] < end_time and u["end"] > start_time]
    if inside_units:
        result["first_sentence"] = inside_units[0]["text"]
        result["last_sentence"] = inside_units[-1]["text"]
        # Mid-sentence start: the cut starts strictly AFTER the first unit's
        # own start (tolerance 150 ms for ASR jitter).
        result["starts_mid_sentence"] = start_time > inside_units[0]["start"] + 0.15
        # Mid-sentence end: the cut ends strictly BEFORE the last unit's end.
        result["ends_mid_sentence"] = end_time < inside_units[-1]["end"] - 0.15

    if text:
        first = _first_word(text)
        last = _last_word(text)
        result["starts_with_connector"] = _starts_with_connector(text)
        result["opens_with_unresolved_reference"] = (
            first in UNRESOLVED_OPENERS_AR or first.lower() in UNRESOLVED_OPENERS_EN)
        result["ends_incomplete"] = (
            last in END_INCOMPLETE_AR or last.lower() in END_INCOMPLETE_EN)
        result["ends_with_terminal_punctuation"] = bool(_TERMINAL_RE.search(text))

    result["complete"] = bool(
        text
        and not result["starts_mid_sentence"]
        and not result["ends_mid_sentence"]
        and not result["ends_incomplete"]
    )
    return result


# ---------------------------------------------------------------------------
# Boundary repair
# ---------------------------------------------------------------------------

def snap_edges_to_words(start_time, end_time, words):
    """Snap cut edges to word boundaries so a cut never lands inside a word.

    * Start inside a word → that word's start (include the whole word).
    * End inside a word → that word's end (finish the word).
    * Edges inside a silence gap are left untouched.
    Returns the input unchanged when word data is missing/unusable.
    """
    if not words:
        return start_time, end_time
    snapped_start, snapped_end = start_time, end_time
    for word in words:
        w_start, w_end = word["start"], word["end"]
        if w_start < start_time < w_end:
            snapped_start = w_start
            break
        if w_end >= start_time:
            break
    for word in words:
        w_start, w_end = word["start"], word["end"]
        if w_start < end_time < w_end:
            snapped_end = w_end
            break
        if w_start >= end_time:
            break
    if snapped_end <= snapped_start + 0.05:
        return start_time, end_time
    return snapped_start, snapped_end


def repair_connector_start(start_time, end_time, transcript_segments,
                           analysis, max_duration, transcript_start=0.0, words=None):
    """Walk the cut start back when the clip opens with a dangling connector.

    Only when: the first word is an Arabic/English connector AND the previous
    sentence unit ended within ``CONNECTOR_LOOKBACK_SECONDS`` AND including it
    keeps the duration ≤ ``max_duration``. Returns (new_start, note|None).
    """
    if not analysis.get("starts_with_connector"):
        return start_time, None
    units = split_sentence_units(transcript_segments, words=words)
    previous = None
    for unit in units:
        if unit["end"] <= start_time + 0.05:
            previous = unit
        else:
            break
    if previous is None:
        return start_time, None
    if start_time - previous["end"] > CONNECTOR_LOOKBACK_SECONDS:
        return start_time, None
    new_start = max(float(transcript_start or 0.0), previous["start"])
    if end_time - new_start > max_duration:
        return start_time, None
    if new_start < start_time - 0.01:
        return new_start, "connector_start_included_antecedent"
    return start_time, None


def repair_dangling_end(start_time, end_time, transcript_segments, analysis,
                        max_duration, transcript_end=None, words=None):
    """Extend the cut end when it finishes on a preposition/conjunction.

    Extends to the end of the current sentence unit, bounded by
    ``max_duration`` and the transcript end. Returns (new_end, note|None).
    """
    if not analysis.get("ends_incomplete"):
        return end_time, None
    units = split_sentence_units(transcript_segments, words=words)
    current = None
    for unit in units:
        if unit["start"] < end_time and unit["end"] >= end_time - 0.05:
            current = unit
        if unit["start"] >= end_time:
            break
    if current is None or current["end"] <= end_time + 0.01:
        return end_time, None
    new_end = current["end"]
    if transcript_end is not None:
        new_end = min(new_end, float(transcript_end))
    if new_end - start_time > max_duration:
        return end_time, None
    return new_end, "dangling_end_extended_to_sentence_end"


def refine_boundaries(start_time, end_time, transcript_segments, *,
                      words=None, min_duration=0.0, max_duration=float("inf"),
                      transcript_start=0.0, transcript_end=None,
                      pre_roll=None, post_roll=None):
    """Production boundary refinement for one candidate window.

    Order: word snap → connector-start repair → dangling-end repair →
    pre/post-roll → duration re-check. Returns
    ``(start, end, notes)`` with millisecond precision; on any internal
    failure the original window is returned with a note.
    """
    notes = []
    start_time = max(0.0, float(start_time))
    end_time = max(start_time + 0.1, float(end_time))
    original = (start_time, end_time)
    try:
        if words:
            w_start, w_end = snap_edges_to_words(start_time, end_time, words)
            if (w_start, w_end) != (start_time, end_time):
                notes.append("word_boundary_snap")
                start_time, end_time = w_start, w_end

        analysis = analyze_window(
            transcript_segments, start_time, end_time, words=words)

        new_start, note = repair_connector_start(
            start_time, end_time, transcript_segments, analysis,
            max_duration, transcript_start=transcript_start, words=words)
        if note:
            start_time = new_start
            notes.append(note)
            analysis = analyze_window(
                transcript_segments, start_time, end_time, words=words)

        new_end, note = repair_dangling_end(
            start_time, end_time, transcript_segments, analysis,
            max_duration, transcript_end=transcript_end, words=words)
        if note:
            end_time = new_end
            notes.append(note)

        rolls = pre_post_roll_config()
        pre_roll = rolls["pre_roll"] if pre_roll is None else float(pre_roll)
        post_roll = rolls["post_roll"] if post_roll is None else float(post_roll)
        if pre_roll > 0.0 or post_roll > 0.0:
            duration = end_time - start_time
            budget = max(0.0, float(max_duration) - duration)
            post_roll = min(post_roll, budget)
            budget -= post_roll
            pre_roll = min(pre_roll, budget)
            if transcript_end is not None:
                post_roll = min(post_roll, max(0.0, float(transcript_end) - end_time))
            pre_roll = min(pre_roll, start_time - max(0.0, float(transcript_start or 0.0)))
            if pre_roll > 0.0 or post_roll > 0.0:
                start_time -= pre_roll
                end_time += post_roll
                notes.append("pre_post_roll_applied")

        # Never violate the duration budget or produce an empty window.
        if end_time - start_time > float(max_duration) + 0.05:
            return original[0], original[1], notes + ["refinement_reverted_max_duration"]
        if end_time <= start_time + 0.1:
            return original[0], original[1], notes + ["refinement_reverted_empty"]
        return round(start_time, 3), round(end_time, 3), notes
    except Exception as exc:
        logger.warning("Boundary refinement failed; restoring original window: %s", exc,
                       exc_info=True)
        return original[0], original[1], notes + ["refinement_error_reverted"]


# ---------------------------------------------------------------------------
# Semantic duplication helpers
# ---------------------------------------------------------------------------

def semantic_similarity(text_a, text_b):
    """Similarity in [0, 1] between two transcript excerpts (Arabic-safe).

    Combines containment, SequenceMatcher ratio and token Jaccard on
    orthography-normalized text — catches "same idea, different wording" only
    when the wording still largely overlaps (true paraphrase detection needs
    embeddings, which this project deliberately avoids as a dependency).
    """
    import difflib

    a = arabic_text.normalized_match_text(text_a)
    b = arabic_text.normalized_match_text(text_b)
    if not a or not b:
        return 0.0
    if a in b or b in a:
        return 1.0
    ratio = difflib.SequenceMatcher(None, a, b).ratio()
    tokens_a, tokens_b = set(a.split()), set(b.split())
    union = tokens_a | tokens_b
    jaccard = (len(tokens_a & tokens_b) / len(union)) if union else 0.0
    return max(ratio, jaccard)


def _number_set(text):
    return set(re.findall(r"\d+(?:\.\d+)?", arabic_text.normalize_digits(str(text or ""))))


def are_semantic_duplicates(text_a, text_b, threshold=None):
    """Decide whether two transcript excerpts state the SAME idea.

    Guards against the classic false positives of surface similarity:
    * different numeric content (٣ خطوات vs ٥ خطوات, "part 3" vs "part 7")
      means different facts — never a duplicate;
    * character-level similarity without real token overlap (template-like
      sentences) is not a duplicate: the token Jaccard must reach at least
      half of the threshold too.
    Returns (is_duplicate, similarity).
    """
    import difflib

    a = arabic_text.normalized_match_text(text_a)
    b = arabic_text.normalized_match_text(text_b)
    if not a or not b:
        return False, 0.0
    if threshold is None:
        threshold = semantic_duplicate_threshold()
    if a in b or b in a:
        similarity = 1.0
    else:
        ratio = difflib.SequenceMatcher(None, a, b).ratio()
        tokens_a, tokens_b = set(a.split()), set(b.split())
        union = tokens_a | tokens_b
        jaccard = (len(tokens_a & tokens_b) / len(union)) if union else 0.0
        similarity = max(ratio, jaccard)
        # Template guard: high char ratio but low token overlap ≠ same idea.
        if similarity >= threshold and jaccard < threshold * 0.5:
            return False, similarity
    # Numeric guard: different stated numbers = different facts.
    numbers_a, numbers_b = _number_set(text_a), _number_set(text_b)
    if numbers_a and numbers_b and numbers_a != numbers_b:
        return False, similarity
    return similarity >= threshold, similarity


# Semantic comparison needs enough text to be meaningful: below this many
# normalized words, surface similarity is dominated by shared grammar tokens
# ("moment 0" vs "moment 1" look identical), so only temporal dedup applies.
MIN_SEMANTIC_WORDS = 6


def guarded_semantic_similarity(text_a, text_b):
    """semantic_similarity with the duplicate guards applied (0.0 when the
    numeric-content or token-overlap guards reject the comparison). Used by
    the repetition-penalty gradient."""
    import difflib

    a = arabic_text.normalized_match_text(text_a)
    b = arabic_text.normalized_match_text(text_b)
    if not a or not b:
        return 0.0
    # Numeric guard: different stated numbers = different facts.
    numbers_a, numbers_b = _number_set(text_a), _number_set(text_b)
    if numbers_a and numbers_b and numbers_a != numbers_b:
        return 0.0
    if a in b or b in a:
        return 1.0
    ratio = difflib.SequenceMatcher(None, a, b).ratio()
    tokens_a, tokens_b = set(a.split()), set(b.split())
    union = tokens_a | tokens_b
    jaccard = (len(tokens_a & tokens_b) / len(union)) if union else 0.0
    if jaccard < 0.25:
        # No real token overlap: character resemblance alone is not repetition.
        return jaccard
    return max(ratio, jaccard)


def semantic_duplicate_threshold(default=0.75):
    try:
        value = float(os.getenv("VIRALCUTTER_SEMANTIC_DUP_THRESHOLD", "").strip())
        return max(0.3, min(1.0, value))
    except (TypeError, ValueError):
        return float(default)


def repetition_penalty_slope(default=25.0):
    """Max penalty applied when similarity sits in [0.55, threshold]."""
    try:
        value = float(os.getenv("VIRALCUTTER_REPETITION_PENALTY_MAX", "").strip())
        return max(0.0, min(100.0, value))
    except (TypeError, ValueError):
        return float(default)
