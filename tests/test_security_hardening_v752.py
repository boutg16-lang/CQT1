# -*- coding: utf-8 -*-
"""v7.52 security hardening — fail-closed safety gates + credential perms.

Locks in three fixes made during the comprehensive audit:

1. ``content_guard.filter_segments`` must FAIL CLOSED when the semantic-safety
   layer cannot be imported. Before this, an import/load error silently set
   ``analyze_text = None`` and the semantic policy check became a no-op.
2. ``upload_gate.check_clip`` must FAIL CLOSED when the publish-metadata
   semantic check raises, instead of ``except Exception: pass``.
3. OAuth tokens written by ``upload_gate._save_token`` must be owner-only
   (0600) from creation, never briefly world-readable.
"""

import json
import os
import stat
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import content_guard, upload_gate


def _project(root, name, segments=None):
    project = root / name
    project.mkdir(parents=True)
    source = root / "source.mp4"
    if not source.exists():
        source.write_bytes(b"source")
    (project / "project_manifest.json").write_text(
        json.dumps({"source": {"type": "local", "path": str(source), "managed": False}}),
        encoding="utf-8")
    (project / "viral_segments.txt").write_text(
        json.dumps({"segments": segments or [{"title": "clip", "start_time": 0, "end_time": 10}]}),
        encoding="utf-8")
    return project


# ---------------------------------------------------------------------------
# 1. content_guard fails closed when the semantic layer is unavailable
# ---------------------------------------------------------------------------

def test_content_guard_fails_closed_when_semantic_layer_unavailable(tmp_path, monkeypatch):
    project = _project(tmp_path / "VIRALS", "p")
    monkeypatch.setattr(content_guard, "_load_semantic_tools",
                        lambda: (None, None, None, "ImportError: boom"))

    kept, report = content_guard.filter_segments(
        str(project), [{"title": "perfectly clean", "start_time": 0, "end_time": 10}])

    assert kept == [], "no candidate may be exported when the policy gate is down"
    assert report["blocked"] == 1
    codes = {reason["code"] for reason in report["blocked_segments"][0]["reasons"]}
    assert "semantic_safety_unavailable" in codes
    # The reason is surfaced as high severity so it cannot be ignored downstream.
    reasons = report["blocked_segments"][0]["reasons"]
    assert any(r["code"] == "semantic_safety_unavailable" and r["severity"] == "high"
               for r in reasons)


def test_content_guard_still_allows_when_semantic_layer_loads(tmp_path, monkeypatch):
    """Guard against an over-eager fail-closed: a healthy layer keeps clips."""
    project = _project(tmp_path / "VIRALS", "p")
    real = content_guard._load_semantic_tools()
    assert real[3] is None, "semantic layer should import cleanly in the test env"
    monkeypatch.setattr(content_guard, "_load_semantic_tools", lambda: real)

    kept, report = content_guard.filter_segments(
        str(project), [{"title": "نصيحة مفيدة", "start_time": 0, "end_time": 10}])

    assert len(kept) == 1
    assert report["blocked"] == 0


# ---------------------------------------------------------------------------
# 2. upload_gate fails closed when the metadata semantic check raises
# ---------------------------------------------------------------------------

def test_upload_gate_metadata_semantic_failure_blocks(tmp_path, monkeypatch):
    from scripts import semantic_safety

    def boom(*_args, **_kwargs):
        raise RuntimeError("semantic engine down")

    monkeypatch.setattr(semantic_safety, "analyze_text", boom)

    verdict = upload_gate.check_clip(
        str(tmp_path), 0, "clean title", "clean caption", ["shorts"])

    assert verdict["allowed"] is False
    assert any(r.get("source") == "semantic_safety" for r in verdict["reasons"])


# ---------------------------------------------------------------------------
# 3. OAuth token files are owner-only
# ---------------------------------------------------------------------------

