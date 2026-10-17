# -*- coding: utf-8 -*-
"""Regression tests for Arabic transcript windows and factual title numbers."""

import json
import subprocess
import sys
from pathlib import Path

from scripts import create_viral_segments as cvs
from scripts import title_factual, transcript_window

TRANSCRIPT = [{
    "start": 0.0,
    "end": 2.5,
    "text": "التسويق يحتاج صبر بصح الاستمرارية تصنع النتيجة.",
}]
WORDS = [
    {"start": 0.00, "end": 0.22, "word": "التسويق"},
    {"start": 0.27, "end": 0.50, "word": "يحتاج"},
    {"start": 0.55, "end": 0.82, "word": "صبر"},
    {"start": 1.07, "end": 1.30, "word": "بصح"},
    {"start": 1.35, "end": 1.66, "word": "الاستمرارية"},
    {"start": 1.71, "end": 1.94, "word": "تصنع"},
    {"start": 1.99, "end": 2.45, "word": "النتيجة."},
]


def test_sentence_units_split_single_asr_block_at_short_darija_connector_pause():
    units = transcript_window.split_sentence_units(TRANSCRIPT, words=WORDS)

    assert [unit["text"] for unit in units] == [
        "التسويق يحتاج صبر",
        "بصح الاستمرارية تصنع النتيجة.",
    ]
    assert units[0]["end"] == 0.82
    assert units[1]["start"] == 1.07


def test_sentence_units_split_unpunctuated_single_block_without_word_timings():
    transcript = [{
        "start": 0.0,
        "end": 10.0,
        "text": "التسويق يحتاج صبر بصح الاستمرارية تصنع النتيجة",
    }]

    units = transcript_window.split_sentence_units(transcript)

    assert [unit["text"] for unit in units] == [
        "التسويق يحتاج صبر",
        "بصح الاستمرارية تصنع النتيجة",
    ]
    assert units[0]["start"] == 0.0
    assert units[1]["end"] == 10.0


def test_sentence_units_split_word_per_segment_when_pause_is_below_600ms():
    transcript = [
        {"start": word["start"], "end": word["end"], "text": word["word"]}
        for word in WORDS
    ]

    units = transcript_window.split_sentence_units(transcript)

    assert len(units) == 2
    assert units[0]["text"] == "التسويق يحتاج صبر"
    assert units[1]["text"] == "بصح الاستمرارية تصنع النتيجة."


def test_analyze_window_uses_word_timings_for_exact_clip_text():
    transcript = [{
        "start": 0.0,
        "end": 2.0,
        "text": "قبل المقطع ٩٩ وبعده فقط.",
    }]
    words = [
        {"start": 0.00, "end": 0.20, "word": "قبل"},
        {"start": 0.24, "end": 0.48, "word": "المقطع"},
        {"start": 0.52, "end": 0.82, "word": "٩٩"},
        {"start": 1.10, "end": 1.42, "word": "وبعده"},
        {"start": 1.46, "end": 1.80, "word": "فقط."},
    ]

    analysis = transcript_window.analyze_window(
        transcript, 1.10, 1.80, words=words)

    assert analysis["text"] == "وبعده فقط."
    assert "99" in title_factual.hallucinated_facts(
        "المقطع 99", analysis["text"])


def test_malformed_whisperx_word_timings_are_logged_and_fall_back(tmp_path, caplog):
    (tmp_path / "input.json").write_text("{broken", encoding="utf-8")

    assert transcript_window.load_word_timings(tmp_path) == []
    assert any("Could not read WhisperX word timings" in record.message
               for record in caplog.records)


def test_refine_boundaries_recovers_connector_using_word_timing_in_one_block():
    start, end, notes = transcript_window.refine_boundaries(
        1.07,
        2.45,
        TRANSCRIPT,
        words=WORDS,
        max_duration=5.0,
        transcript_start=0.0,
        transcript_end=2.5,
    )

    assert start == 0.0
    assert end == 2.45
    assert "connector_start_included_antecedent" in notes


def test_arabic_cardinal_words_are_checked_against_clip_numbers():
    unsupported = title_factual.hallucinated_facts(
        "خمس خطوات لإتقان مهارة واحدة",
        "الخطوة الأولى هي اختيار مهارة واحدة وإتقانها.",
    )

    assert "5" in unsupported
    assert title_factual.hallucinated_facts(
        "ثلاث خطوات للنجاح", "ثلاث خطوات واضحة للنجاح") == []


def test_arabic_indic_digits_match_ascii_and_written_number_forms():
    assert title_factual.hallucinated_facts(
        "١٢ يوماً للإنجاز", "12 يوماً للإنجاز") == []
    assert title_factual.hallucinated_facts(
        "خمسة وعشرين يوماً للإنجاز", "25 يوماً للإنجاز") == []


def test_equal_number_with_a_different_unit_is_rejected():
    validation = title_factual.validate_title_vs_clip(
        "ربحت ٥ كيلوغرامات",
        "ربحت 5 لترات",
        "ar",
    )

    assert validation["status"] == "rejected"
    assert validation["contains_unsupported_claim"] is True


def test_equivalent_units_are_accepted_across_spelling_variants():
    assert title_factual.unsupported_claims(
        "٥ كيلوغرامات", "5 كغ") == []


def test_create_viral_segments_import_does_not_replace_windows_stdout():
    source = """
import io
import sys
sys.platform = "win32"
stream = io.TextIOWrapper(io.BytesIO(), encoding="ascii")
sys.stdout = stream
import scripts.create_viral_segments
if sys.stdout is not stream:
    sys.__stderr__.write("module import replaced stdout\\n")
    raise SystemExit(1)
"""
    result = subprocess.run(
        [sys.executable, "-c", source],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_windows_stdout_configuration_is_called_by_main_only(monkeypatch):
    import io

    from main_improved import _configure_windows_stdout

    stream = io.TextIOWrapper(io.BytesIO(), encoding="ascii")
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "stdout", stream)

    _configure_windows_stdout()

    assert sys.stdout is stream
    assert stream.encoding.lower() == "utf-8"
    assert stream.errors == "replace"


def test_snap_uses_whisperx_word_units_from_input_json(tmp_path):
    transcript = [{"start": 0.0, "end": 2.2, "text": "صار الصرف كثيراً جداً بصح تغير الوضع."}]
    words = [
        {"start": 0.00, "end": 0.25, "word": "صار"},
        {"start": 0.30, "end": 0.55, "word": "الصرف"},
        {"start": 0.60, "end": 0.90, "word": "كثيراً"},
        {"start": 0.96, "end": 1.13, "word": "جداً"},
        {"start": 1.35, "end": 1.55, "word": "بصح"},
        {"start": 1.60, "end": 1.87, "word": "تغير"},
        {"start": 1.90, "end": 2.20, "word": "الوضع."},
    ]
    (tmp_path / "input.json").write_text(
        json.dumps({"segments": [{"words": words}]}, ensure_ascii=False),
        encoding="utf-8",
    )
    loaded_words = cvs._load_word_timings(str(tmp_path))
    assert cvs.snap_segment_boundaries(1.5, 2.0, transcript, words=loaded_words) == (1.35, 2.2)
