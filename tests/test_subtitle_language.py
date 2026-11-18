# -*- coding: utf-8 -*-
"""Direct tests for scripts/subtitle_language.py.

Subtitle script detection (Arabic vs Latin) used to pick the WhisperX
alignment language. Stdlib only; no I/O, no network, no models.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import subtitle_language as sl  # noqa: E402

# ---------------------------------------------------------------------------
# detect_text_script
# ---------------------------------------------------------------------------

def test_detect_arabic_dominant():
    assert sl.detect_text_script("مرحبا بالعالم الجميل") == "ar"


def test_detect_arabic_lower_threshold_than_title_text():
    # 5 Arabic / 5 Latin -> ratio 0.5, above this module's 0.35 -> 'ar'
    # (the title_text detector would call the same text 'mixed').
    assert sl.detect_text_script("مرحبا hello") == "ar"


def test_detect_mixed_below_arabic_threshold():
    # 5 Arabic / 10 Latin -> ratio 0.333 < 0.35 -> 'mixed'.
    assert sl.detect_text_script("Hello world مرحبا") == "mixed"


def test_detect_english_dominant():
    assert sl.detect_text_script("Hello world this is English") == "en"


def test_detect_english_low_arabic_ratio():
    # 1 Arabic / 10 Latin -> ratio ~0.09 <= 0.10 -> 'en'.
    assert sl.detect_text_script("Hello world ا") == "en"


def test_detect_none_empty_non_string_digits():
    assert sl.detect_text_script("") == "none"
    assert sl.detect_text_script("   ") == "none"
    assert sl.detect_text_script(None) == "none"
    assert sl.detect_text_script(123) == "none"
    assert sl.detect_text_script("12345 !!!") == "none"


def test_detect_none_other_script():
    # Cyrillic is neither Arabic nor Latin for this detector.
    assert sl.detect_text_script("Привет мир") == "none"


def test_detect_arabic_supplement_block():
    assert sl.detect_text_script("\u0750\u0751") == "ar"


def test_detect_arabic_indic_digits_do_not_count_as_latin():
    # Arabic-Indic digits are Arabic-script chars, so a digit-heavy Arabic
    # line still classifies as Arabic, not 'none'.
    assert sl.detect_text_script("مرحبا ١٢٣") == "ar"


def test_detect_mixed_one_line_each_way():
    # A mostly-English line with one Arabic word stays mixed, not 'ar'.
    assert sl.detect_text_script("This is a long English sentence مرحبا") == "mixed"


# ---------------------------------------------------------------------------
# choose_alignment_language
# ---------------------------------------------------------------------------

def test_choose_arabic_subtitles():
    assert sl.choose_alignment_language("مرحبا بالعالم، كيف حالك؟") == "ar"


def test_choose_english_subtitles_default():
    assert sl.choose_alignment_language("Hello world, how are you?") == "en"


def test_choose_mixed_subtitles_uses_default():
    assert sl.choose_alignment_language("Hello world مرحبا") == "en"


def test_choose_empty_subtitles_uses_default():
    assert sl.choose_alignment_language("") == "en"
    assert sl.choose_alignment_language(None) == "en"


def test_choose_custom_forced_default():
    assert sl.choose_alignment_language("Hello world", forced_default="fr") == "fr"
    # Arabic still wins over a custom default.
    assert sl.choose_alignment_language("مرحبا بالعالم", forced_default="fr") == "ar"


def test_choose_darija_arabic_script():
    # Moroccan Darija written in Arabic script must align as Arabic.
    assert sl.choose_alignment_language("كيفاش داير؟ شنو الجديد؟") == "ar"
