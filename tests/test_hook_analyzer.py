from scripts import hook_analyzer


def test_clean_question_opening_scores_high():
    report = hook_analyzer.analyze_opening("ليش الناس تخاف من التغيير؟ الجواب راح يفاجئك")
    assert report["has_question_hook"] is True
    assert report["opens_with_filler"] is False
    assert report["opens_mid_thought"] is False
    assert report["hook_score"] >= 68.0
    assert report["trim_words"] == 0


def test_filler_prefix_is_detected_and_trimmed():
    report = hook_analyzer.analyze_opening("يعني طيب أه اللي صار إني وجدت الحل النهائي")
    assert report["opens_with_filler"] is True
    assert report["filler_word"] == "يعني"
    assert report["trim_words"] == 3
    assert any("حشو" in note for note in report["notes"])
    clean = hook_analyzer.analyze_opening("اللي صار إني وجدت الحل النهائي")
    assert report["hook_score"] < clean["hook_score"]


def test_mid_thought_opener_is_flagged():
    report = hook_analyzer.analyze_opening("وبعدين قال لي إن المشروع كله اتغير")
    assert report["opens_mid_thought"] is True
    assert report["continuation_marker"] == "وبعدين"
    assert any("منتصف فكرة" in note for note in report["notes"])


def test_number_and_personal_hooks_add_score():
    report = hook_analyzer.analyze_opening("أنت تعرف إن 90 بالمية من الناس يطبقونها غلط")
    assert report["has_number_hook"] is True
    assert report["has_personal_hook"] is True
    baseline = hook_analyzer.analyze_opening("الناس يطبقون الطريقة دي غلط كثير")
    assert report["hook_score"] > baseline["hook_score"]


def test_arabic_orthography_is_normalised():
    # أ/إ/آ and ة variants must not hide a filler opener
    report = hook_analyzer.analyze_opening("إيه يعني الموضوع بسيط")
    assert report["opens_with_filler"] is True


def test_empty_text_scores_zero():
    report = hook_analyzer.analyze_opening("")
    assert report["hook_score"] == 0.0
    assert report["notes"]


def test_score_stays_within_bounds():
    weak = hook_analyzer.analyze_opening("يعني طيب وبعدين امم")
    strong = hook_analyzer.analyze_opening(
        "ليش أنت ضيعت 3 سنين؟! والله القصة مجنونة!!! صدمني الجواب؟")
    assert 0.0 <= weak["hook_score"] <= 100.0
    assert 0.0 <= strong["hook_score"] <= 100.0
    assert strong["hook_score"] > weak["hook_score"]


def test_analyze_segment_opening_prefers_transcript_text():
    segment = {
        "title": "عنوان تجريبي",
        "transcript_text": "ليش الناس تسأل هذا السؤال دائماً؟",
        "hook_text": "نص بديل",
    }
    report = hook_analyzer.analyze_segment_opening(segment)
    assert report["has_question_hook"] is True
    assert report["segment_title"] == "عنوان تجريبي"


def test_rank_openings_sorts_and_does_not_mutate():
    segments = [
        {"title": "ضعيف", "transcript_text": "يعني طيب الموضوع عادي"},
        {"title": "قوي", "transcript_text": "ليش أنت هنا؟ 3 أسباب"},
    ]
    ranked = hook_analyzer.rank_openings(segments)
    assert ranked[0]["title"] == "قوي"
    assert ranked[0]["opening_analysis"]["hook_score"] > ranked[1]["opening_analysis"]["hook_score"]
    assert "opening_analysis" not in segments[0]  # input untouched
