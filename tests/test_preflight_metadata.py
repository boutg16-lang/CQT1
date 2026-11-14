# -*- coding: utf-8 -*-
"""Windows recovery: half-written venv metadata and optional integrations.

Pins the fix for "Failed to read metadata ... charset_normalizer-3.5.2.dist-info"
and the rule that a broken optional integration (MediaPipe/Gemini) warns
instead of blocking the WebUI.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import preflight


def _dist_info(root, name, metadata=None):
    path = root / name
    path.mkdir()
    if metadata is not None:
        (path / "METADATA").write_text(metadata, encoding="utf-8")
    return path


def test_repair_removes_only_incomplete_dist_info(tmp_path):
    site = tmp_path / "site-packages"
    site.mkdir()
    good = _dist_info(site, "good_pkg-1.0.dist-info", "Name: good_pkg\nVersion: 1.0\n")
    missing = _dist_info(site, "charset_normalizer-3.5.2.dist-info")
    empty = _dist_info(site, "empty_pkg-2.0.dist-info", "")
    (site / "not-a-dir.dist-info").write_text("x", encoding="utf-8")

    removed, failed = preflight.repair_incomplete_dist_info(str(site))

    assert failed == []
    assert set(removed) == {"charset_normalizer-3.5.2.dist-info",
                            "empty_pkg-2.0.dist-info"}
    assert good.is_dir()
    assert not missing.exists()
    assert not empty.exists()
    assert (site / "not-a-dir.dist-info").is_file()


def test_repair_dry_run_changes_nothing(tmp_path):
    site = tmp_path / "site-packages"
    site.mkdir()
    broken = _dist_info(site, "broken-1.0.dist-info")
    removed, failed = preflight.repair_incomplete_dist_info(str(site), dry_run=True)
    assert removed == ["broken-1.0.dist-info"]
    assert failed == []
    assert broken.is_dir()


def test_repair_is_safe_on_missing_paths(tmp_path):
    removed, failed = preflight.repair_incomplete_dist_info(
        str(tmp_path / "does-not-exist"))
    assert (removed, failed) == ([], [])


def test_optional_integrations_warn_instead_of_blocking(monkeypatch):
    monkeypatch.setattr(preflight, "_module_importable", lambda module: False)
    monkeypatch.setattr(preflight, "_dist_installed", lambda dist: True)
    monkeypatch.setattr(preflight, "_import_failure_detail", lambda module: "boom")

    for dist in ("mediapipe", "google-genai", "insightface"):
        result = preflight.check_dependency(dist, dist, critical=True)
        assert result["status"] == preflight.WARN, dist
        assert "optional" in result["detail"]

    result = preflight.check_dependency("requests", "requests", critical=True)
    assert result["status"] == preflight.FAIL
    assert "optional" not in result["detail"]


def test_required_core_packages_stay_critical(monkeypatch):
    monkeypatch.setattr(preflight, "_module_importable", lambda module: False)
    monkeypatch.setattr(preflight, "_dist_installed", lambda dist: True)
    monkeypatch.setattr(preflight, "_import_failure_detail", lambda module: "boom")
    for dist in ("gradio", "fastapi", "uvicorn", "websockets", "yt-dlp"):
        result = preflight.check_dependency(dist, dist, critical=True)
        assert result["status"] == preflight.FAIL, dist


def test_check_venv_metadata_detects_and_repairs(tmp_path, monkeypatch):
    site = tmp_path / "site-packages"
    site.mkdir()
    _dist_info(site, "charset_normalizer-3.5.2.dist-info")
    monkeypatch.setattr(preflight, "_site_packages_dirs", lambda: [str(site)])
    check = preflight.check_venv_metadata()
    assert check["status"] == preflight.FAIL
    assert "charset_normalizer" in check["detail"]

    removed, failed = preflight.repair_incomplete_dist_info()
    assert removed == ["charset_normalizer-3.5.2.dist-info"]
    assert failed == []
    assert preflight.check_venv_metadata()["status"] == preflight.OK


def test_pyannote_vad_is_checked_when_whisperx_is_installed(monkeypatch):
    monkeypatch.setattr(preflight, "_dist_installed", lambda dist: True)

    def broken(module):
        if module == "asteroid_filterbanks":
            raise ModuleNotFoundError("No module named 'asteroid_filterbanks'")
        return object()

    result = preflight.check_pyannote_vad(import_module=broken)
    assert result["status"] == preflight.WARN
    assert "asteroid-filterbanks" in result["detail"]

    result = preflight.check_pyannote_vad(import_module=lambda module: object())
    assert result["status"] == preflight.OK


def test_pyannote_vad_is_skipped_without_whisperx(monkeypatch):
    monkeypatch.setattr(preflight, "_dist_installed", lambda dist: False)
    result = preflight.check_pyannote_vad(
        import_module=lambda module: (_ for _ in ()).throw(AssertionError()))
    assert result["status"] == preflight.OK
    assert "not installed" in result["detail"]
