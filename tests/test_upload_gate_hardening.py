# -*- coding: utf-8 -*-
"""Regression tests for the four upload-gate hardening defects.

Covers:
  1. ``check_clip`` must fail CLOSED when the content guard itself raises.
  2. A ``censor``-status report entry must not bypass a context-level
     ``semantic.action == "review"`` manual-review verdict.
  3. TikTok / Instagram uploaders must accept the same optional keyword set as
     the YouTube uploader (the generic CLI / WebUI construction sites).
  4. The public-privacy guard must evaluate the value the uploader will
     actually use (``YT_PRIVACY`` included).

No network is touched.
"""

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import content_guard  # noqa: E402
from scripts import upload_gate as ug  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _write(project, name, data):
    with open(os.path.join(str(project), name), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _write_publish_ready_evidence(project):
    """A clip that passes every non-semantic gate so only the tested rule bites."""
    clip = {"index": 0, "title": "Short clip", "start_time": 10.0, "end_time": 20.0}
    segment = {
        **clip,
        "selection_score": 88,
        "quality_status": "verified",
        "completion_status": "complete",
        "selection_readiness": "publish_ready",
        "final_validation": {"ok": True, "errors": []},
        "title_validation": {"status": "verified"},
        "title_confidence": 88,
        "recommended_title": "Short clip",
    }
    _write(project, ug.VIRAL_SEGMENTS_FILE, {"segments": [segment]})
    _write(project, ug.SAFETY_REPORT, {
        "mode": "censor",
        "segments": [{**clip, "status": "censor"}],
    })


def _write_verified_censor_evidence(project):
    _write(project, ug.CENSOR_MAP, {
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


# ---------------------------------------------------------------------------
# 1. content_guard failure must fail closed
# ---------------------------------------------------------------------------

class TestContentGuardUnavailableFailsClosed:
    def test_guard_exception_blocks_the_upload(self, tmp_path, monkeypatch):
        def boom(*_args, **_kwargs):
            raise RuntimeError("registry is locked by another process")

        monkeypatch.setattr(content_guard, "assess_clip", boom)
        verdict = ug.check_clip(
            str(tmp_path), 0, "A clean title", "A clean caption", ["shorts"])

        assert verdict["allowed"] is False
        reason = next(
            (r for r in verdict["reasons"] if r["source"] == "content_guard_unavailable"),
            None,
        )
        assert reason is not None, verdict["reasons"]
        assert reason["severity"] == "high"
        assert "registry is locked by another process" in reason["detail"]
        assert "registry is locked by another process" in verdict["content_guard"]["evidence"]["error"]

    def test_gate_upload_refuses_when_guard_raises(self, tmp_path, monkeypatch):
        def boom(*_args, **_kwargs):
            raise OSError("registry file disappeared")

        monkeypatch.setattr(content_guard, "assess_clip", boom)
        with pytest.raises(ug.UploadGateError):
            ug.gate_upload(str(tmp_path), 0, "A clean title", "A clean caption", ["shorts"])

    def test_guard_success_unchanged(self, tmp_path, monkeypatch):
        monkeypatch.setattr(content_guard, "assess_clip", lambda *a, **k: {
            "allowed": True, "reasons": [], "evidence": {},
        })
        verdict = ug.check_clip(
            str(tmp_path), 0, "A clean title", "A clean caption", ["shorts"])
        assert verdict["allowed"] is True


# ---------------------------------------------------------------------------
# 2. censor status must not bypass a semantic manual-review verdict
# ---------------------------------------------------------------------------

class TestCensorDoesNotBypassSemanticReview:
    def test_censor_with_semantic_review_is_refused(self, tmp_path):
        _write_publish_ready_evidence(tmp_path)
        _write_verified_censor_evidence(tmp_path)
        safety = {
            "mode": "censor",
            "segments": [{
                "index": 0, "title": "Short clip",
                "start_time": 10.0, "end_time": 20.0,
                "status": "censor",
                "semantic": {"action": "review", "explanation": "contextual policy risk"},
            }],
        }
        _write(tmp_path, ug.SAFETY_REPORT, safety)

        verdict = ug.check_clip(str(tmp_path), 0, "Short clip", "", [])

        assert verdict["allowed"] is False
        assert any(r["source"] == "semantic_safety" for r in verdict["reasons"])
        # The block comes from the semantic verdict, not missing censor evidence.
        assert not any(r["source"] == "censor_verification" for r in verdict["reasons"])

    def test_censor_without_semantic_review_still_publishes(self, tmp_path):
        _write_publish_ready_evidence(tmp_path)
        _write_verified_censor_evidence(tmp_path)

        verdict = ug.check_clip(str(tmp_path), 0, "Short clip", "", [])

        assert verdict["allowed"] is True, verdict["reasons"]


# ---------------------------------------------------------------------------
# 3. TikTok / Instagram uploaders accept the generic keyword set
# ---------------------------------------------------------------------------

class TestSocialUploaderConstructors:
    @pytest.mark.parametrize("uploader_cls", [ug.TikTokUploader, ug.InstagramUploader])
    def test_accepts_generic_uploader_kwargs(self, tmp_path, uploader_cls):
        uploader = uploader_cls(
            str(tmp_path), dry_run=True, extra_rules_path=None, video_url=None,
            music_gate="block", client_secrets_path="/tmp/secrets.json",
            token_path="/tmp/token.json", privacy_status="private",
            publish_at=None, oauth_full_access=True,
        )
        assert uploader.music_gate == "block"
        assert uploader.client_secrets_path == "/tmp/secrets.json"
        assert uploader.token_path == "/tmp/token.json"
        assert uploader.privacy_status == "private"
        assert uploader.oauth_full_access is True

    @pytest.mark.parametrize("uploader_cls", [ug.TikTokUploader, ug.InstagramUploader])
    def test_positional_video_url_still_works(self, tmp_path, uploader_cls):
        uploader = uploader_cls(
            str(tmp_path), True, None, "https://example.com/clip.mp4")
        assert uploader.video_url == "https://example.com/clip.mp4"
        assert uploader.dry_run is True

    @pytest.mark.parametrize("platform", ["tiktok", "instagram"])
    def test_cli_upload_reaches_gate_without_typeerror(self, tmp_path, platform):
        proc = subprocess.run(
            [sys.executable, "-m", "scripts.upload_gate",
             "--project", str(tmp_path), "--upload", platform,
             "--title", "A clean title", "--caption", "A clean caption"],
            cwd=REPO_ROOT, capture_output=True, text=True,
        )
        combined = proc.stdout + proc.stderr
        assert "TypeError" not in combined, combined
        assert "unexpected keyword argument" not in combined, combined
        assert proc.returncode == 0, combined

    @pytest.mark.parametrize("platform", ["tiktok", "instagram"])
    def test_cli_auth_constructs_without_typeerror(self, tmp_path, platform):
        # Reaching the OAuth setup path (a clean RuntimeError / success) proves
        # construction with the full generic kwarg set no longer raises TypeError.
        proc = subprocess.run(
            [sys.executable, "-m", "scripts.upload_gate",
             "--project", str(tmp_path), "--auth", platform,
             "--music-gate", "warn"],
            cwd=REPO_ROOT, capture_output=True, text=True,
        )
        combined = proc.stdout + proc.stderr
        assert "TypeError" not in combined, combined
        assert "unexpected keyword argument" not in combined, combined


# ---------------------------------------------------------------------------
# 4. privacy resolution (YT_PRIVACY must be visible to every guard)
# ---------------------------------------------------------------------------

class TestEffectivePrivacy:
    def test_defaults_to_private(self, monkeypatch):
        monkeypatch.delenv("YT_PRIVACY", raising=False)
        assert ug.effective_privacy(None) == "private"

    def test_explicit_argument_wins_over_env(self, monkeypatch):
        monkeypatch.setenv("YT_PRIVACY", "public")
        assert ug.effective_privacy("unlisted") == "unlisted"

    def test_env_opt_in_is_visible(self, monkeypatch):
        monkeypatch.setenv("YT_PRIVACY", "public")
        assert ug.effective_privacy(None) == "public"

    def test_invalid_value_raises(self, monkeypatch):
        monkeypatch.delenv("YT_PRIVACY", raising=False)
        with pytest.raises(ValueError):
            ug.effective_privacy("secret")
        monkeypatch.setenv("YT_PRIVACY", "bogus")
        with pytest.raises(ValueError):
            ug.effective_privacy(None)

    def test_scheduled_guard_sees_env_privacy_and_refuses(self, tmp_path, monkeypatch):
        monkeypatch.setenv("YT_PRIVACY", "public")
        video = tmp_path / "clip.mp4"
        video.write_bytes(b"fake video")
        uploader = ug.YouTubeUploader(str(tmp_path), dry_run=False)
        future = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
        with pytest.raises(ValueError, match="private"):
            uploader.upload(str(video), "T", "C", [], index=0, publish_at=future)

    def test_upload_body_uses_resolved_env_privacy(self, tmp_path, monkeypatch):
        video = tmp_path / "clip.mp4"
        video.write_bytes(b"fake video")
        _write_publish_ready_evidence(tmp_path)
        # Make the clip clean for the live gate.
        _write(tmp_path, ug.SAFETY_REPORT, {
            "mode": "block",
            "segments": [{
                "index": 0, "title": "Short clip",
                "start_time": 10.0, "end_time": 20.0, "status": "safe",
            }],
        })

        captured = {}

        class FakeCreds:
            valid = True

        class FakeMedia:
            def __init__(self, path, chunksize, resumable):
                pass

        class FakeRequest:
            def next_chunk(self):
                return None, {"id": "VID1"}

        class FakeVideos:
            def insert(self, part, body, media_body):
                captured["body"] = body
                return FakeRequest()

        class FakeService:
            def videos(self):
                return FakeVideos()

        fake_discovery = type(sys)("googleapiclient.discovery")
        fake_discovery.build = lambda *a, **k: FakeService()
        fake_http = type(sys)("googleapiclient.http")
        fake_http.MediaFileUpload = FakeMedia
        monkeypatch.setitem(sys.modules, "googleapiclient.discovery", fake_discovery)
        monkeypatch.setitem(sys.modules, "googleapiclient.http", fake_http)
        monkeypatch.setenv("YT_PRIVACY", "unlisted")

        uploader = ug.YouTubeUploader(str(tmp_path), dry_run=False)
        uploader.require_preflight_reports = False
        uploader.require_quality_gate = False
        monkeypatch.setattr(uploader, "_load_or_create_token", lambda: FakeCreds())
        result = uploader.upload(str(video), "Short clip", "Caption", [], index=0)

        assert result["video_id"] == "VID1"
        assert captured["body"]["status"]["privacyStatus"] == "unlisted"
