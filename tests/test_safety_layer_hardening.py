# -*- coding: utf-8 -*-
"""Regression tests for fail-closed safety-layer fixes.

Covers three defects found in review:

* ``semantic_safety`` matched raw-orthography patterns against
  ``normalize_text()``-ed text, so its flagship hate phrases never fired.
* ``censor_engine`` left a stale ``censor_map.json`` behind when it bailed
  out, which the upload gate then trusted as censor evidence.
* ``ocr_safety`` reported a clean "scanned/allow" pass when every sampled
  frame failed, i.e. it failed open with zero evidence.
"""
import json
import os

from scripts import censor_engine, ocr_safety, semantic_safety, upload_gate

# ---------------------------------------------------------------------------
# semantic_safety: patterns must live in the same orthographic space as text
# ---------------------------------------------------------------------------

class TestSemanticPatternsAreNormalized:
    def test_flagship_exclusion_phrase_is_blocked(self):
        # normalize_text folds these to "هولاء ناس لا يستحقون حياه".
        verdict = semantic_safety.analyze_text("هؤلاء الناس لا يستحقون الحياة")
        assert verdict["action"] == "block"
        assert "protected_or_collective_target" in verdict["signals"]

    def test_extermination_noun_is_blocked(self):
        # "إبادة" (extermination) was missing from the violent-verb list, so
        # only the noun form slipped through while "ابيد" was caught.
        verdict = semantic_safety.analyze_text("يجب إبادة المسيحيين")
        assert verdict["action"] == "block"
        assert "violent_or_coercive_verb" in verdict["signals"]

    def test_dialect_exclusion_variant_is_blocked(self):
        verdict = semantic_safety.analyze_text("هذولا الناس ما يستاهلوش الحياة")
        assert verdict["action"] == "block"

    def test_counter_speech_stays_review_not_allow(self):
        verdict = semantic_safety.analyze_text("نحن ضد الكراهية ولا نؤيد قتل المهاجرين")
        assert verdict["action"] == "review"
        assert "counter_speech_or_educational_context" in verdict["signals"]

    def test_ordinary_arabic_text_is_allowed(self):
        for text in ("نصائح عملية لتحسين جودة الفيديو والصوت",
                     "القنفذ حيوان أليف ومفيد للغاية"):
            assert semantic_safety.analyze_text(text)["action"] == "allow"

    def test_normalization_helper_agrees_with_patterns(self):
        # Guard the invariant directly: every phrase must match its own
        # normalized form through the compiled pattern.
        for phrase in semantic_safety._GROUP_PHRASES:
            normalized = semantic_safety.normalize_text(phrase)
            assert semantic_safety._matched(
                semantic_safety._GROUPS, normalized), phrase


# ---------------------------------------------------------------------------
# censor_engine: a failed run must not leave evidence the gate will trust
# ---------------------------------------------------------------------------

def _censor_report(index=0):
    return {
        "segments": [{
            "index": index,
            "status": "censor",
            "title": "Risky",
            "semantic": {"action": "allow", "explanation": "word-level match"},
        }]
    }


def _stale_map():
    return {
        "mode": "censor",
        "segments": {
            "0": {
                "title": "Risky",
                "start_time": 10.0,
                "end_time": 20.0,
                "spans": [{"term": "bad", "start": 11.0, "end": 12.0}],
                "muted_words": 1,
                "video_censored": True,
                "subtitle_masked": 1,
            }
        },
        "errors": [],
    }


def test_failed_censor_run_removes_stale_map(tmp_path):
    (tmp_path / "censor_map.json").write_text(
        json.dumps(_stale_map()), encoding="utf-8")

    # No input.json ⇒ bleep censoring cannot locate words and bails out.
    result = censor_engine.censor_project(
        str(tmp_path), {"segments": [{"title": "Risky", "start_time": 10, "end_time": 20}]})

    assert result.get("error") == "no_word_transcript"
    assert not (tmp_path / "censor_map.json").exists()


def test_gate_refuses_censor_status_after_failed_censor_run(tmp_path):
    (tmp_path / "censor_map.json").write_text(
        json.dumps(_stale_map()), encoding="utf-8")
    (tmp_path / "safety_report.json").write_text(
        json.dumps(_censor_report()), encoding="utf-8")

    censor_engine.censor_project(
        str(tmp_path), {"segments": [{"title": "Risky", "start_time": 10, "end_time": 20}]})

    verdict = upload_gate.check_clip(
        str(tmp_path), index=0, title="Clean", caption="Clean", hashtags=[])

    assert verdict["allowed"] is False
    assert any(reason["source"] == "censor_verification"
               for reason in verdict["reasons"])


def test_stale_map_alone_is_not_evidence(tmp_path):
    # Guard the baseline: with a stale map present and no censoring attempted,
    # the gate must not accept the entry just because the file exists.
    (tmp_path / "censor_map.json").write_text(
        json.dumps(_stale_map()), encoding="utf-8")
    (tmp_path / "safety_report.json").write_text(
        json.dumps(_censor_report()), encoding="utf-8")
    os.remove(tmp_path / "censor_map.json")

    verdict = upload_gate.check_clip(
        str(tmp_path), index=0, title="Clean", caption="Clean", hashtags=[])

    assert verdict["allowed"] is False


# ---------------------------------------------------------------------------
# ocr_safety: zero usable evidence must fail closed
# ---------------------------------------------------------------------------

def test_all_frames_failed_fails_closed(monkeypatch):
    monkeypatch.setattr(ocr_safety, "availability",
                        lambda *a, **k: {"available": True, "binary": "tesseract",
                                         "reason": None})
    monkeypatch.setattr(ocr_safety, "_duration", lambda *a, **k: 30.0)

    def boom(*args, **kwargs):
        raise RuntimeError("ffmpeg exploded")

    monkeypatch.setattr(ocr_safety, "_frame_png", boom)

    report = ocr_safety.analyze_video("clip.mp4", frames=3)

    assert report["available"] is False
    assert report["status"] == "failed"
    assert report["reason"] == "all_frames_failed"
    assert report["action"] == "review"
    assert all(frame.get("error") for frame in report["frames"])


def test_partial_frame_failure_still_scans(monkeypatch):
    monkeypatch.setattr(ocr_safety, "availability",
                        lambda *a, **k: {"available": True, "binary": "tesseract",
                                         "reason": None})
    monkeypatch.setattr(ocr_safety, "_duration", lambda *a, **k: 30.0)

    calls = {"n": 0}

    def flaky(video_path, seconds, ffmpeg="ffmpeg"):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("first frame failed")
        return b"png"

    monkeypatch.setattr(ocr_safety, "_frame_png", flaky)
    monkeypatch.setattr(ocr_safety, "_recognize", lambda *a, **k: "")

    report = ocr_safety.analyze_video("clip.mp4", frames=3)

    assert report["status"] == "scanned"
    assert report["available"] is True
    assert report["action"] == "allow"
