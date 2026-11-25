from scripts import hook_analyzer


def test_clean_question_opening_scores_high():
    report = hook_analyzer.analyze_opening(
        "ليش الناس تخاف من التغيير؟ الجواب راح يفاجئك"
    )
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
    report = hook_analyzer.analyze_opening(
        "أنت تعرف إن 90 بالمية من الناس يطبقونها غلط"
    )
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
        "ليش أنت ضيعت 3 سنين؟! والله القصة مجنونة!!! صدمني الجواب؟"
    )
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
    assert (
        ranked[0]["opening_analysis"]["hook_score"]
        > ranked[1]["opening_analysis"]["hook_score"]
    )
    assert "opening_analysis" not in segments[0]  # input untouched


# --- feat/pro-cutting item 4: greeting/intro penalty ------------------------


class TestGreetingIntroPenalty:
    GREETING = "السلام عليكم ورحمة الله، اهلا بكم اعزائي المشاهدين. "
    STRONG = "لما رفضوا الدفع انفجر الموقف بالكامل. هنا القصة الحقيقية."

    def test_detect_greeting_flags_welcome_words(self):
        report = hook_analyzer.detect_greeting_intro(
            self.GREETING + "اليوم نشرح الموضوع."
        )
        assert report["is_greeting"] is True
        assert report["intro_word_count"] >= 4

    def test_detect_intro_announcement_prefix(self):
        report = hook_analyzer.detect_greeting_intro(
            "في هذا الفيديو سنتحدث عن القبائل. " + self.STRONG
        )
        assert report["is_intro_announcement"] is True
        assert report["is_greeting"] is False
        assert report["intro_word_count"] == len("في هذا الفيديو".split())

    def test_clean_content_is_not_flagged(self):
        report = hook_analyzer.detect_greeting_intro(self.STRONG)
        assert report["is_greeting"] is False
        assert report["is_intro_announcement"] is False
        assert report["intro_word_count"] == 0

    def test_opening_score_penalizes_greeting(self):
        clean = hook_analyzer.analyze_opening(self.STRONG)
        greeted = hook_analyzer.analyze_opening(self.GREETING + self.STRONG)
        assert clean["hook_score"] > greeted["hook_score"]
        assert greeted["opens_with_greeting"] is True
        assert greeted["greeting_label"] == "greeting"

    def test_intro_announcement_penalized_and_trimmed(self):
        text = "في هذا الفيديو سنتحدث عن القبائل. " + self.STRONG
        report = hook_analyzer.analyze_opening(text)
        assert report["is_intro_announcement"] is True
        assert report["intro_word_count"] >= 3
        assert report["hook_score"] < 50.0

    def test_suggested_start_skips_to_strong_sentence(self):
        report = hook_analyzer.analyze_opening(self.GREETING + self.STRONG)
        assert report["suggested_start"]
        assert "لما رفضوا" in report["suggested_start"]

    def test_live_source_uses_strict_threshold(self):
        clean = hook_analyzer.analyze_opening(self.STRONG, source_is_live=True)
        assert clean["hook_threshold"] == hook_analyzer.LIVE_HOOK_PASS_SCORE
        assert clean["hook_score"] < clean["hook_threshold"]
        assert clean["passes_hook_threshold"] is False

    def test_segment_opening_propagates_live_flag(self):
        segment = {
            "title": "بث مباشر",
            "transcript_text": self.STRONG,
            "source_is_live": True,
        }
        report = hook_analyzer.analyze_segment_opening(segment)
        assert report["source_is_live"] is True
        assert report["hook_threshold"] == hook_analyzer.LIVE_HOOK_PASS_SCORE


class TestGreetingIntroPenalty:
    """Item 4: greeting/intro ceremony at the START must be flagged, penalised,
    and accompanied by a deterministic alternative start."""

    def test_detects_plain_greeting(self):
        report = hook_analyzer.detect_greeting_intro("السلام عليكم، اهلا بكم. الموضوع اليوم خطير.")
        assert report["is_greeting"] is True
        assert report["label"] == "greeting"
        assert report["intro_word_count"] >= 3

    def test_detects_intro_announcement(self):
        report = hook_analyzer.detect_greeting_intro("في هذا الفيديو سنتحدث عن القبائل والشرف.")
        assert report["is_intro_announcement"] is True
        assert report["label"] == "intro_announcement"
        assert report["intro_word_count"] >= 4

    def test_clean_content_is_not_flagged(self):
        report = hook_analyzer.detect_greeting_intro("لما رفضوا الدفع انفجر الموقف بالكامل.")
        assert report["is_greeting"] is False
        assert report["is_intro_announcement"] is False
        assert report["intro_word_count"] == 0

    def test_greeting_sentence_counts_all_ceremony_words(self):
        # religious padding between the welcome words belongs to the ceremony
        report = hook_analyzer.detect_greeting_intro(
            "السلام عليكم ورحمة الله وبركاته، اهلا بكم اعزائي المشاهدين. "
            "لما رفضوا الدفع انفجر الموقف بالكامل."
        )
        assert report["intro_word_count"] >= 8

    def test_analyze_opening_flags_and_penalises(self):
        report = hook_analyzer.analyze_opening(
            "السلام عليكم، اهلا بكم. لما رفضوا الدفع انفجر الموقف بالكامل."
        )
        assert report["opens_with_greeting"] is True
        assert report["hook_score"] < hook_analyzer.HOOK_PASS_SCORE
        assert report["passes_hook_threshold"] is False
        assert report["suggested_start"]

    def test_suggested_start_points_after_the_ceremony(self):
        text = (
            "السلام عليكم ورحمة الله، اهلا بكم اعزائي المشاهدين. "
            "لما رفضوا الدفع انفجر الموقف بالكامل. هنا القصة الحقيقية."
        )
        report = hook_analyzer.analyze_opening(text)
        assert report["suggested_start"].startswith("لما رفضوا")

    def test_live_source_threshold_is_stricter(self):
        text = "في هذا الفيديو سنتحدث عن القبائل والشرف والكرامة."
        normal = hook_analyzer.analyze_opening(text)
        live = hook_analyzer.analyze_opening(text, source_is_live=True)
        assert live["hook_threshold"] == hook_analyzer.LIVE_HOOK_PASS_SCORE
        assert live["hook_score"] < normal["hook_score"]
        assert live["passes_hook_threshold"] is False

    def test_segment_opening_respects_live_flag(self):
        segment = {
            "transcript_text": "مرحبا بكم في حلقة جديدة. الموضوع اليوم خطير جدا.",
            "is_live": True,
        }
        report = hook_analyzer.analyze_segment_opening(segment)
        assert report["hook_threshold"] == hook_analyzer.LIVE_HOOK_PASS_SCORE
        assert report["opens_with_greeting"] is True

    def test_ceremony_only_text_yields_no_suggestion(self):
        report = hook_analyzer.analyze_opening("في هذا الفيديو سنتحدث عن الموضوع.")
        # announcement-only, nothing strong follows
        assert report["suggested_start"] == ""
        assert report["hook_score"] < hook_analyzer.HOOK_PASS_SCORE