@pytest.mark.skipif(os.name == "nt", reason="POSIX file modes only")
def test_save_token_is_owner_only(tmp_path, monkeypatch):
    token_path = tmp_path / "secrets" / "yt_token.json"
    monkeypatch.setenv("YT_TOKEN_FILE", str(token_path))

    returned = upload_gate._save_token("youtube", {"access_token": "secret"})

    assert returned == str(token_path)
    mode = stat.S_IMODE(os.stat(returned).st_mode)
    assert mode == 0o600, "token file must not be group/world readable (got %o)" % mode
    assert json.loads(open(returned, encoding="utf-8").read()) == {"access_token": "secret"}
    # No leftover temp file in the store directory.
    assert sorted(p.name for p in token_path.parent.iterdir()) == ["yt_token.json"]


# ---------------------------------------------------------------------------
# 4. .gitignore covers OAuth secrets that could be dropped in the project root
# ---------------------------------------------------------------------------

def test_gitignore_covers_oauth_secret_filenames():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, ".gitignore"), encoding="utf-8") as handle:
        ignore = handle.read()
    for pattern in ("token.json", "client_secrets*.json", "*.pem", ".viralcutter/"):
        assert pattern in ignore, "missing .gitignore protection for %r" % pattern


# ---------------------------------------------------------------------------
# 5. Duplicate-prevention gates fail closed when their engine breaks
# ---------------------------------------------------------------------------

def test_content_guard_perceptual_failure_blocks(tmp_path, monkeypatch):
    from scripts import originality

    root = tmp_path / "VIRALS"
    project = _project(root, "p")
    video = project / "clip.mp4"
    video.write_bytes(b"rendered-clip")
    # A prior publish of the same source gives the perceptual gate rows to
    # compare against (records.insert).
    content_guard.record_publish(str(project), "youtube", str(video), index=0,
                                 result={"status": "uploaded", "video_id": "x"})

    def boom(*_args, **_kwargs):
        raise RuntimeError("fingerprint engine down")

    monkeypatch.setattr(originality, "assess_against_registry", boom)

    verdict = content_guard.assess_clip(
        str(project), 0, title="t", video_path=str(video), platform="youtube",
        perceptual=True)

    assert verdict["allowed"] is False
    assert any(r["code"] == "perceptual_check_unavailable" for r in verdict["reasons"])


def test_content_guard_cross_project_failure_blocks(tmp_path, monkeypatch):
    from scripts import content_ledger

    root = tmp_path / "VIRALS"
    project = _project(root, "p")
    video = project / "clip.mp4"
    video.write_bytes(b"rendered-clip")

    def boom(*_args, **_kwargs):
        raise RuntimeError("ledger unavailable")

    monkeypatch.setattr(content_ledger, "find_visual_matches", boom)

    verdict = content_guard.assess_clip(
        str(project), 0, title="t", video_path=str(video), platform="youtube")

    assert verdict["allowed"] is False
    assert any(r["code"] == "cross_project_check_unavailable" for r in verdict["reasons"])


# ---------------------------------------------------------------------------
# 6. Music copyright gate: silent only when it is not a hard gate
# ---------------------------------------------------------------------------

def test_upload_gate_music_block_mode_failure_blocks(tmp_path, monkeypatch):
    from scripts import music_fingerprint

    def boom(*_args, **_kwargs):
        raise RuntimeError("fpcalc missing")

    monkeypatch.setattr(music_fingerprint, "music_gate_reasons", boom)

    verdict = upload_gate.check_clip(
        str(tmp_path), 0, "clean title", "clean caption", ["shorts"],
        music_gate="block")

    assert verdict["allowed"] is False
    assert any(r.get("source") == "music_fingerprint" for r in verdict["reasons"])


def test_upload_gate_music_warn_mode_failure_does_not_add_block(tmp_path, monkeypatch):
    from scripts import music_fingerprint

    def boom(*_args, **_kwargs):
        raise RuntimeError("fpcalc missing")

    monkeypatch.setattr(music_fingerprint, "music_gate_reasons", boom)

    verdict = upload_gate.check_clip(
        str(tmp_path), 0, "clean title", "clean caption", ["shorts"],
        music_gate="warn")

    assert not any(r.get("source") == "music_fingerprint" for r in verdict["reasons"])
