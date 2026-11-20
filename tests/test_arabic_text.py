# -*- coding: utf-8 -*-
"""Direct tests for scripts/arabic_text.py — Arabic text normalization helpers.

Pure stdlib module: orthography unification, digit conversion, light
stemming, the three normalization levels (DISPLAY / COMPARISON / SEARCH)
and language/RTL detection. No I/O, no network, no models.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import arabic_text as at

# ---------------------------------------------------------------------------
# normalize_arabic_orthography
# ---------------------------------------------------------------------------


def test_orthography_hamza_carriers_fold_to_alef():
    assert at.normalize_arabic_orthography("أإآٱ") == "اااا"


def test_orthography_hamza_seats_and_ta_marbuta():
    assert at.normalize_arabic_orthography("مؤمن") == "مومن"
    assert at.normalize_arabic_orthography("قارئ") == "قاري"
    assert at.normalize_arabic_orthography("مدرسة") == "مدرسه"


def test_orthography_tatweel_and_tashkeel_removed():
    assert at.normalize_arabic_orthography("ســـلام") == "سلام"
    assert at.normalize_arabic_orthography("السَّلامُ") == "السلام"


def test_orthography_alif_maqsura_is_not_folded():
    # على / علي must stay distinct.
    assert at.normalize_arabic_orthography("على") == "على"
    assert at.normalize_arabic_orthography("علي") == "علي"
    assert at.normalize_arabic_orthography("على") != at.normalize_arabic_orthography(
        "علي"
    )


def test_orthography_none_and_non_arabic_passthrough():
    assert at.normalize_arabic_orthography(None) == ""
    assert at.normalize_arabic_orthography("") == ""
    assert at.normalize_arabic_orthography("Hello 123!") == "Hello 123!"
    assert at.normalize_arabic_orthography(123) == "123"


# ---------------------------------------------------------------------------
# normalize_digits
# ---------------------------------------------------------------------------


def test_normalize_digits_arabic_indic_and_persian():
    assert at.normalize_digits("٣٫٥") == "3.5"
    assert at.normalize_digits("۱۲۳") == "123"
    assert at.normalize_digits("١٬٠٠٠") == "1.000"
    assert at.normalize_digits("عام ٢٠٢٤") == "عام 2024"


def test_normalize_digits_none_and_ascii_passthrough():
    assert at.normalize_digits(None) == ""
    assert at.normalize_digits("2024") == "2024"


# ---------------------------------------------------------------------------
# normalized_match_text
# ---------------------------------------------------------------------------


def test_match_text_lowercases_and_strips_punctuation():
    assert at.normalized_match_text("Hello, World!") == "hello world"
    assert at.normalized_match_text("النجاح!") == "النجاح"


def test_match_text_unifies_spelling_but_keeps_distinct_words():
    assert at.normalized_match_text("أحمد") == at.normalized_match_text("احمد")
    assert at.normalized_match_text("على") != at.normalized_match_text("علي")


def test_match_text_empty_and_none():
    assert at.normalized_match_text("") == ""
    assert at.normalized_match_text(None) == ""


# ---------------------------------------------------------------------------
# light_stem / stemmed_tokens
# ---------------------------------------------------------------------------


def test_light_stem_strips_prefixes():
    assert at.light_stem("النجاح") == "نجاح"
    assert at.light_stem("والنجاح") == "نجاح"
    assert at.light_stem("بالنجاح") == "نجاح"
    assert at.light_stem("للنجاح") == "نجاح"


def test_light_stem_strips_one_suffix_only():
    assert at.light_stem("النجاحات") == "نجاح"
    assert at.light_stem("المعلمون") == "معلم"
    assert at.light_stem("المعلمين") == "معلم"
    assert at.light_stem("كتابه") == "كتاب"
    assert at.light_stem("كتابي") == "كتاب"


def test_light_stem_documented_example():
    # «إتقان» matches «وإتقانها» (docstring example).
    assert at.light_stem("وإتقانها") == at.light_stem("إتقان") == "اتقان"


def test_light_stem_minimum_length_guard():
    # Never strips below the minimum stem length.
    assert at.light_stem("ال") == "ال"
    assert at.light_stem("علم") == "علم"
    # Bare ب/ك/ل are deliberately NOT prefixes (they collide with roots).
    assert at.light_stem("كتاب") == "كتاب"
    assert at.light_stem("برنامج") == "برنامج"
    assert at.light_stem("لعب") == "لعب"
    # Orthography unification still applies first: ة -> ه even when no
    # affix is stripped.
    assert at.light_stem("لغة") == "لغه"


def test_light_stem_non_arabic_is_lowercased_only():
    assert at.light_stem("Hello") == "hello"
    assert at.light_stem("123") == "123"
    assert at.light_stem("") == ""


def test_stemmed_tokens():
    assert at.stemmed_tokens("وإتقانها والنجاح") == ["اتقان", "نجاح"]
    assert at.stemmed_tokens("") == []
    assert at.stemmed_tokens("Hello world") == ["hello", "world"]


# ---------------------------------------------------------------------------
# collapse_spaces
# ---------------------------------------------------------------------------


def test_collapse_spaces():
    assert at.collapse_spaces("a  b\tc") == "a b c"
    assert at.collapse_spaces("  padded  ") == "padded"
    assert at.collapse_spaces("a\u200bb  c") == "a b c"  # ZWSP run collapses too
    assert at.collapse_spaces(None) == ""
    assert at.collapse_spaces("") == ""


# ---------------------------------------------------------------------------
# Three normalization levels
# ---------------------------------------------------------------------------


def test_to_display_is_identity():
    text = "السَّلامُ ٣ خطوات!"
    assert at.to_display(text) == text
    assert at.to_display(None) == ""
    assert at.to_display(5) == "5"


def test_to_comparison_keeps_punctuation_and_case():
    assert at.to_comparison("النجاح! ٣ خطوات") == "النجاح! 3 خطوات"
    assert at.to_comparison("Hello  World") == "Hello World"


def test_to_search_lowercases_and_strips_punctuation():
    assert at.to_search("النجاح! ٣ خطوات") == "النجاح 3 خطوات"
    assert at.to_search("Hello, World!") == "hello world"


def test_search_tokens():
    assert at.search_tokens("كيف 123!") == ["كيف", "123"]
    assert at.search_tokens("") == []
    assert at.search_tokens("  a   b  ") == ["a", "b"]


# ---------------------------------------------------------------------------
# language_profile / dominant_language
# ---------------------------------------------------------------------------


def test_language_profile_empty():
    profile = at.language_profile("")
    assert profile == {
        "arabic": 0,
        "latin": 0,
        "arabic_ratio": 0.0,
        "latin_ratio": 0.0,
        "script": "none",
        "dominant": "none",
    }


def test_language_profile_arabic_and_english():
    assert at.language_profile("مرحبا")["script"] == "ar"
    assert at.language_profile("مرحبا")["dominant"] == "ar"
    assert at.language_profile("hello")["script"] == "en"
    assert at.language_profile("hello")["dominant"] == "en"


def test_language_profile_mixed_majority_wins():
    profile = at.language_profile("مرحبا hello world foo bar")
    assert profile["script"] == "mixed"
    assert profile["dominant"] == "en"
    assert profile["arabic"] == 5
    assert profile["latin"] == 16
    assert profile["arabic_ratio"] == 0.238


def test_language_profile_tie_goes_arabic():
    # Exactly 50/50: script stays "mixed", dominant resolves to Arabic.
    profile = at.language_profile("مرحبا abcde")
    assert profile["script"] == "mixed"
    assert profile["dominant"] == "ar"


def test_language_profile_ratio_boundaries():
    assert at.language_profile("مرحباا abcd")["script"] == "ar"  # 6/10 = 0.6
    assert at.language_profile("مر abcdefgh")["script"] == "en"  # 2/10 = 0.2


def test_dominant_language():
    assert at.dominant_language("مرحبا") == "ar"
    assert at.dominant_language("hello") == "en"
    assert at.dominant_language("") == "none"
    assert at.dominant_language("مرحبا hello world foo bar") == "en"


# ---------------------------------------------------------------------------
# RTL helpers
# ---------------------------------------------------------------------------


def test_is_rtl_first_strong_character_decides():
    assert at.is_rtl("مرحبا") is True
    assert at.is_rtl("  مرحبا") is True
    assert at.is_rtl("hello") is False
    assert at.is_rtl("  hello") is False
    assert at.is_rtl("123") is False
    assert at.is_rtl("") is False
    assert at.is_rtl("5 مرحبا") is True


def test_rtl_display_disabled_is_byte_exact():
    text = "مرحبا بالعالم"
    assert at.rtl_display(text) == text
    assert at.rtl_display(text, enabled=False) == text
    assert at.rtl_display(None, enabled=True) == ""


def test_rtl_display_enabled_wraps_rtl_only():
    assert at.rtl_display("مرحبا", enabled=True) == "\u2068مرحبا\u2069"
    assert at.rtl_display("hello", enabled=True) == "hello"
