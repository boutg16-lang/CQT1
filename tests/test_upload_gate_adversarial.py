# -*- coding: utf-8 -*-
"""Adversarial battery for the upload gate (permanent attack-suite).

The v7.45.0 review round proved that the safety layers were strong
*individually* but had fail-open paths in their *interactions*. This battery
turns that lesson into a permanent regression guard: a fully-evidenced
project is built once, then each test breaks exactly one thing and asserts
the gate still fails closed — plus a never-raises matrix over malformed
report files, and one fully-clean control proving the battery is not
vacuously always-false.
"""
import json
import os
import shutil
import subprocess
import threading

import pytest

from scripts import upload_gate

INDEX = 0
CLEAN_TITLE = "نصائح للمونتاج"
CLEAN_CAPTION = "ثلاث نصائح عملية لتحسين جودة الفيديو"


# ---------------------------------------------------------------------------
# Fixture: a fully-evidenced project that SHOULD pass
# ---------------------------------------------------------------------------

def _write(project, name, payload):
    with open(os.path.join(project, name), "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False)


def _segment(**overrides):
    base = {
        "index": INDEX,
        "title": CLEAN_TITLE,
        "start_time": 10.0,
        "end_time": 20.0,
        "selection_score": 82.0,
        "selection_readiness": "publish_ready",
        "completion_status": "complete",
        "final_validation": {"ok": True},
        "title_validation": {"status": "verified"},
        "title_confidence": 90.0,
        "transcript_text": "هذه ثلاث نصائح عملية لتحسين جودة الفيديو والصوت",
    }
    base.update(overrides)
    return base


def _safety_report(**overrides):
    base = {
        "mode": "block",
        "segments": [{
            "index": INDEX,
            "title": CLEAN_TITLE,
            "start_time": 10.0,
            "end_time": 20.0,
            "status": "safe",
            "semantic": {"action": "allow"},
        }],
        "ai_review_requested": False,
    }
    base.update(overrides)
    return base


def _scorecard(**overrides):
    base = {
        "segments": [{
            "index": INDEX,
            "title": CLEAN_TITLE,
            "start_time": 10.0,
            "end_time": 20.0,
            "overall": "low",
        }],
    }
    base.update(overrides)
    return base


@pytest.fixture()
def clean_project(tmp_path):
    project = str(tmp_path)
    _write(project, "viral_segments.txt", {"segments": [_segment()]})
    _write(project, "safety_report.json", _safety_report())
    _write(project, "risk_scorecard.json", _scorecard())
    return project


def _check(project, **kwargs):
    kwargs.setdefault("title", CLEAN_TITLE)
    kwargs.setdefault("caption", CLEAN_CAPTION)
    kwargs.setdefault("hashtags", [])
    kwargs.setdefault("require_reports", True)
    return upload_gate.check_clip(project, index=INDEX, **kwargs)


# ---------------------------------------------------------------------------
# Control: the battery must not be vacuously always-false
# ---------------------------------------------------------------------------

def test_control_fully_evidenced_clip_is_allowed(clean_project):
    verdict = _check(clean_project)
    assert verdict["allowed"] is True, verdict["reasons"]


def test_control_also_passes_the_quality_gate(clean_project):
    verdict = _check(clean_project, quality_gate=True)
    assert verdict["allowed"] is True, verdict["reasons"]


def test_control_with_a_real_rendered_video(clean_project, tmp_path):
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg required")
    final_dir = os.path.join(clean_project, "final")
    os.makedirs(final_dir)
    clip = os.path.join(final_dir, "000_clean.mp4")
    subprocess.run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=10:duration=2",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100",
        "-t", "2", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
        clip], check=True)
    verdict = _check(clean_project, require_video=True)
    assert verdict["allowed"] is True, verdict["reasons"]


# ---------------------------------------------------------------------------
# Fail-closed: every single broken thing must sink the upload
# ---------------------------------------------------------------------------

class TestPrepublishEvidence:
    def test_missing_safety_report(self, clean_project):
        os.remove(os.path.join(clean_project, "safety_report.json"))
        assert _check(clean_project)["allowed"] is False

    def test_missing_risk_scorecard(self, clean_project):
        os.remove(os.path.join(clean_project, "risk_scorecard.json"))
        assert _check(clean_project)["allowed"] is False

    def test_truncated_safety_report_fails_closed(self, clean_project):
        with open(os.path.join(clean_project, "safety_report.json"), "w") as handle:
            handle.write('{"mode": "block", "segments": [{"index": 0, "sta')
        assert _check(clean_project)["allowed"] is False

    def test_safety_mode_off_is_refused(self, clean_project):
        _write(clean_project, "safety_report.json", _safety_report(mode="off"))
        assert _check(clean_project)["allowed"] is False

    def test_evidence_for_a_different_window_does_not_cover(self, clean_project):
        report = _safety_report()
        report["segments"][0]["start_time"] = 10.5  # > 0.25s tolerance
        _write(clean_project, "safety_report.json", report)
        assert _check(clean_project)["allowed"] is False

    def test_incomplete_contextual_review_is_refused(self, clean_project):
        report = _safety_report(ai_review_requested=True, ai_review_status="failed")
        _write(clean_project, "safety_report.json", report)
        assert _check(clean_project)["allowed"] is False

    def test_contextual_review_covering_the_clip_is_accepted(self, clean_project):
        report = _safety_report(
            ai_review_requested=True,
            ai_review_status="complete",
            ai_reviewed_clips=[{
                "index": INDEX, "start_time": 10.0, "end_time": 20.0,
                "status": "safe",
            }])
        _write(clean_project, "safety_report.json", report)
        assert _check(clean_project)["allowed"] is True


