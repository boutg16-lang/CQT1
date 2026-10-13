# -*- coding: utf-8 -*-
"""Tests for the upload gate (forced refusal before publishing)."""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import upload_gate as ug


def _write(project, name, data):
    with open(os.path.join(project, name), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _write_current_publish_evidence(project, *, ai_review=None, title="Short clip"):
    clip = {"index": 0, "title": title, "start_time": 10.0, "end_time": 20.0}
    segment = {
        **clip,
        "selection_score": 88,
        "quality_status": "verified",
        "completion_status": "complete",
        "selection_readiness": "publish_ready",
        "final_validation": {"ok": True, "errors": []},
        "title_validation": {"status": "verified"},
        "title_confidence": 88,
        "recommended_title": title,
    }
    _write(project, "viral_segments.txt", {"segments": [segment]})
    safety = {
        "mode": "block",
        "segments": [{**clip, "status": "safe"}],
        "ai_review_requested": True,
        "ai_review_status": "complete",
        "ai_review_backend": "gemini",
        "ai_reviewed_clips": [clip],
        "ai_review": ai_review or [],
    }
    _write(project, ug.SAFETY_REPORT, safety)
    _write(project, ug.SCORECARD, {"segments": [clip]})


class TestBlocklist:
    def test_clean_project_allows(self, tmp_path):
        verdict = ug.check_clip(str(tmp_path), 0, "Nice title", "Nice caption", ["shorts"])
        assert verdict["allowed"] is True
        assert verdict["reasons"] == []

    def test_semantic_import_failure_refuses_upload(self, tmp_path, monkeypatch):
        import sys

        monkeypatch.setitem(sys.modules, "scripts.semantic_safety", None)
        verdict = ug.check_clip(str(tmp_path), 0, "Nice title", "Nice caption", ["shorts"])

        assert verdict["allowed"] is False
        reason = next(
            reason for reason in verdict["reasons"]
            if reason.get("code") == "semantic_safety_unavailable"
        )
        assert reason["severity"] == "high"
        assert "ModuleNotFoundError" in reason["detail"] or "ImportError" in reason["detail"]

    def test_semantic_analysis_failure_refuses_upload(self, tmp_path, monkeypatch):
        from scripts import semantic_safety

        def fail_analysis(_text):
            raise RuntimeError("semantic engine failed")

        monkeypatch.setattr(semantic_safety, "analyze_text", fail_analysis)
        verdict = ug.check_clip(str(tmp_path), 0, "Nice title", "Nice caption", ["shorts"])

        assert verdict["allowed"] is False
        reason = next(
            reason for reason in verdict["reasons"]
            if reason.get("code") == "semantic_safety_unavailable"
        )
        assert reason["severity"] == "high"
        assert "RuntimeError: semantic engine failed" in reason["detail"]

    def test_blocked_clip_refused(self, tmp_path):
        _write(tmp_path, ug.PUBLISH_BLOCKLIST, {
            "blocked": [{"index": 0, "title": "Bad", "axes": {"reuse": {"score": 80}}}]})
        verdict = ug.check_clip(str(tmp_path), 0, "Title", "Caption", [])
        assert verdict["allowed"] is False
        assert any(r["source"] == "publish_blocklist" for r in verdict["reasons"])

    def test_other_index_not_blocked(self, tmp_path):
        _write(tmp_path, ug.PUBLISH_BLOCKLIST, {
            "blocked": [{"index": 1, "title": "Bad", "axes": {"reuse": {"score": 80}}}]})
        verdict = ug.check_clip(str(tmp_path), 0, "Title", "Caption", [])
        assert verdict["allowed"] is True

    def test_gate_upload_raises(self, tmp_path):
        _write(tmp_path, ug.PUBLISH_BLOCKLIST, {
            "blocked": [{"index": 0, "title": "Bad", "axes": {"reuse": {"score": 90}}}]})
        with pytest.raises(ug.UploadGateError) as ei:
            ug.gate_upload(str(tmp_path), 0, "Title", "Caption", [])
        assert any(r["severity"] == "high" for r in ei.value.reasons)

    def test_strict_publish_requires_safety_and_risk_reports(self, tmp_path):
        verdict = ug.check_clip(
            str(tmp_path), 0, "Title", "Caption", [], require_reports=True)
        assert verdict["allowed"] is False
        sources = {reason["source"] for reason in verdict["reasons"]}
        assert "prepublish_evidence" in sources
        _write_current_publish_evidence(tmp_path)
        verdict = ug.check_clip(
            str(tmp_path), 0, "Title", "Caption", [], require_reports=True)
        assert verdict["allowed"] is True

    def test_strict_reports_are_bound_to_the_current_clip_boundaries(self, tmp_path):
        _write_current_publish_evidence(tmp_path)
        _write(tmp_path, "viral_segments.txt", {"segments": [{
            "index": 0, "title": "Short clip", "start_time": 30.0, "end_time": 40.0,
        }]})
        verdict = ug.check_clip(
            str(tmp_path), 0, "Title", "Caption", [], require_reports=True)
        assert verdict["allowed"] is False
        assert any(reason["source"] == "prepublish_evidence" for reason in verdict["reasons"])

    def test_ai_flagged_clip_is_refused_even_when_report_says_review_complete(self, tmp_path):
        clip = {"index": 0, "title": "Short clip", "start_time": 10.0, "end_time": 20.0}
        _write_current_publish_evidence(tmp_path, ai_review=[{
            **clip, "status": "ai_flagged", "reason": "contextual hateful claim",
        }])
        verdict = ug.check_clip(
            str(tmp_path), 0, "Title", "Caption", [], require_reports=True)
        assert verdict["allowed"] is False
        assert any(reason["source"] == "safety_report" for reason in verdict["reasons"])

    def test_unavailable_contextual_review_blocks_strict_publish(self, tmp_path):
        _write_current_publish_evidence(tmp_path)
        report = json.loads((tmp_path / ug.SAFETY_REPORT).read_text(encoding="utf-8"))
        report["ai_review_status"] = "unavailable"
        _write(tmp_path, ug.SAFETY_REPORT, report)
        verdict = ug.check_clip(
            str(tmp_path), 0, "Title", "Caption", [], require_reports=True)
        assert verdict["allowed"] is False
        assert any(reason["source"] == "prepublish_evidence" for reason in verdict["reasons"])

    def test_strict_quality_rejects_weak_selection_and_title(self, tmp_path):
        _write(tmp_path, "viral_segments.txt", {"segments": [{
            "index": 0,
            "selection_score": 48,
            "quality_status": "verified",
            "completion_status": "complete",
            "title_validation": {"status": "verified"},
            "title_confidence": 88,
        }]})
        verdict = ug.check_clip(
            str(tmp_path), 0, "Title", "Caption", [], quality_gate=True)
        assert verdict["allowed"] is False
        assert any(reason["source"] == "selection_quality" for reason in verdict["reasons"])

        _write(tmp_path, "viral_segments.txt", {"segments": [{
            "index": 0,
            "selection_score": 88,
            "quality_status": "verified",
            "completion_status": "complete",
            "title_validation": {"status": "verified"},
            "title_confidence": 45,
        }]})
        verdict = ug.check_clip(
            str(tmp_path), 0, "Title", "Caption", [], quality_gate=True)
        assert verdict["allowed"] is False
        assert any(reason["source"] == "title_quality" for reason in verdict["reasons"])

    def test_strict_quality_maps_filename_index_to_segment_position(self, tmp_path):
        transcript = "الخطوة الأولى هي اختيار مهارة واحدة وإتقانها بعمق"
        _write(tmp_path, "viral_segments.txt", {"segments": [{
            "selection_score": 88,
            "quality_status": "verified",
            "completion_status": "complete",
            "selection_readiness": "publish_ready",
            "final_validation": {"ok": True, "errors": []},
            "recommended_title": "اختيار مهارة واحدة",
            "transcript_text": transcript,
            "title_review_required": False,
            "title_data": {
                "title_language": "ar",
                "title_confidence": 88,
                "title_validation": {"status": "verified", "checks": {"clip_ratio": 0.2}},
            },
        }]})
        verdict = ug.check_clip(
            str(tmp_path), 0, "اختيار مهارة واحدة", "", [], quality_gate=True)
        assert verdict["allowed"] is True

    def test_strict_quality_rechecks_an_edited_title_against_clip_transcript(self, tmp_path):
        transcript = "الخطوة الأولى هي اختيار مهارة واحدة وإتقانها بعمق"
        _write(tmp_path, "viral_segments.txt", {"segments": [{
            "selection_score": 88,
            "quality_status": "verified",
            "completion_status": "complete",
            "selection_readiness": "publish_ready",
            "final_validation": {"ok": True, "errors": []},
            "recommended_title": "اختيار مهارة واحدة",
            "transcript_text": transcript,
            "title_review_required": False,
            "title_data": {
                "title_language": "ar",
                "title_confidence": 88,
                "title_validation": {"status": "verified", "checks": {"clip_ratio": 0.2}},
            },
        }]})
        verdict = ug.check_clip(
            str(tmp_path), 0, "أفضل مهارة للثراء", "", [], quality_gate=True)
        assert verdict["allowed"] is False
        assert any(reason["source"] == "title_quality" for reason in verdict["reasons"])

    def test_strict_quality_fails_closed_when_selection_evidence_is_missing(self, tmp_path):
        verdict = ug.check_clip(
            str(tmp_path), 0, "Title", "Caption", [], quality_gate=True)
        assert verdict["allowed"] is False
        assert any(reason["source"] == "selection_quality" for reason in verdict["reasons"])


class TestSafetyReport:
    def test_safety_blocked_refused(self, tmp_path):
        _write(tmp_path, ug.SAFETY_REPORT, {
            "blocked": [{"index": 2, "reason": "hate speech (high)"}]})
        verdict = ug.check_clip(str(tmp_path), 2, "Title", "Caption", [])
        assert verdict["allowed"] is False
        assert any(r["source"] == "safety_report" for r in verdict["reasons"])

    def test_verified_censor_evidence_can_publish(self, tmp_path):
        _write_current_publish_evidence(tmp_path)
        safety_path = tmp_path / ug.SAFETY_REPORT
        safety = json.loads(safety_path.read_text(encoding="utf-8"))
        safety["segments"][0]["status"] = "censor"
        _write(tmp_path, ug.SAFETY_REPORT, safety)
        _write(tmp_path, ug.CENSOR_MAP, {
            "mode": "censor",
            "segments": {
                "0": {
                    "title": "Short clip",
                    "start_time": 10.0,
                    "end_time": 20.0,
                    "video_censored": True,
                    "subtitle_masked": 2,
                    "muted_words": 2,
                    "spans": [{"term": "slur", "start": 1.0, "end": 1.2}],
                }
            },
            "errors": [],
        })
        verdict = ug.check_clip(str(tmp_path), 0, "Short clip", "", [],
                                require_reports=True)
        assert verdict["allowed"] is True

    def test_incomplete_censor_is_refused(self, tmp_path):
        _write_current_publish_evidence(tmp_path)
        safety_path = tmp_path / ug.SAFETY_REPORT
        safety = json.loads(safety_path.read_text(encoding="utf-8"))
        safety["segments"][0]["status"] = "censor"
        _write(tmp_path, ug.SAFETY_REPORT, safety)
        _write(tmp_path, ug.CENSOR_MAP, {
            "mode": "censor",
            "segments": {
                "0": {
                    "title": "Short clip",
                    "start_time": 10.0,
                    "end_time": 20.0,
                    "video_censored": False,
                    "subtitle_masked": 0,
                    "muted_words": 2,
                    "spans": [{"term": "slur", "start": 1.0, "end": 1.2}],
                }
            },
            "errors": [{"index": 0, "error": "audio_censor_failed"}],
        })
        verdict = ug.check_clip(str(tmp_path), 0, "Short clip", "", [],
                                require_reports=True)
        assert verdict["allowed"] is False
        assert any(reason["source"] == "censor_verification" for reason in verdict["reasons"])


class TestMetadataGate:
    def test_medical_claim_blocks(self, tmp_path):
        verdict = ug.check_clip(str(tmp_path), 0, "This cures cancer", "", [])
        assert verdict["allowed"] is False
        assert any(r["source"] == "metadata_compliance" for r in verdict["reasons"])

    def test_clean_metadata_allows(self, tmp_path):
        verdict = ug.check_clip(str(tmp_path), 0, "Top 5 Tips", "Full video", ["shorts"])
        assert verdict["allowed"] is True


class TestUploaders:
    def test_uploader_blocks_before_any_sdk_call(self, tmp_path, monkeypatch):
        _write(tmp_path, ug.PUBLISH_BLOCKLIST, {
            "blocked": [{"index": 0, "title": "Bad", "axes": {"reuse": {"score": 85}}}]})
        uploader = ug.YouTubeUploader(str(tmp_path), dry_run=True)
        with pytest.raises(ug.UploadGateError):
            uploader.upload("clip.mp4", "Title", "Caption", [], index=0)

    def test_uploader_dry_run_allows_clean(self, tmp_path, capsys):
        uploader = ug.YouTubeUploader(str(tmp_path), dry_run=True)
        result = uploader.upload("clip.mp4", "Title", "Caption", ["shorts"], index=0)
        assert result["status"] == "dry-run"
        out = capsys.readouterr().out
        assert "DRY-RUN" in out

    def test_uploader_without_credentials_fails_loudly(self, tmp_path):
        _write_current_publish_evidence(tmp_path, title="Title")
        uploader = ug.TikTokUploader(str(tmp_path), dry_run=False)
        with pytest.raises(RuntimeError, match="OAuth credentials"):
            uploader.upload("clip.mp4", "Title", "Caption", [], index=0)

    def test_live_uploader_requires_current_reports_and_quality_evidence(self, tmp_path):
        uploader = ug.YouTubeUploader(str(tmp_path), dry_run=False)
        with pytest.raises(ug.UploadGateError) as exc:
            uploader.upload("clip.mp4", "Title", "Caption", [], index=0)
        assert any(reason["source"] == "prepublish_evidence" for reason in exc.value.reasons)
        assert any(reason["source"] == "selection_quality" for reason in exc.value.reasons)


class TestAudit:
    def test_audit_project(self, tmp_path):
        _write(tmp_path, ug.SCORECARD, {
            "segments": [
                {"index": 0, "title": "Clean"},
                {"index": 1, "title": "Dangerous cure"},
            ]})
        _write(tmp_path, ug.PUBLISH_BLOCKLIST, {
            "blocked": [{"index": 1, "title": "Dangerous cure", "axes": {"reuse": {"score": 75}}}]})
        allowed, blocked = ug.audit_project(str(tmp_path))
        assert allowed == [0]
        assert len(blocked) == 1
        assert blocked[0]["index"] == 1


class TestYouTubeUploaderReal:
    """YouTube OAuth uploader (Roadmap 2.2) — mocked API, real gate logic."""

    def test_missing_video_raises(self, tmp_path, monkeypatch):
        from scripts import upload_gate as ug
        monkeypatch.setenv("YT_CLIENT_SECRETS_FILE", str(tmp_path / "cs.json"))
        _write_current_publish_evidence(tmp_path, title="T")
        uploader = ug.YouTubeUploader(str(tmp_path), dry_run=False)
        with pytest.raises(FileNotFoundError):
            uploader.upload(str(tmp_path / "nope.mp4"), "T", "C", [], index=0)

    def test_missing_credentials_clear_error(self, tmp_path, monkeypatch):
        from scripts import upload_gate as ug
        monkeypatch.delenv("YT_CLIENT_SECRETS_FILE", raising=False)
        video = tmp_path / "clip.mp4"
        video.write_bytes(b"x")
        _write_current_publish_evidence(tmp_path, title="T")
        uploader = ug.YouTubeUploader(str(tmp_path), dry_run=False)
        with pytest.raises(RuntimeError, match="OAuth credentials"):
            uploader.upload(str(video), "T", "C", ["shorts"], index=0)

    def test_upload_builds_request_and_returns_id(self, tmp_path, monkeypatch):
        import sys as _sys

        from scripts import upload_gate as ug

        video = tmp_path / "clip.mp4"
        video.write_bytes(b"fake video")
        _write_current_publish_evidence(tmp_path, title="My Title")

        captured = {}

        class FakeCreds:
            valid = True

        class FakeMedia:
            def __init__(self, path, chunksize, resumable):
                captured["media_path"] = path
                captured["chunksize"] = chunksize

        class FakeRequest:
            def __init__(self, body, media_body):
                captured["body"] = body
                captured["media"] = media_body

            def next_chunk(self):
                captured["called"] = True
                return None, {"id": "VID123", "status": "uploaded"}

        class FakeVideos:
            def insert(self, part, body, media_body):
                captured["part"] = part
                return FakeRequest(body, media_body)

        class FakeService:
            def __init__(self, *_a, **_k):
                pass

            def videos(self):
                return FakeVideos()

        # fake the google libs that _do_upload imports lazily
        fake_discovery = type(_sys)("googleapiclient.discovery")
        fake_discovery.build = lambda *a, **k: FakeService(*a, **k)
        fake_http = type(_sys)("googleapiclient.http")
        fake_http.MediaFileUpload = FakeMedia
        _sys.modules["googleapiclient.discovery"] = fake_discovery
        _sys.modules["googleapiclient.http"] = fake_http

        monkeypatch.setenv("YT_PRIVACY", "unlisted")
        uploader = ug.YouTubeUploader(str(tmp_path), dry_run=False)
        monkeypatch.setattr(uploader, "_load_or_create_token", lambda: FakeCreds())
        result = uploader.upload(str(video), "My Title", "My caption",
                                 ["#shorts", "funny"], index=0)
        assert result["video_id"] == "VID123"
        assert result["status"] == "uploaded"
        assert captured["body"]["snippet"]["title"] == "My Title"
        assert captured["body"]["snippet"]["tags"] == ["shorts", "funny"]
        assert "funny" in captured["body"]["snippet"]["description"]
        assert captured["body"]["status"]["privacyStatus"] == "unlisted"
        assert captured["called"] is True

    def test_scheduled_upload_requires_private_and_sets_publish_at(self, tmp_path, monkeypatch):
        import sys as _sys
        from datetime import datetime, timedelta, timezone

        video = tmp_path / "clip.mp4"
        video.write_bytes(b"fake video")
        _write_current_publish_evidence(tmp_path, title="Scheduled")

        captured = {}

        class FakeCreds:
            valid = True

        class FakeMedia:
            def __init__(self, path, chunksize, resumable):
                captured["media_path"] = path

        class FakeRequest:
            def next_chunk(self):
                return None, {"id": "SCHEDULED123"}

        class FakeVideos:
            def insert(self, part, body, media_body):
                captured["body"] = body
                return FakeRequest()

        class FakeService:
            def videos(self):
                return FakeVideos()

        fake_discovery = type(_sys)("googleapiclient.discovery")
        fake_discovery.build = lambda *a, **k: FakeService()
        fake_http = type(_sys)("googleapiclient.http")
        fake_http.MediaFileUpload = FakeMedia
        monkeypatch.setitem(_sys.modules, "googleapiclient.discovery", fake_discovery)
        monkeypatch.setitem(_sys.modules, "googleapiclient.http", fake_http)

        future = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
        uploader = ug.YouTubeUploader(str(tmp_path), dry_run=False)
        monkeypatch.setattr(uploader, "_load_or_create_token", lambda: FakeCreds())
        result = uploader.upload(str(video), "Scheduled", "Caption", ["#shorts"],
                                 index=0, privacy_status="private", publish_at=future)
        assert result["status"] == "scheduled"
        assert result["video_id"] == "SCHEDULED123"
        assert captured["body"]["status"]["privacyStatus"] == "private"
        assert captured["body"]["status"]["publishAt"].endswith("Z")

        with pytest.raises(ValueError, match="private"):
            uploader.upload(str(video), "Scheduled", "Caption", [], index=0,
                            privacy_status="public", publish_at=future)
