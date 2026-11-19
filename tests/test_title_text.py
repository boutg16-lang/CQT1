# -*- coding: utf-8 -*-
"""Direct tests for scripts/title_text.py — publish-title text helpers.

Pure stdlib helpers: Arabic/Latin script detection, emoji budget and
word-boundary title fitting. No I/O, no network, no third-party imports.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import title_text as tt  # noqa: E402

# ---------------------------------------------------------------------------
# is_arabic_script
# ---------------------------------------------------------------------------

def test_is_arabic_script_arabic():
    assert tt.is_arabic_script("مرحبا بالعالم") is True


def test_is_arabic_script_latin():
    assert tt.is_arabic_script("Hello world") is False


def test_is_arabic_script_empty_and_none():
    assert tt.is_arabic_script("") is False
    assert tt.is_arabic_script(None) is False


def test_is_arabic_script_digits_only():
    assert tt.is_arabic_script("12345") is False


def test_is_arabic_script_supplement_block():
    # U+0750 Arabic Supplement counts as Arabic.
    assert tt.is_arabic_script("\u0750") is True


def test_is_arabic_script_presentation_forms_excluded():
    # Presentation forms (U+FB50+) are deliberately excluded.
    assert tt.is_arabic_script("\ufb50\ufb51") is False


def test_is_arabic_script_mixed():
    assert tt.is_arabic_script("Hello مرحبا") is True


# ---------------------------------------------------------------------------
# detect_text_script
# ---------------------------------------------------------------------------

def test_detect_arabic_dominant():
    assert tt.detect_text_script("مرحبا بالعالم الجميل") == "ar"


def test_detect_english_dominant():
    assert tt.detect_text_script("Hello world this is English") == "en"


def test_detect_mixed_middle_ratio():
    # 5 Arabic letters vs 5 Latin letters -> ratio 0.5 -> mixed.
    assert tt.detect_text_script("مرحبا hello") == "mixed"


def test_detect_mixed_low_ratio_with_arabic():
    # 5 Arabic vs 10 Latin -> ratio 0.33 -> mixed (not 'en': ratio > 0.2).
    assert tt.detect_text_script("Hello world مرحبا") == "mixed"


def test_detect_none_empty_digits_other_script():
    assert tt.detect_text_script("") == "none"
    assert tt.detect_text_script("12345 !!!") == "none"
    # Cyrillic letters are "other" letters, not Arabic or Latin.
    assert tt.detect_text_script("Привет мир") == "none"


def test_detect_ar_boundary_ratio_06():
    # Exactly 3 Arabic / 2 Latin -> ratio 0.6 -> 'ar'.
    assert tt.detect_text_script("مرحبا ab") == "ar"


def test_detect_en_boundary_ratio_02():
    # 1 Arabic / 4 Latin -> ratio 0.2 -> 'en'.
    assert tt.detect_text_script("م abcd") == "en"


# ---------------------------------------------------------------------------
# scripts_match
# ---------------------------------------------------------------------------

def test_scripts_match_same_script():
    assert tt.scripts_match("مرحبا بالعالم", "هذا نص عربي") is True
    assert tt.scripts_match("Hello world", "English caption here") is True


def test_scripts_match_ar_vs_en_mismatch():
    assert tt.scripts_match("مرحبا بالعالم", "Hello world English") is False
    assert tt.scripts_match("Hello world English", "مرحبا بالعالم") is False


def test_scripts_match_mixed_tolerated():
    assert tt.scripts_match("مرحبا hello", "Hello world English") is True
    assert tt.scripts_match("Hello world English", "مرحبا hello") is True


def test_scripts_match_empty_or_none_side():
    assert tt.scripts_match("", "Hello world") is True
    assert tt.scripts_match("مرحبا", "") is True
    assert tt.scripts_match("123", "مرحبا") is True
    assert tt.scripts_match(None, None) is True


# ---------------------------------------------------------------------------
# count_emoji / strip_excess_emoji
# ---------------------------------------------------------------------------

def test_count_emoji_none():
    assert tt.count_emoji("plain text") == 0
    assert tt.count_emoji("") == 0
    assert tt.count_emoji(None) == 0


def test_count_emoji_single_and_multiple():
    assert tt.count_emoji("hello 😀 world") == 1
    assert tt.count_emoji("🎉🎉🎉") == 3


def test_count_emoji_heart_with_variation_selector():
    # U+2764 is in the counted range; U+FE0F is not a code point in it.
    assert tt.count_emoji("❤️") == 1


def test_strip_excess_emoji_within_budget_unchanged():
    text = "a 😀 b"
    assert tt.strip_excess_emoji(text, max_emoji=1) == text


def test_strip_excess_emoji_drops_extras():
    result = tt.strip_excess_emoji("a 😀 b 🎉 c 🔥", max_emoji=1)
    assert "😀" in result
    assert "🎉" not in result
    assert "🔥" not in result
    assert "a" in result and "b" in result and "c" in result


def test_strip_excess_emoji_zero_budget():
    assert tt.strip_excess_emoji("a 😀 b", max_emoji=0) == "a  b"


def test_strip_excess_emoji_drops_variation_selector():
    result = tt.strip_excess_emoji("❤️ 🎉", max_emoji=1)
    assert "🎉" not in result
    assert "\ufe0f" not in result
    assert "❤" in result


def test_strip_excess_emoji_empty():
    assert tt.strip_excess_emoji("") == ""
    assert tt.strip_excess_emoji(None) is None


# ---------------------------------------------------------------------------
# fit_publish_title
# ---------------------------------------------------------------------------

def test_fit_none_and_empty():
    assert tt.fit_publish_title(None) == ""
    assert tt.fit_publish_title("") == ""
    assert tt.fit_publish_title("   ") == ""


def test_fit_limit_zero_or_negative():
    assert tt.fit_publish_title("hello world", 0) == ""
    assert tt.fit_publish_title("hello world", -5) == ""


def test_fit_short_title_unchanged():
    text = "Learn Python fast"
    assert tt.fit_publish_title(text, 100) == text


def test_fit_exact_limit_unchanged_no_ellipsis():
    text = "abcdefghij"
    assert tt.fit_publish_title(text, 10) == text


def test_fit_truncates_at_word_boundary_with_ellipsis():
    result = tt.fit_publish_title("aaa bbb ccc ddd", 8)
    assert result == "aaa bbb…"
    assert len(result) <= 8


def test_fit_single_long_token_hard_cut():
    result = tt.fit_publish_title("abcdefghijklmnop", 8)
    assert result == "abcdefg…"
    assert len(result) == 8


def test_fit_drops_dangling_tail_word():
    # Prefix would end on "the" — a bare function word — so it is dropped.
    result = tt.fit_publish_title(
        "Learn Python the easy way for beginners today", 20)
    assert result == "Learn Python…"


def test_fit_drops_arabic_dangling_tail():
    result = tt.fit_publish_title(
        "الدرس الأول من سلسلة تعلم البرمجة للمبتدئين", 20)
    assert not result.rstrip("…").endswith("من")
    assert result.endswith("…")


def test_fit_zero_width_space_is_a_boundary():
    result = tt.fit_publish_title("aaa\u200bbbbbbbbbb", 8)
    assert result == "aaa…"


def test_fit_trims_surrounding_whitespace():
    assert tt.fit_publish_title("  hello world  ", 100) == "hello world"


def test_fit_arabic_title():
    text = "كيف تكسب المال من الإنترنت بسهولة وبدون خبرة"
    result = tt.fit_publish_title(text, 20)
    assert result.endswith("…")
    assert len(result) <= 20
    # Never cut in the middle of a word: last char before '…' is not a space.
    assert result[-2] != " "
