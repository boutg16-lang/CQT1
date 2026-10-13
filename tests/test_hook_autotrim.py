from scripts import create_viral_segments as cvs
from scripts import hook_analyzer


def _timed(text_words):
    """Build [{start,end,word}] with 0.5s per word starting at 11.0."""
    timings = []
    cursor = 11.0
    for word in text_words:
        timings.append({"start": cursor, "end": cursor + 0.45, "word": word})
        cursor += 0.5
    return timings


def test_leading_filler_trim_returns_plan():
    plan = hook_analyzer.leading_filler_trim(
        _timed(["يعني", "طيب", "القصة", "بدأت", "هنا"]))
    assert plan["trim_words"] == 2
    assert plan["filler_words"] == ["يعني", "طيب"]
    assert plan["new_start"] == 12.0
    assert plan["next_word"] == "القصة"


def test_leading_filler_trim_handles_punctuation_and_variants():
    plan = hook_analyzer.leading_filler_trim(
        _timed(["يعني،", "إيه", "الحل", "واضح"]))
    assert plan is not None
    assert plan["trim_words"] == 2


def test_leading_filler_trim_rejects_non_filler_opening():
    assert hook_analyzer.leading_filler_trim(
        _timed(["ليش", "الناس", "تخاف"])) is None


def test_leading_filler_trim_rejects_long_hesitation_run():
    words = _timed(["يعني", "طيب", "امم", "اه", "أوكي", "حسنا", "المحتوى"])
    assert hook_analyzer.leading_filler_trim(words, max_filler=4) is None


def test_leading_filler_trim_rejects_all_filler():
    assert hook_analyzer.leading_filler_trim(_timed(["يعني", "طيب"])) is None
    assert hook_analyzer.leading_filler_trim([]) is None
    assert hook_analyzer.leading_filler_trim(None) is None


# ---------------------------------------------------------------------------
# process_segments integration
# ---------------------------------------------------------------------------

def _transcript():
    return [
        {"start": 0.0, "end": 10.0, "text": "مقدمة عادية عن الموضوع"},
        {"start": 11.0, "end": 25.0, "text": "يعني طيب ليش الناس تخاف من التغيير الكبير؟ الجواب صادم!"},
        {"start": 26.0, "end": 40.0, "text": "تفاصيل إضافية تكمّل القصة"},
        {"start": 45.0, "end": 60.0, "text": "خاتمة هادئة بلا لحظة قوية"},
    ]


def _raw():
    return [
        {"title": "لحظة قوية", "start_time": 11, "end_time": 40, "score": 95},
        {"title": "لحظة ضعيفة", "start_time": 45, "end_time": 60, "score": 40},
    ]


def _word_timings():
    return _timed([
        "يعني", "طيب", "ليش", "الناس", "تخاف", "من", "التغيير",
    ]) + [{"start": 15.0, "end": 39.5, "word": "استمرار"}]


def test_opening_analysis_attached_without_flag(tmp_path, monkeypatch):
    monkeypatch.delenv("VIRALCUTTER_HOOK_AUTOTRIM", raising=False)
    monkeypatch.setattr(cvs, "_load_word_timings", lambda folder: _word_timings())
    result = cvs.process_segments(
        _raw(), _transcript(), 15, 90, project_folder=str(tmp_path))
    first = result["segments"][0]
    assert "opening_analysis" in first
    assert first["opening_analysis"]["opens_with_filler"] is True
    assert first["start_time"] == 11.0  # untouched without the flag


def test_autotrim_removes_filler_prefix(tmp_path, monkeypatch):
    monkeypatch.setenv("VIRALCUTTER_HOOK_AUTOTRIM", "1")
    monkeypatch.setattr(cvs, "_load_word_timings", lambda folder: _word_timings())
    result = cvs.process_segments(
        _raw(), _transcript(), 15, 90, project_folder=str(tmp_path))
    first = result["segments"][0]
    assert first["hook_trimmed_from"] == 11.0
    assert first["start_time"] == 12.0
    assert first["duration"] == 28.0
    assert first["opening_analysis"]["auto_trimmed"] is True
    # the refreshed opening is no longer filler-led
    assert first["opening_analysis"]["opens_with_filler"] is False
    # the second segment was untouched
    assert result["segments"][1]["start_time"] == 45.0


def test_autotrim_respects_min_duration(tmp_path, monkeypatch):
    monkeypatch.setenv("VIRALCUTTER_HOOK_AUTOTRIM", "1")
    # window 11-40 with a long filler run ending at 28.0: trimming to the
    # content word would leave less than the 15s minimum → skip the trim
    timings = _timed(["يعني", "طيب"]) + [{"start": 28.0, "end": 28.4, "word": "المحتوى"}]
    monkeypatch.setattr(cvs, "_load_word_timings", lambda folder: timings)
    result = cvs.process_segments(
        _raw(), _transcript(), 15, 90, project_folder=str(tmp_path))
    assert result["segments"][0]["start_time"] == 11.0
    assert "hook_trimmed_from" not in result["segments"][0]


def test_autotrim_fail_open_on_broken_timings(tmp_path, monkeypatch):
    monkeypatch.setenv("VIRALCUTTER_HOOK_AUTOTRIM", "1")
    monkeypatch.setattr(cvs, "_load_word_timings",
                        lambda folder: [{"start": 12.0, "end": 12.4, "word": None}])
    result = cvs.process_segments(
        _raw(), _transcript(), 15, 90, project_folder=str(tmp_path))
    assert result["segments"][0]["start_time"] == 11.0  # untouched


def test_drop_leading_filler_tokens_stops_at_content():
    assert hook_analyzer.drop_leading_filler_tokens(
        "يعني طيب ليش الناس تخاف؟", 4) == "ليش الناس تخاف؟"
    assert hook_analyzer.drop_leading_filler_tokens(
        "ليش الناس تخاف؟", 3) == "ليش الناس تخاف؟"
    assert hook_analyzer.drop_leading_filler_tokens("", 2) == ""