class TestBlockSignalsAlwaysWin:
    def test_publish_blocklist(self, clean_project):
        _write(clean_project, "publish_blocklist.json",
               {"blocked": [{"index": INDEX, "axes": {"reuse": {"score": 92}}}]})
        verdict = _check(clean_project)
        assert verdict["allowed"] is False
        assert any(reason["source"] == "publish_blocklist"
                   for reason in verdict["reasons"])

    def test_blocked_segment(self, clean_project):
        report = _safety_report()
        report["segments"][0]["status"] = "blocked"
        _write(clean_project, "safety_report.json", report)
        assert _check(clean_project)["allowed"] is False

    def test_semantic_review_verdict(self, clean_project):
        report = _safety_report()
        report["segments"][0]["semantic"] = {"action": "review"}
        _write(clean_project, "safety_report.json", report)
        assert _check(clean_project)["allowed"] is False

    def test_provenance_block(self, clean_project):
        _write(clean_project, "provenance_report.json", {
            "policy": "block",
            "clips": [{"index": INDEX, "action": "block", "reasons": ["no rights"]}],
        })
        assert _check(clean_project)["allowed"] is False

    def test_unverified_censor_status_is_refused(self, clean_project):
        report = _safety_report()
        report["segments"][0]["status"] = "censor"
        _write(clean_project, "safety_report.json", report)
        assert _check(clean_project)["allowed"] is False

    def test_export_blocked_segment(self, clean_project):
        _write(clean_project, "viral_segments.txt",
               {"segments": [_segment(export_blocked=True)]})
        assert _check(clean_project)["allowed"] is False

    def test_requires_review_segment(self, clean_project):
        _write(clean_project, "viral_segments.txt",
               {"segments": [_segment(requires_review=True)]})
        assert _check(clean_project)["allowed"] is False

    def test_title_review_required_segment(self, clean_project):
        _write(clean_project, "viral_segments.txt",
               {"segments": [_segment(title_review_required=True)]})
        assert _check(clean_project)["allowed"] is False

    def test_hate_phrase_in_the_publish_title(self, clean_project):
        verdict = _check(clean_project, title="هؤلاء الناس لا يستحقون الحياة")
        assert verdict["allowed"] is False

    def test_quality_gate_missing_selection_evidence(self, clean_project):
        _write(clean_project, "viral_segments.txt", {"segments": []})
        verdict = _check(clean_project, quality_gate=True)
        assert verdict["allowed"] is False


# ---------------------------------------------------------------------------
# Never-raises matrix: malformed inputs must return a verdict, not crash
# ---------------------------------------------------------------------------

REPORT_FILES = [
    "safety_report.json",
    "risk_scorecard.json",
    "publish_blocklist.json",
    "provenance_report.json",
    "viral_segments.txt",
    "censor_map.json",
]

MALFORMED_PAYLOADS = [
    "{not json",
    "[1, 2, 3]",
    '"just a string"',
    "42",
    '{"segments": {"not": "a list"}}',
    '{"segments": [null, 42, "junk"]}',
    '{"segments": [{"start_time": "abc", "end_time": null, "index": []}]}',
    '{"index": 0, "title": "' + "عنوان طويل جداً " * 500 + '"}',
]


@pytest.mark.parametrize("report_file", REPORT_FILES)
@pytest.mark.parametrize("payload", MALFORMED_PAYLOADS)
def test_gate_never_raises_on_malformed_files(clean_project, report_file, payload):
    with open(os.path.join(clean_project, report_file), "w", encoding="utf-8") as handle:
        handle.write(payload)
    verdict = _check(clean_project)
    assert isinstance(verdict, dict)
    assert isinstance(verdict["allowed"], bool)
    assert isinstance(verdict["reasons"], list)


def test_gate_never_raises_on_an_empty_project(tmp_path):
    verdict = upload_gate.check_clip(str(tmp_path), index=0, title="t", caption="c",
                                     hashtags=[], require_reports=True)
    assert verdict["allowed"] is False


def test_gate_never_raises_with_none_and_junk_arguments(clean_project):
    for kwargs in ({"index": None}, {"index": "abc"}, {"title": None, "caption": None}):
        verdict = upload_gate.check_clip(clean_project, hashtags=None,
                                         require_reports=True, **kwargs)
        assert isinstance(verdict, dict)


def test_gate_survives_concurrent_checks(clean_project):
    """Concurrent checks share the content-guard SQLite registry; contention
    may legitimately refuse (fail closed) but must never raise."""
    errors = []

    def worker():
        try:
            for _ in range(5):
                upload_gate.check_clip(clean_project, index=INDEX, title=CLEAN_TITLE,
                                       caption=CLEAN_CAPTION, hashtags=[],
                                       require_reports=True)
        except Exception as exc:  # pragma: no cover - failure path
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not errors


def test_gate_upload_raises_structured_error(clean_project):
    _write(clean_project, "publish_blocklist.json",
           {"blocked": [{"index": INDEX}]})
    with pytest.raises(upload_gate.UploadGateError) as caught:
        upload_gate.gate_upload(clean_project, INDEX, CLEAN_TITLE, CLEAN_CAPTION, [],
                                require_reports=True)
    assert caught.value.reasons
    assert all("source" in reason for reason in caught.value.reasons)
