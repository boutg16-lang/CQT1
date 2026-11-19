# -*- coding: utf-8 -*-
"""Direct tests for scripts/title_factual.py.

Factual title validation: number/claim grounding against the exact clip
window, scoring, deterministic fallbacks and the v7.41 validation schema.
Stdlib only; no network, no LLM (the llm_entailment hook is mocked).
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import title_factual as tf  # noqa: E402

WINDOW = "اليوم نتحدث عن كيف تكسب المال من الإنترنت بسهولة"
GROUNDED_TITLE = "كيف تكسب المال من الإنترنت"


# ---------------------------------------------------------------------------
# Number extraction
# ---------------------------------------------------------------------------

def test_title_numbers_plain_digits():
    assert tf.title_numbers("ربحت 5000 دولار") == {"5000"}


def test_title_numbers_arabic_indic_thousands():
    # ٣٬٥٠٠ with the Arabic thousands separator is 3500, not 3.5.
    assert tf.title_numbers("ربحت ٣٬٥٠٠ ريال") == {"3500"}


def test_title_numbers_decimal_separator():
    assert tf.title_numbers("نسبة 1.5 بالمئة") == {"1.5"}
    assert tf.title_numbers("نسبة 1٫5 بالمئة") == {"1.5"}


def test_title_numbers_digit_scale_composite():
    # "20 ألف" is one fact (20000), not {20, 1000}.
    assert tf.title_numbers("ربحت 20 ألف ريال") == {"20000"}


def test_title_numbers_arabic_number_words():
    assert tf.title_numbers("ربحت عشرون ألف ريال") == {"20000"}
    assert tf.title_numbers("ثلاثة ملايين مشاهدة") == {"3000000"}
    assert tf.title_numbers("خمسة وعشرون ألف مشترك") == {"25000"}


def test_title_numbers_bare_scale_word_is_a_value():
    assert tf.title_numbers("ربحت مليون ريال") == {"1000000"}


def test_title_numbers_no_numbers():
    assert tf.title_numbers("كيف تكسب المال") == set()


def test_canonical_number_group_lengths():
    assert tf._canonical_number("3,500") == "3500"
    assert tf._canonical_number("3.500") == "3500"
    assert tf._canonical_number("1.5") == "1.5"
    assert tf._canonical_number("42") == "42"


# ---------------------------------------------------------------------------
# hallucinated_facts / factual_accuracy_score / relevance
# ---------------------------------------------------------------------------

def test_hallucinated_facts_unsupported_number():
    assert tf.hallucinated_facts("ربحت 5000 دولار", WINDOW) == ["5000"]


def test_hallucinated_facts_supported_number():
    assert tf.hallucinated_facts("ربحت 5000 دولار",
                                 "ربحت 5000 دولار من الإنترنت") == []


def test_hallucinated_facts_unsupported_latin_token():
    assert tf.hallucinated_facts("iPhone الجديد", "الجديد في عالم الهواتف") == [
        "iphone"]


def test_hallucinated_facts_supported_latin_token():
    assert tf.hallucinated_facts("iPhone الجديد",
                                 "iPhone الجديد في عالم الهواتف") == []


def test_factual_accuracy_fully_grounded():
    assert tf.factual_accuracy_score(GROUNDED_TITLE, WINDOW) == 100.0


def test_factual_accuracy_empty_title():
    assert tf.factual_accuracy_score("", WINDOW) == 0.0


def test_factual_accuracy_invented_number_capped_at_40():
    # Three grounded words (3 checks pass) + one invented number (2 fail):
    # raw 60.0, but any invented hard fact caps factuality at 40.0.
    assert tf.factual_accuracy_score("كيف تكسب المال 5000", WINDOW) == 40.0
    # Nothing grounded at all scores 0, still within the cap.
    assert tf.factual_accuracy_score("ربحت 5000 دولار", WINDOW) == 0.0


def test_factual_accuracy_pure_stopwords_is_mediocre_not_zero():
    # A generic title with nothing checkable scores 50, not 0.
    assert tf.factual_accuracy_score("في هذا", WINDOW) == 50.0


def test_transcript_relevance_full_and_none():
    assert tf.transcript_relevance_score(GROUNDED_TITLE, WINDOW) == 100.0
    assert tf.transcript_relevance_score("الطقس جميل", WINDOW) == 0.0
    assert tf.transcript_relevance_score("", WINDOW) == 0.0
    assert tf.transcript_relevance_score(GROUNDED_TITLE, "") == 0.0


# ---------------------------------------------------------------------------
# Component scores
# ---------------------------------------------------------------------------

def test_clarity_score_good_title():
    assert tf.clarity_score("كيف تكسب المال من الإنترنت؟") == 80.0


def test_clarity_score_empty():
    assert tf.clarity_score("") == 0.0


def test_clarity_score_shouty_and_caps():
    assert tf.clarity_score("HOW TO MAKE MONEY!!!") < 80.0
    assert tf.clarity_score("المال المال المال المال") < 80.0


def test_curiosity_score_question_and_hook():
    assert tf.curiosity_score("كيف تكسب المال؟") == 85.0


def test_curiosity_score_number_only():
    assert tf.curiosity_score("5 خطوات للنجاح") == 45.0


def test_curiosity_score_empty():
    assert tf.curiosity_score("") == 0.0


def test_seo_score_delegates_to_seo_titles():
    from scripts import seo_titles
    expected = seo_titles.score_title(GROUNDED_TITLE)["score"]
    assert tf.seo_score(GROUNDED_TITLE) == float(expected)


def test_contains_clickbait_pattern():
    assert tf.contains_clickbait_pattern("لن تصدق ما حدث") is True
    assert tf.contains_clickbait_pattern("shocking news today") is True
    assert tf.contains_clickbait_pattern("طريقة عادية لتعلم البرمجة") is False


def test_language_match_score_exact_and_mismatch():
    assert tf.language_match_score("مرحبا بالعالم", "ar", WINDOW) == 100.0
    assert tf.language_match_score("Hello world English", "ar", WINDOW) == 10.0


def test_language_match_score_none_is_neutral():
    assert tf.language_match_score("12345", "ar", WINDOW) == 60.0
    assert tf.language_match_score(GROUNDED_TITLE, "", None) == 60.0


def test_language_match_score_auto_resolves_from_window():
    assert tf.language_match_score(GROUNDED_TITLE, "auto", WINDOW) == 100.0
    assert tf.language_match_score("Hello world English", "auto", WINDOW) == 10.0


def test_detect_content_language():
    assert tf.detect_content_language(WINDOW) == "ar"
    assert tf.detect_content_language("Hello world this is English") == "en"
    assert tf.detect_content_language("") == "none"


def test_score_title_candidate_keys_and_range():
    result = tf.score_title_candidate(GROUNDED_TITLE, WINDOW)
    assert set(result) == {
        "factual_accuracy_score", "transcript_relevance_score",
        "clarity_score", "curiosity_score", "SEO_score",
        "language_match_score", "clickbait_penalty", "final_title_score"}
    assert 0.0 <= result["final_title_score"] <= 100.0
    assert result["factual_accuracy_score"] == 100.0


def test_score_title_candidate_clickbait_penalty():
    result = tf.score_title_candidate("لن تصدق ما حدث", WINDOW)
    assert result["clickbait_penalty"] == 30.0


# ---------------------------------------------------------------------------
# validate_title / title_needs_replacement
# ---------------------------------------------------------------------------

def test_validate_title_grounded():
    result = tf.validate_title(GROUNDED_TITLE, WINDOW)
    assert result["matches_transcript"] is True
    assert result["contains_hallucinated_fact"] is False
    assert result["hallucinated_facts"] == []
    assert result["language_matches_content"] is True
    assert result["within_length_limit"] is True
    assert result["excessive_clickbait"] is False


def test_validate_title_hallucinated_and_too_long():
    result = tf.validate_title("ربحت 5000 دولار", WINDOW)
    assert result["contains_hallucinated_fact"] is True
    assert result["hallucinated_facts"] == ["5000"]
    assert result["matches_transcript"] is False
    long_title = "x" * 101
    assert tf.validate_title(long_title, WINDOW)["within_length_limit"] is False


def test_title_needs_replacement_hallucinated():
    needs, _ = tf.title_needs_replacement("ربحت 5000 دولار", WINDOW)
    assert needs is True


def test_title_needs_replacement_grounded():
    needs, _ = tf.title_needs_replacement(GROUNDED_TITLE, WINDOW)
    assert needs is False


def test_title_needs_replacement_low_factual_with_content():
    needs, _ = tf.title_needs_replacement("الطقس جميل اليوم", WINDOW)
    assert needs is True


def test_title_needs_replacement_empty_window_cannot_disprove():
    needs, _ = tf.title_needs_replacement("الطقس جميل اليوم", "")
    assert needs is False


# ---------------------------------------------------------------------------
# fallback_titles
# ---------------------------------------------------------------------------

def test_fallback_titles_first_last_and_question():
    analysis = {
        "text": "المال مهم جدا. العمل مهم أيضا. هل تعلم كيف؟",
        "first_sentence": "المال مهم جدا.",
        "last_sentence": "هل تعلم كيف؟",
    }
    titles = tf.fallback_titles(analysis)
    styles = [item["style"] for item in titles]
    assert "direct" in styles
    assert titles[0]["text"].startswith("المال")


def test_fallback_titles_mid_sentence_start_drops_first():
    analysis = {
        "text": "ب السبب الحقيقي للنجاح هو العمل الجاد كل يوم.",
        "first_sentence": "ب السبب الحقيقي للنجاح هو العمل الجاد.",
        "last_sentence": "العمل الجاد كل يوم.",
        "starts_mid_sentence": True,
    }
    titles = tf.fallback_titles(analysis)
    assert all("ب السبب" not in item["text"] for item in titles)


def test_fallback_titles_empty_analysis():
    assert tf.fallback_titles({}) == []
    assert tf.fallback_titles(None) == []


def test_fallback_titles_max_count():
    analysis = {
        "text": "المال مهم جدا. العمل مهم أيضا. هل تعلم كيف؟",
        "first_sentence": "المال مهم جدا.",
        "last_sentence": "العمل مهم أيضا.",
    }
    assert len(tf.fallback_titles(analysis, max_count=1)) == 1


# ---------------------------------------------------------------------------
# unsupported_claims / full-video scope
# ---------------------------------------------------------------------------

def test_unsupported_claims_superlative():
    assert tf.unsupported_claims("أفضل طريقة مجانا", "نتحدث عن طريقة") == [
        "أفضل", "مجانًا"]


def test_unsupported_claims_supported():
    assert tf.unsupported_claims("أفضل طريقة", "هذه أفضل طريقة للتعلم") == []


def test_unsupported_claims_unit_group():
    result = tf.unsupported_claims("ربحت 5000 ريال", "ربحت 5000 من العمل")
    assert "ريال" in result


def test_unsupported_claims_unit_supported_by_any_variant():
    # Unit groups are per-currency: "ريال" is supported by "ريالات" (same
    # group), but NOT by "دولار" (a different currency group).
    assert tf.unsupported_claims("ربحت 5000 ريال",
                                 "ربحت 5000 ريالات من العمل") == []
    assert tf.unsupported_claims("ربحت 5000 ريال",
                                 "ربحت 5000 دولار من العمل") == ["ريال"]


def test_detect_full_video_scope_needs_ratio():
    assert tf.detect_full_video_scope("كل الفيديو في دقيقة", WINDOW) is False


def test_detect_full_video_scope_small_clip():
    assert tf.detect_full_video_scope("كل الفيديو في دقيقة", WINDOW,
                                      clip_ratio=0.3) is True


def test_detect_full_video_scope_large_clip_or_in_window():
    assert tf.detect_full_video_scope("كل الفيديو في دقيقة", WINDOW,
                                      clip_ratio=0.6) is False
    assert tf.detect_full_video_scope(
        "كل الفيديو في دقيقة",
        "هذا الفيديو يتحدث عن كل الفيديو في دقيقة",
        clip_ratio=0.3) is False


# ---------------------------------------------------------------------------
# validate_title_vs_clip (v7.41 schema)
# ---------------------------------------------------------------------------

def test_validate_vs_clip_verified():
    result = tf.validate_title_vs_clip(GROUNDED_TITLE, WINDOW)
    assert result["status"] == "verified"
    assert result["matches_clip_transcript"] is True
    assert result["factual_consistency"] == 1.0
    assert result["reasons"] == []
    assert result["checks"]["schema_version"] == "1.1"


def test_validate_vs_clip_rejected_invented_number():
    result = tf.validate_title_vs_clip("ربحت 5000 دولار", WINDOW)
    assert result["status"] == "rejected"
    assert result["contains_unsupported_number"] is True
    assert result["matches_clip_transcript"] is False
    assert any("unsupported_number" in reason for reason in result["reasons"])


def test_validate_vs_clip_rejected_unsupported_claim():
    result = tf.validate_title_vs_clip("أفضل طريقة مجانا", WINDOW)
    assert result["status"] == "rejected"
    assert result["contains_unsupported_claim"] is True


def test_validate_vs_clip_rejected_language_mismatch():
    result = tf.validate_title_vs_clip("Hello world English", WINDOW)
    assert result["status"] == "rejected"
    assert result["language_matches_clip"] is False


def test_validate_vs_clip_review_partial_overlap():
    # Relevance 50 (2/4 tokens grounded) -> factual 50 < 60 -> review.
    result = tf.validate_title_vs_clip("المال مهم والعمل والنجاح",
                                       "المال مهم جدا")
    assert result["status"] == "review"
    assert result["matches_clip_transcript"] is True


def test_validate_vs_clip_review_empty_title():
    result = tf.validate_title_vs_clip("", WINDOW)
    assert result["status"] == "review"


def test_validate_vs_clip_review_over_length():
    title = ("المال مهم جدا في حياتنا اليومية والعمل " * 3).strip()
    assert len(title) > 100
    window = "المال مهم جدا في حياتنا اليومية والعمل"
    result = tf.validate_title_vs_clip(title, window)
    assert result["status"] == "review"
    assert any("title_exceeds_length_limit" in r for r in result["reasons"])


def test_validate_vs_clip_review_soft_entity():
    # Latin brand with no Latin in the window: review, not rejected.
    # NOTE: an unsupported Latin token also caps factual accuracy at 40.0,
    # so the review goes through the factual threshold, not the
    # "unverified_entity" branch (that branch is unreachable while the cap
    # applies) — the verdict is still "review", never "rejected".
    title = "المال مهم جدا في حياتنا اليومية والعمل والنجاح iPhone"
    window = "المال مهم جدا في حياتنا اليومية والعمل والنجاح"
    result = tf.validate_title_vs_clip(title, window)
    assert result["status"] == "review"
    assert result["contains_unsupported_entity"] is True
    assert result["checks"]["factual_accuracy_score"] == 40.0


def test_validate_vs_clip_llm_entailment_false_rejects():
    result = tf.validate_title_vs_clip(
        GROUNDED_TITLE, WINDOW, llm_entailment=lambda t, w: False)
    assert result["status"] == "rejected"
    assert "llm_entailment_failed" in result["reasons"]
    assert result["checks"]["llm_entailment"] is False


def test_validate_vs_clip_llm_entailment_none_or_raising_stands():
    result = tf.validate_title_vs_clip(
        GROUNDED_TITLE, WINDOW, llm_entailment=lambda t, w: None)
    assert result["status"] == "verified"

    def _boom(title, window):
        raise RuntimeError("llm down")

    result = tf.validate_title_vs_clip(
        GROUNDED_TITLE, WINDOW, llm_entailment=_boom)
    assert result["status"] == "verified"
    assert result["checks"]["llm_entailment"] is None


def test_validate_vs_clip_full_video_scope_rejects():
    result = tf.validate_title_vs_clip("كل الفيديو في دقيقة", WINDOW,
                                       clip_ratio=0.3)
    assert result["status"] == "rejected"
    assert "full_video_scope" in result["reasons"]


# ---------------------------------------------------------------------------
# validate_all_title_candidates / build_title_data
# ---------------------------------------------------------------------------

def test_validate_all_candidates_primary_verified():
    result = tf.validate_all_title_candidates(
        GROUNDED_TITLE, ["المال مهم جدا"], WINDOW)
    assert result["primary"]["validation"]["status"] == "verified"
    assert result["verified_available"] is True
    assert result["review_required"] is False
    assert result["rejected"] == []


def test_validate_all_candidates_rejected_primary_kept_raw():
    result = tf.validate_all_title_candidates(
        "ربحت 5000 دولار", [], WINDOW)
    assert result["primary"]["validation"]["status"] == "rejected"
    assert result["rejected"][0]["text"] == "ربحت 5000 دولار"


def test_validate_all_candidates_dedupes_alternatives():
    result = tf.validate_all_title_candidates(
        GROUNDED_TITLE, [GROUNDED_TITLE, GROUNDED_TITLE], WINDOW)
    assert result["alternatives"] == []


def test_validate_all_candidates_nothing_verified_adds_fallback():
    # Primary is rejected (no overlap), but the transcript-derived fallback
    # verifies, so review is NOT required and the fallback is offered.
    result = tf.validate_all_title_candidates(
        "الطقس جميل اليوم", [], "المال مهم جدا في حياتنا")
    assert result["primary"]["validation"]["status"] == "rejected"
    assert result["verified_available"] is True
    assert result["review_required"] is False
    assert result["alternatives"]
    assert result["alternatives"][0]["text"] == "المال مهم جدا في حياتنا"


def test_build_title_data_verified_primary():
    result = tf.build_title_data(
        GROUNDED_TITLE, ["المال مهم جدا"], WINDOW, {})
    assert result["primary_title"] == GROUNDED_TITLE
    assert result["title_review_required"] is False
    assert result["fallback_used"] is False
    assert result["title_is_factual"] is True
    assert result["title_language"] == "ar"
    assert result["schema_version"] == "1.1"
    assert set(result) >= {
        "primary_title", "title_language", "title_confidence",
        "title_is_factual", "fallback_used", "title_scores",
        "alternative_titles", "validation", "title_validation",
        "validated_alternatives", "rejected_titles",
        "title_review_required", "schema_version"}


def test_build_title_data_all_rejected_uses_fallback():
    result = tf.build_title_data("ربحت 99999 دولار", [], WINDOW, {})
    assert result["fallback_used"] is True
    assert result["primary_title"] != "ربحت 99999 دولار"
    assert result["rejected_titles"]


def test_build_title_data_empty_window_needs_review():
    result = tf.build_title_data("المال مهم", [], "", {})
    assert result["title_review_required"] is True
