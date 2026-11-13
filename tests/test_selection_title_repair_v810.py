# -*- coding: utf-8 -*-
"""Regression tests for the 8.1 selection/title repair.

Each test pins one defect that shipped in 8.0: a boundary snap that turned a
clip into the whole video, an unbounded min-duration extension, a cut anchored
five ASR lines early, a validated title that never reached the publish field,
Arabic number formats rejected as hallucinations, and title truncation that
could hide an invented claim.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import create_viral_segments as cvs
from scripts import title_factual as tf
from scripts.title_text import fit_publish_title


def _lines(n, step=3.0, gap=0.05):
    out = []
    time_value = 0.0
    for index in range(n):
        out.append({
            "start": round(time_value, 2),
            "end": round(time_value + step - gap, 2),
            "text": "جملة رقم {} فيها كلام متصل عن الموضوع".format(index),
        })
        time_value += step
    return out


def _candidate(title, start, end, **extra):
    payload = {
        "title": title,
        "start_time": start,
        "end_time": end,
        "score": 80,
        "hook_strength": 80,
        "narrative_completeness": 80,
        "clarity_score": 80,
        "novelty_score": 80,
    }
    payload.update(extra)
    return payload


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------

def test_snap_does_not_merge_every_candidate_into_the_whole_video():
    transcript = _lines(18)
    raw = [_candidate("A", 5, 25), _candidate("B", 25, 45)]
    result = cvs.process_segments(raw, transcript, 10, 60, output_count=4)
    segments = sorted(result["segments"], key=lambda item: item["start_time"])
    assert len(segments) == 2, "two distinct moments must survive"
    assert segments[0]["end_time"] < 40, "clip A must not swallow the video"
    assert segments[1]["start_time"] < segments[0]["end_time"]


def test_min_extension_is_bounded_to_the_minimum():
    # One unpunctuated ASR block spanning 55 s: a 2 s candidate used to be
    # expanded to the block's far end (43 s of unrelated speech).
    transcript = [{"start": 0.0, "end": 55.0,
                   "text": "كلام متواصل بدون أي علامات ترقيم على الإطلاق " * 3}]
    result = cvs.process_segments([_candidate("Short", 12, 14)], transcript, 15, 60)
    assert result["segments"], "a connected 15 s window must exist"
    segment = result["segments"][0]
    assert 15.0 <= segment["duration"] <= 18.5
    assert segment["end_time"] < 30.0


def test_alignment_does_not_cut_five_lines_before_the_anchor():
    transcript = [{"start": i * 3.0, "end": i * 3.0 + 2.9,
                   "text": "سطر {} مختلف تماما عن غيره".format(i)} for i in range(120)]
    raw = [{
        "title": "Anchor",
        "start_time_ref": "(300s)",
        "start_text": "نص لا يشبه أي سطر هنا إطلاقا",
        "end_time": 310,
        "score": 80,
    }]
    result = cvs.process_segments(raw, transcript, 5, 60)
    segment = result["segments"][0]
    assert segment["start_time"] >= 290.0, "cut must stay near the 300 s anchor"


def test_missing_ref_uses_a_global_text_search_instead_of_the_video_head():
    transcript = [{"start": i * 5.0, "end": i * 5.0 + 4.5,
                   "text": "كلام عادي رقم {}".format(i)} for i in range(120)]
    transcript[60]["text"] = "هذه العبارة الفريدة عن النجاح والفشل"
    raw = [{
        "title": "Global",
        "start_time_ref": "(0s)",
        "start_text": "هذه العبارة الفريدة عن النجاح والفشل",
        "end_time": 305,
        "score": 80,
    }]
    result = cvs.process_segments(raw, transcript, 5, 60)
    assert result["segments"], "the far-away text anchor must be found"
    assert result["segments"][0]["start_time"] >= 295.0


def test_media_overshoot_is_clamped_not_dropped():
    transcript = [
        {"start": 0.0, "end": 10.0, "text": "افتتاحية عادية"},
        {"start": 58.9, "end": 60.5, "text": "خاتمة تتجاوز نهاية الملف بقليل"},
    ]
    raw = [_candidate("Tail", 58.5, 59.5)]
    result = cvs.process_segments(raw, transcript, 1, 60, media_duration=60.0)
    assert result["segments"], "clamping must keep the clip, not delete it"
    assert result["segments"][0]["end_time"] <= 60.0


def test_unrelated_short_window_is_rejected_not_mislabelled():
    transcript = [
        {"start": 0.0, "end": 2.0, "text": "موضوع اول هنا"},
        {"start": 35.0, "end": 45.0, "text": "شيء مختلف تماما عن السابق"},
    ]
    result = cvs.process_segments([_candidate("Short", 0, 2)], transcript, 15, 60)
    assert result["segments"] == []


# ---------------------------------------------------------------------------
# Titles
# ---------------------------------------------------------------------------

def test_validated_primary_replaces_a_hallucinated_recommended_title():
    window = "اختيار مهارة واحدة وإتقانها أفضل من تشتيت الجهد"
    transcript = [
        {"start": 0.0, "end": 10.0, "text": window},
        {"start": 10.5, "end": 20.0, "text": "نقطة ثانية مهمة في نفس الموضوع"},
    ]
    raw = [_candidate(
        "موضوع", 0, 10,
        recommended_title="ربحت 5000 دولار في يوم واحد",
        alt_titles=["اختيار مهارة واحدة وإتقانها"],
    )]
    segment = cvs.process_segments(raw, transcript, 5, 30)["segments"][0]
    assert segment["recommended_title"] == "اختيار مهارة واحدة وإتقانها"
    assert segment["title_data"]["llm_title_replaced"] == "ربحت 5000 دولار في يوم واحد"


def test_arabic_thousands_separator_is_not_a_decimal_point():
    result = tf.validate_title_vs_clip(
        "جمعنا ٣٬٥٠٠ دولار", "جمعنا 3500 دولار", "ar")
    assert result["status"] == "verified", result["reasons"]
    assert tf.title_numbers("٣٬٥٠٠") == {"3500"}


def test_digit_plus_scale_word_equals_the_numeric_amount():
    result = tf.validate_title_vs_clip(
        "جمعنا 20 ألف دولار", "جمعنا 20000 دولار", "ar")
    assert result["status"] == "verified", result["reasons"]


def test_number_words_compose_with_scales():
    assert "25000" in tf.title_numbers("خمسة وعشرون ألف شخص")
    assert "3000000" in tf.title_numbers("ثلاثة ملايين مشاهدة")
    # The bare scale word is a number too, so an invented "million" is caught.
    result = tf.validate_title_vs_clip(
        "جمعت مليون في الصباح", "جمعت الفريق في الصباح وبدينا الخدمة", "ar")
    assert result["status"] == "rejected"


def test_idiomatic_wahid_is_not_treated_as_the_number_one():
    result = tf.validate_title_vs_clip(
        "واحد من الناس حكا لي قصة", "الناس حكاو لي قصة", "ar")
    assert result["status"] != "rejected", result["reasons"]


def test_mixed_clip_rejects_a_foreign_only_title():
    window = "هذا المنتج vraiment pas cher و الجودة عالية vraiment top qualité"
    result = tf.validate_title_vs_clip(
        "vraiment pas cher top qualité", window, "auto")
    assert result["status"] == "rejected"
    assert any("language_mismatch" in reason for reason in result["reasons"])


def test_claim_beyond_the_publish_limit_is_still_validated():
    filler = " ".join(["كلمة"] * 30)
    long_title = filler + " ربحت 9999 دولار"
    assert len(long_title) > tf.PUBLISH_TITLE_LIMIT
    outcome = tf.validate_all_title_candidates(
        long_title, [], "هذا المقطع يتحدث عن كلمة فقط", "ar")
    rejected_texts = [item["text"] for item in outcome["rejected"]]
    assert any("9999" in text for text in rejected_texts)
    assert "9999" not in outcome["primary"]["text"]


def test_truncation_never_ends_on_a_bare_function_word():
    text = "ا" * 90 + " من" + "ب" * 20
    fitted = fit_publish_title(text, 100)
    assert len(fitted) <= 100
    assert not fitted.rstrip("…").rstrip().endswith("من")


def test_rejected_alt_titles_are_not_offered():
    window = "اختيار مهارة واحدة وإتقانها أفضل من تشتيت الجهد"
    result = cvs.process_segments(
        [_candidate("موضوع", 0, 10,
                    recommended_title="اختيار مهارة واحدة وإتقانها",
                    alt_titles=["ربحت 5000 دولار في يوم واحد"])],
        [{"start": 0.0, "end": 10.0, "text": window}], 5, 30)
    assert result["segments"][0]["alt_titles"] == []


def test_fallback_never_quotes_a_mid_sentence_start():
    window = "ب السبب الحقيقي الذي جعل الناس يهتمون بالموضوع"
    analysis = {
        "text": window,
        "first_sentence": window,
        "last_sentence": "",
        "starts_mid_sentence": True,
    }
    titles = tf.fallback_titles(analysis, "ar")
    assert all(not item["text"].startswith("ب ") for item in titles)


def test_unpunctuated_asr_is_not_one_giant_sentence():
    from scripts import transcript_window as tw
    transcript = _lines(10, step=6.0, gap=0.1)
    units = tw.split_sentence_units(transcript)
    assert len(units) >= 3, "unpunctuated ASR must split at a bounded length"
    analysis = tw.analyze_window(transcript, 6.0, 12.0)
    assert analysis["starts_mid_sentence"] is False
    assert analysis["ends_mid_sentence"] is False
    assert len(analysis["first_sentence"].split()) < 20


def test_speech_blocks_are_capped():
    blocks = cvs._speech_blocks(_lines(10, step=6.0, gap=0.1))
    assert len(blocks) >= 3
    assert all(end - start <= cvs.MAX_SENTENCE_BLOCK_SECONDS + 0.01
               for start, end in blocks[:-1])


def test_selection_config_records_the_ai_route():
    transcript = _lines(6)
    result = cvs.process_segments(
        [_candidate("Route", 6, 18)], transcript, 5, 30,
        selection_meta={"ai_backend": "auto", "ai_model": "qwen2.5:7b",
                        "ai_chain": ["qwen2.5:7b (local)"],
                        "ai_used": "qwen2.5:7b (local)"})
    config = result["selection_config"]
    assert config["ai_backend"] == "auto"
    assert config["ai_model"] == "qwen2.5:7b"
    assert config["ai_chain"] == ["qwen2.5:7b (local)"]
    assert config["ai_used"] == "qwen2.5:7b (local)"
