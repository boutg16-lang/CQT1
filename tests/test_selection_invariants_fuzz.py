# -*- coding: utf-8 -*-
"""Deterministic fuzz tests for the selection and validation invariants.

The earlier suites use hand-picked Arabic transcripts; they cannot prove that
an unusual combination of gaps, punctuation, numbers and model timestamps
still yields a usable segment list. These tests generate 150 cases from a
FIXED seed, so a failure is reproducible and CI stays deterministic.
"""
import math
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import create_viral_segments as cvs
from scripts import segment_validator, title_factual

WORDS = [
    "النجاح", "الصبر", "المهارة", "الخطة", "النتيجة", "التجربة", "العمل",
    "الوقت", "الفكرة", "الطريق", "البداية", "النهاية", "السر", "الخطأ",
    "هذا", "لكن", "ثم", "بصح", "واش", "علاش", "كيفاش", "بزاف",
]
NUMBERS = ["3", "٥", "20", "3500", "٣٬٥٠٠", "مليون", "عشرون ألف"]


def _sentence(rng):
    length = rng.randint(3, 11)
    words = [rng.choice(WORDS) for _ in range(length)]
    if rng.random() < 0.25:
        words.insert(rng.randrange(len(words) + 1), rng.choice(NUMBERS))
    text = " ".join(words)
    if rng.random() < 0.4:
        text += rng.choice([".", "؟", "!", ""])
    return text


def _transcript(rng, count):
    lines = []
    time_value = 0.0
    for _index in range(count):
        duration = rng.uniform(1.5, 9.0)
        gap = rng.choice([0.0, 0.05, 0.1, 0.3, 0.6, 1.2, 2.0])
        lines.append({
            "start": round(time_value, 2),
            "end": round(time_value + duration, 2),
            "text": _sentence(rng),
        })
        time_value += duration + gap
    return lines


def _raw(rng, transcript):
    count = rng.randint(1, 6)
    end_limit = transcript[-1]["end"]
    raw = []
    for index in range(count):
        start = rng.uniform(0.0, max(0.0, end_limit - 2.0))
        duration = rng.uniform(1.0, 40.0)
        entry = {
            "title": "مرشح {}".format(index),
            "start_time": round(start, 2),
            "end_time": round(min(end_limit, start + duration), 2),
            "score": rng.randint(40, 100),
            "hook_strength": rng.randint(10, 100),
            "narrative_completeness": rng.randint(10, 100),
            "clarity_score": rng.randint(10, 100),
            "novelty_score": rng.randint(10, 100),
        }
        if rng.random() < 0.3:
            entry["recommended_title"] = " ".join(
                rng.choice(WORDS) for _ in range(rng.randint(2, 5)))
        raw.append(entry)
    return raw


def _overlap_ratio(left, right):
    try:
        left_start, left_end = float(left["start_time"]), float(left["end_time"])
        right_start, right_end = float(right["start_time"]), float(right["end_time"])
    except (KeyError, TypeError, ValueError):
        return 0.0
    overlap = max(0.0, min(left_end, right_end) - max(left_start, right_start))
    shorter = min(left_end - left_start, right_end - right_start)
    return overlap / shorter if shorter > 0 else 0.0


def test_random_transcripts_produce_valid_non_overlapping_segments():
    rng = random.Random(811_2026)
    for case in range(150):
        transcript = _transcript(rng, rng.randint(3, 25))
        media_duration = round(transcript[-1]["end"] + rng.choice([0.0, 0.5, 2.0]), 2)
        raw = _raw(rng, transcript)
        min_duration = rng.choice([3, 5, 8])
        max_duration = min_duration + rng.choice([10, 20, 40])
        result = cvs.process_segments(
            raw, transcript, min_duration, max_duration,
            output_count=len(raw), media_duration=media_duration)
        segments = result.get("segments", [])
        for segment in segments:
            start = float(segment["start_time"])
            end = float(segment["end_time"])
            assert start >= 0.0
            assert end > start
            assert end <= media_duration + 0.01
            assert end - start <= max_duration + 0.01
            assert str(segment.get("recommended_title") or "").strip()
            report = segment.get("final_validation") or {}
            fatal = [item for item in report.get("errors", [])
                     if item.get("code") in cvs.FATAL_VALIDATION_CODES]
            assert not fatal, "case {}: fatal returned {}".format(case, fatal)
        for left_index in range(len(segments)):
            for right_index in range(left_index + 1, len(segments)):
                assert _overlap_ratio(segments[left_index], segments[right_index]) <= 0.61, \
                    "case {} returned overlapping clips".format(case)


def test_random_selection_is_deterministic():
    rng = random.Random(424_242)
    for _case in range(30):
        transcript = _transcript(rng, rng.randint(4, 18))
        raw = _raw(rng, transcript)
        first = cvs.process_segments(raw, transcript, 5, 30)
        second = cvs.process_segments(raw, transcript, 5, 30)
        snapshot = lambda result: [  # noqa: E731
            (segment.get("title"), round(float(segment["start_time"]), 3),
             round(float(segment["end_time"]), 3),
             segment.get("recommended_title"))
            for segment in result.get("segments", [])
        ]
        assert snapshot(first) == snapshot(second)


def test_validator_never_raises_on_random_malformed_segments():
    rng = random.Random(991_17)
    values = [None, "", "abc", 0, -1, 1.5, float("nan"), float("inf"),
              [], {}, "01:30", "1:2:3", True, False]
    for _case in range(300):
        segment = {}
        for key in ("start_time", "end_time", "duration", "transcript_text",
                    "title", "quality_flags", "window_analysis"):
            if rng.random() < 0.8:
                segment[key] = rng.choice(values)
        report = segment_validator.validate_final_segment(segment)
        assert isinstance(report, dict)
        assert isinstance(report.get("ok"), bool)
        assert isinstance(report.get("errors"), list)


def test_title_validation_never_raises_on_random_text():
    rng = random.Random(77_001)
    alphabet = "ابتثجحخدذرزسشصضطظعغفقكلمنهوي ٠١٢٣٤٥٦٧٨٩.,!?٪-"
    for _case in range(250):
        title = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 90)))
        window = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 200)))
        outcome = title_factual.validate_title_vs_clip(title, window, "auto")
        assert outcome["status"] in {"verified", "review", "rejected"}
        assert isinstance(outcome["reasons"], list)
        assert math.isfinite(float(outcome["factual_consistency"]))
