"""Tests for the pre-flight check & auto-repair module (scripts/preflight.py)."""

import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import preflight


# --------------------------------------------------------------------------
# Unit checks
# --------------------------------------------------------------------------
def test_collect_checks_structure():
    checks, critical, warn = preflight.collect_checks()
    assert len(checks) > 5
    assert any(c["name"] == "Safety blocklist freshness" for c in checks)
    for c in checks:
        assert set(c.keys()) == {"name", "status", "detail"}
        assert c["status"] in (preflight.OK, preflight.WARN, preflight.FAIL)
    assert isinstance(critical, list)
    assert isinstance(warn, list)


def test_check_python_passes():
    assert preflight.check_python()["status"] in (preflight.OK, preflight.WARN)


def test_check_binary_found(monkeypatch):
    monkeypatch.setattr(preflight.shutil, "which", lambda name: f"/usr/bin/{name}")
    r = preflight.check_binary("ffmpeg")
    assert r["status"] == preflight.OK
    assert "ffmpeg" in r["detail"]


def test_check_binary_missing_critical(monkeypatch):
    monkeypatch.setattr(preflight.shutil, "which", lambda name: None)
    assert preflight.check_binary("ffmpeg", critical=True)["status"] == preflight.FAIL
    assert preflight.check_binary("fpcalc", critical=False)["status"] == preflight.WARN


def test_check_bundled_binary_skipped_when_not_frozen():
    assert preflight.check_bundled_binary("ffmpeg") is None


def test_check_bundled_binary_found_when_frozen(monkeypatch, tmp_path):
    monkeypatch.setattr(preflight.sys, "frozen", True, raising=False)
    fake = tmp_path / "ffmpeg"
    fake.write_text("x")
    monkeypatch.setattr(preflight.sys, "_MEIPASS", str(tmp_path), raising=False)
    r = preflight.check_bundled_binary("ffmpeg")
    assert r is not None
    assert r["status"] == preflight.OK


def test_check_dependency_present():
    assert preflight.check_dependency("json", "json", True)["status"] == preflight.OK

def test_opentelemetry_distribution_uses_public_import_name():
    assert preflight._import_name("opentelemetry-api") == "opentelemetry"


def test_check_dependency_missing(monkeypatch):
    monkeypatch.setattr(preflight, "_module_importable", lambda m: False)
    monkeypatch.setattr(preflight, "_dist_installed", lambda d: False)
    assert preflight.check_dependency("no_such_pkg_xyz", "no_such_pkg_xyz", True)["status"] == preflight.FAIL
    assert preflight.check_dependency("no_such_pkg_xyz", "no_such_pkg_xyz", False)["status"] == preflight.WARN


def test_check_api_config_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(preflight, "_app_dir", lambda: str(tmp_path))
    r = preflight.check_api_config()
    assert r["status"] == preflight.FAIL
    assert "missing" in r["detail"]


def test_check_api_config_invalid(tmp_path):
    p = tmp_path / "api_config.json"
    p.write_text("{not json")
    monkeypatch_dir_holder = tmp_path
    old = preflight._app_dir
    preflight._app_dir = lambda: str(monkeypatch_dir_holder)
    try:
        r = preflight.check_api_config()
        assert r["status"] == preflight.FAIL
        assert "invalid" in r["detail"]
    finally:
        preflight._app_dir = old


def test_check_api_config_empty_key(tmp_path):
    p = tmp_path / "api_config.json"
    p.write_text(json.dumps({"selected_api": "gemini", "gemini": {"api_key": "", "model": "m"}}))
    old = preflight._app_dir
    preflight._app_dir = lambda: str(tmp_path)
    try:
        r = preflight.check_api_config()
        assert r["status"] == preflight.WARN
        assert "empty" in r["detail"]
    finally:
        preflight._app_dir = old


def test_check_assets_ok_and_missing(tmp_path):
    # ok: use the real repo
    assert preflight.check_assets()["status"] == preflight.OK
    # missing: point APP_ROOT at an empty dir
    old = preflight.APP_ROOT
    preflight.APP_ROOT = str(tmp_path)
    try:
        r = preflight.check_assets()
        assert r["status"] == preflight.FAIL
    finally:
        preflight.APP_ROOT = old


def _write_safety_pack(path, version, updated, reviewed_at=None):
    pack = {
        "version": version,
        "updated": updated,
        "terms": [{"term": "test-term", "lang": "en", "severity": "high"}],
    }
    if reviewed_at is not None:
        pack["reviewed_at"] = reviewed_at
    path.write_text(json.dumps(pack), encoding="utf-8")


def test_safety_blocklist_freshness_warns_after_review_interval(tmp_path):
    _write_safety_pack(tmp_path / "safety_blocklist.json", 6, "2026-09-27")

    result = preflight.check_safety_blocklist_freshness(
        str(tmp_path), today=datetime.date(2026, 10, 12))

    assert result["status"] == preflight.WARN
    assert "15 days ago" in result["detail"]
    assert "14-day review interval" in result["detail"]


def test_safety_blocklist_freshness_uses_review_date_without_rewriting_content_date(tmp_path):
    _write_safety_pack(
        tmp_path / "safety_blocklist.json", 7, "2026-09-27",
        reviewed_at="2026-10-25")

    result = preflight.check_safety_blocklist_freshness(
        str(tmp_path), today=datetime.date(2026, 10, 25))

    assert result["status"] == preflight.OK
    assert "last reviewed 2026-10-25" in result["detail"]
    assert "content last updated 2026-09-27" in result["detail"]


def test_safety_blocklist_freshness_does_not_hide_newer_unreviewed_version(tmp_path):
    _write_safety_pack(
        tmp_path / "safety_blocklist.json", 7, "2026-09-27",
        reviewed_at="2026-10-25")
    _write_safety_pack(
        tmp_path / "safety_blocklist_cache.json", 8, "2026-10-01")

    result = preflight.check_safety_blocklist_freshness(
        str(tmp_path), today=datetime.date(2026, 10, 25))

    assert result["status"] == preflight.WARN
    assert "v8 from downloaded cache" in result["detail"]
    assert "older than the 14-day review interval" in result["detail"]


def test_safety_blocklist_freshness_uses_review_date_without_changing_content_date(tmp_path):
    _write_safety_pack(
        tmp_path / "safety_blocklist.json", 7, "2026-09-27",
        reviewed_at="2026-10-25")

    result = preflight.check_safety_blocklist_freshness(
        str(tmp_path), today=datetime.date(2026, 10, 25))

    assert result["status"] == preflight.OK
    assert "last reviewed 2026-10-25 (0 days ago" in result["detail"]
    assert "content last updated 2026-09-27" in result["detail"]


def test_safety_blocklist_freshness_prefers_newer_version_over_reviewed_older_pack(tmp_path):
    _write_safety_pack(
        tmp_path / "safety_blocklist.json", 7, "2026-09-27",
        reviewed_at="2026-10-25")
    _write_safety_pack(
        tmp_path / "safety_blocklist_cache.json", 8, "2026-10-01")

    result = preflight.check_safety_blocklist_freshness(
        str(tmp_path), today=datetime.date(2026, 10, 25))

    assert result["status"] == preflight.WARN
    assert "downloaded cache" in result["detail"]
    assert "v8" in result["detail"]
    assert "24 days ago" in result["detail"]


def test_safety_blocklist_freshness_uses_newer_cache_at_boundary(tmp_path):
    _write_safety_pack(tmp_path / "safety_blocklist.json", 6, "2026-09-27")
    _write_safety_pack(tmp_path / "safety_blocklist_cache.json", 7, "2026-10-10")

    result = preflight.check_safety_blocklist_freshness(
        str(tmp_path), today=datetime.date(2026, 10, 24))

    assert result["status"] == preflight.OK
    assert "downloaded cache" in result["detail"]
    assert "v7" in result["detail"]


def test_safety_blocklist_freshness_warns_when_date_is_missing(tmp_path):
    _write_safety_pack(tmp_path / "safety_blocklist.json", 6, "")

    result = preflight.check_safety_blocklist_freshness(
        str(tmp_path), today=datetime.date(2026, 10, 24))

    assert result["status"] == preflight.WARN
    assert "could not verify" in result["detail"]


def test_safety_blocklist_freshness_warns_for_future_date(tmp_path):
    _write_safety_pack(tmp_path / "safety_blocklist.json", 6, "2026-10-25")

    result = preflight.check_safety_blocklist_freshness(
        str(tmp_path), today=datetime.date(2026, 10, 24))

    assert result["status"] == preflight.WARN
    assert "future" in result["detail"]


def test_check_models_dir(tmp_path):
    (tmp_path / "models").mkdir()
    (tmp_path / "models" / "llama.gguf").write_text("x")
    old = preflight.APP_ROOT
    preflight.APP_ROOT = str(tmp_path)
    try:
        assert preflight.check_models_dir()["status"] == preflight.OK
    finally:
        preflight.APP_ROOT = old


def test_check_pin_numpy_ok(monkeypatch):
    # Deterministic: with an installed numpy that satisfies the pin, the
    # check must pass regardless of the ambient environment (the old test
    # depended on the dev box having numpy<2, flaking on numpy 2.x boxes).
    monkeypatch.setattr(preflight._pkg_meta, "version", lambda d: "1.26.4")
    assert preflight.check_pin("numpy", "numpy<2") is None


def test_check_pin_numpy_violation(monkeypatch):
    monkeypatch.setattr(preflight._pkg_meta, "version", lambda d: "2.1.0")
    r = preflight.check_pin("numpy", "numpy<2")
    assert r is not None and r["status"] == preflight.FAIL


def test_check_pin_setuptools_accepts_patched_release(monkeypatch):
    class FakeSpec:
        pass

    assert any(
        dist == "setuptools" and rule == "setuptools>=84.1.0,<85"
        for dist, rule, _note in preflight.PINNED_RULES
    )
    monkeypatch.setattr(preflight._iu, "find_spec", lambda name: FakeSpec())
    monkeypatch.setattr(preflight._pkg_meta, "version", lambda d: "84.2.1")
    assert preflight.check_pin("setuptools", "setuptools>=84.1.0,<85") is None


def test_check_pin_setuptools_rejects_below_security_floor(monkeypatch):
    class FakeSpec:
        pass

    monkeypatch.setattr(preflight._iu, "find_spec", lambda name: FakeSpec())
    monkeypatch.setattr(preflight._pkg_meta, "version", lambda d: "84.0.0")
    result = preflight.check_pin("setuptools", "setuptools>=84.1.0,<85")
    assert result is not None and result["status"] == preflight.FAIL


def test_check_pin_setuptools_rejects_outside_supported_range(monkeypatch):
    class FakeSpec:
        pass

    monkeypatch.setattr(preflight._iu, "find_spec", lambda name: FakeSpec())
    monkeypatch.setattr(preflight._pkg_meta, "version", lambda d: "85.0.0")
    result = preflight.check_pin("setuptools", "setuptools>=84.1.0,<85")
    assert result is not None and result["status"] == preflight.FAIL


def test_check_pin_ctranslate2_rejects_outside_whisperx_range(monkeypatch):
    class FakeSpec:
        pass

    assert any(
        dist == "ctranslate2" and rule == "ctranslate2>=4.4.0,<4.5.0"
        for dist, rule, _note in preflight.PINNED_RULES
    )
    monkeypatch.setattr(preflight._iu, "find_spec", lambda name: FakeSpec())
    monkeypatch.setattr(preflight._pkg_meta, "version", lambda dist: "4.5.0")

    result = preflight.check_pin("ctranslate2", "ctranslate2>=4.4.0,<4.5.0")

    assert result is not None and result["status"] == preflight.FAIL


def test_fix_ctranslate2_pin_preserves_existing_dependencies(monkeypatch):
    class FakeSpec:
        pass

    class Result:
        returncode = 0

    calls = []
    monkeypatch.setattr(preflight._iu, "find_spec", lambda name: FakeSpec())
    monkeypatch.setattr(preflight, "_version", lambda dist: "4.5.0")
    monkeypatch.setattr(preflight, "_is_frozen", lambda: False)
    monkeypatch.setattr(preflight, "_run_pip", lambda args: calls.append(args) or Result())

    ok, detail = preflight.fix_ctranslate2_pin()

    assert ok
    assert "--no-deps" in calls[0]
    assert "ctranslate2>=4.4.0,<4.5.0" in calls[0]
    assert "torch" not in calls[0]
    assert "CTranslate2" in detail


def test_fix_setuptools_pin_installs_patched_release(monkeypatch):
    class FakeSpec:
        pass

    class Result:
        returncode = 0

    calls = []
    monkeypatch.setattr(preflight._iu, "find_spec", lambda name: FakeSpec())
    monkeypatch.setattr(preflight, "_version", lambda dist: "81.0.0")
    monkeypatch.setattr(preflight, "_is_frozen", lambda: False)
    monkeypatch.setattr(preflight, "_run_pip", lambda args: calls.append(args) or Result())

    ok, detail = preflight.fix_setuptools_pin()

    assert ok
    assert "--upgrade" in calls[0]
    assert "setuptools>=84.1.0,<85" in calls[0]
    assert "patched setuptools" in detail


def test_check_pin_ctranslate2_accepts_whisperx_331_range(monkeypatch):
    class FakeSpec:
        pass

    assert any(
        dist == "ctranslate2" and rule == "ctranslate2>=4.4.0,<4.5.0"
        for dist, rule, _note in preflight.PINNED_RULES
    )
    monkeypatch.setattr(preflight._iu, "find_spec", lambda name: FakeSpec())
    monkeypatch.setattr(preflight._pkg_meta, "version", lambda dist: "4.4.0")
    assert preflight.check_pin("ctranslate2", "ctranslate2>=4.4.0,<4.5.0") is None


def test_check_pin_transformers_supported_range(monkeypatch):
    class FakeSpec:
        pass

    monkeypatch.setattr(preflight._iu, "find_spec", lambda name: FakeSpec())
    monkeypatch.setattr(preflight._pkg_meta, "version", lambda d: "5.14.1")
    assert preflight.check_pin("transformers", "transformers>=5.14.1,<5.16") is None


def test_check_pin_transformers_outside_supported_range(monkeypatch):
    class FakeSpec:
        pass

    monkeypatch.setattr(preflight._iu, "find_spec", lambda name: FakeSpec())
    monkeypatch.setattr(preflight._pkg_meta, "version", lambda d: "4.57.6")
    result = preflight.check_pin("transformers", "transformers>=5.14.1,<5.16")
    assert result is not None and result["status"] == preflight.FAIL


def test_fix_transformers_pin_repairs_related_huggingface_packages(monkeypatch):
    class FakeSpec:
        pass

    class Result:
        returncode = 0

    calls = []
    monkeypatch.setattr(preflight._iu, "find_spec", lambda name: FakeSpec())
    monkeypatch.setattr(preflight, "_version", lambda dist: "4.57.6")
    monkeypatch.setattr(preflight, "_is_frozen", lambda: False)
    monkeypatch.setattr(preflight, "_run_pip", lambda args: calls.append(args) or Result())

    ok, _ = preflight.fix_transformers_pin()

    assert ok
    assert "transformers>=5.14.1,<5.16" in calls[0]
    assert "huggingface-hub>=1.16.0,<2.0" in calls[0]
    assert "tokenizers>=0.22.2,<0.23.1" in calls[0]
    assert "numpy<2" in calls[0]


def test_check_pin_hub_accepts_compatible_one_x_range(monkeypatch):
    class FakeSpec:
        pass

    monkeypatch.setattr(preflight._iu, "find_spec", lambda name: FakeSpec())
    monkeypatch.setattr(preflight._pkg_meta, "version", lambda d: "1.27.0")
    assert preflight.check_pin("huggingface-hub", "huggingface-hub>=1.16.0,<2.0") is None


def test_check_pin_hub_rejects_version_below_gradio_minimum(monkeypatch):
    class FakeSpec:
        pass

    monkeypatch.setattr(preflight._iu, "find_spec", lambda name: FakeSpec())
    monkeypatch.setattr(preflight._pkg_meta, "version", lambda d: "1.5.0")
    result = preflight.check_pin("huggingface-hub", "huggingface-hub>=1.16.0,<2.0")
    assert result is not None and result["status"] == preflight.FAIL


def test_check_pin_hub_flags_incompatible_zero_x_range(monkeypatch):
    class FakeSpec:
        pass

    monkeypatch.setattr(preflight._iu, "find_spec", lambda name: FakeSpec())
    monkeypatch.setattr(preflight._pkg_meta, "version", lambda d: "0.36.2")
    result = preflight.check_pin("huggingface-hub", "huggingface-hub>=1.16.0,<2.0")
    assert result is not None and result["status"] == preflight.FAIL


def test_fix_hub_pin_restores_compatible_transformers_stack(monkeypatch):
    class FakeSpec:
        pass

    class Result:
        returncode = 0

    calls = []
    monkeypatch.setattr(preflight._iu, "find_spec", lambda name: FakeSpec())
    monkeypatch.setattr(preflight, "_version", lambda dist: "0.36.2")
    monkeypatch.setattr(preflight, "_is_frozen", lambda: False)
    monkeypatch.setattr(preflight, "_run_pip", lambda args: calls.append(args) or Result())

    ok, _ = preflight.fix_hub_pin()

    assert ok
    assert "transformers>=5.14.1,<5.16" in calls[0]
    assert "huggingface-hub>=1.16.0,<2.0" in calls[0]
    assert "tokenizers>=0.22.2,<0.23.1" in calls[0]
    assert "numpy<2" in calls[0]


def test_check_pin_tokenizers_ok(monkeypatch):
    # tokenizers below 0.23.1 (0.23.0 was never released; 0.22.2 is the
    # known-good version for transformers<=5.14) must pass regardless of env.
    class FakeSpec:
        pass

    monkeypatch.setattr(preflight._iu, "find_spec", lambda name: FakeSpec())
    monkeypatch.setattr(
        preflight._pkg_meta, "version",
        lambda d: "0.22.2" if d == "tokenizers" else "4.56.0")
    assert preflight.check_pin("tokenizers", "tokenizers<0.23.1") is None


def test_check_pin_tokenizers_violation(monkeypatch):
    # tokenizers 0.23.1 with transformers on the <=5.14 line (hub<1.0 world)
    # breaks the transformers import check -> must be flagged as FAIL.
    class FakeSpec:
        pass

    monkeypatch.setattr(preflight._iu, "find_spec", lambda name: FakeSpec())

    def fake_version(dist):
        return {"tokenizers": "0.23.1", "transformers": "4.56.0"}.get(dist, "?")

    monkeypatch.setattr(preflight._pkg_meta, "version", fake_version)
    r = preflight.check_pin("tokenizers", "tokenizers<0.23.1")
    assert r is not None and r["status"] == preflight.FAIL
    assert "0.23.1" in r["detail"]


def test_check_pin_tokenizers_violation_on_transformers_5(monkeypatch):
    class FakeSpec:
        pass

    monkeypatch.setattr(preflight._iu, "find_spec", lambda name: FakeSpec())

    def fake_version(dist):
        return {"tokenizers": "0.23.1", "transformers": "5.14.1"}.get(dist, "?")

    monkeypatch.setattr(preflight._pkg_meta, "version", fake_version)
    result = preflight.check_pin("tokenizers", "tokenizers<0.23.1")
    assert result is not None and result["status"] == preflight.FAIL


# --------------------------------------------------------------------------
# Repair actions
# --------------------------------------------------------------------------
def test_ensure_dirs_creates(tmp_path):
    old = preflight._app_dir
    preflight._app_dir = lambda: str(tmp_path)
    try:
        results = preflight.ensure_dirs()
        assert all(ok for _, ok, _ in results)
        assert os.path.isdir(tmp_path / "models")
        assert os.path.isdir(tmp_path / "VIRALS")
    finally:
        preflight._app_dir = old


def test_ensure_api_config_creates_template(tmp_path):
    old = preflight._app_dir
    preflight._app_dir = lambda: str(tmp_path)
    try:
        ok, detail = preflight.ensure_api_config()
        assert ok
        p = tmp_path / "api_config.json"
        assert p.exists()
        data = json.loads(p.read_text())
        assert data["selected_api"] == "gemini"
        assert "api_key" in data["gemini"]
    finally:
        preflight._app_dir = old


def test_ensure_api_config_preserves_existing(tmp_path):
    p = tmp_path / "api_config.json"
    p.write_text(json.dumps({"selected_api": "gemini", "gemini": {"api_key": "AIzaSECRET", "model": "m"}}))
    old = preflight._app_dir
    preflight._app_dir = lambda: str(tmp_path)
    try:
        ok, _ = preflight.ensure_api_config()
        assert ok
        data = json.loads(p.read_text())
        assert data["gemini"]["api_key"] == "AIzaSECRET"  # never overwrite a real key
    finally:
        preflight._app_dir = old


def test_install_requirements_dry_run():
    ok, detail = preflight.install_requirements("requirements-dev.txt", dry_run=True)
    assert not ok
    assert "would run" in detail


def test_install_requirements_missing_file(tmp_path, monkeypatch):
    monkeypatch.setattr(preflight, "APP_ROOT", str(tmp_path))
    ok, detail = preflight.install_requirements("does_not_exist.txt")
    assert ok
    assert "empty" in detail


def test_fix_numpy_pin_skipped_when_missing(monkeypatch):
    monkeypatch.setattr(preflight._iu, "find_spec", lambda name: None)
    ok, detail = preflight.fix_numpy_pin()
    assert ok
    assert "not installed" in detail


def test_fix_numpy_pin_dry_run(monkeypatch):
    class FakeSpec:
        pass

    monkeypatch.setattr(preflight._iu, "find_spec", lambda name: FakeSpec())
    monkeypatch.setattr(preflight.sys, "path", [])
    # force version read as 2.x
    import types

    fake_numpy = types.ModuleType("numpy")
    fake_numpy.__version__ = "2.1.0"
    monkeypatch.setitem(sys.modules, "numpy", fake_numpy)
    ok, detail = preflight.fix_numpy_pin(dry_run=True)
    assert not ok
    assert "would downgrade" in detail


# --------------------------------------------------------------------------
# Orchestration exit codes
# --------------------------------------------------------------------------
def test_run_preflight_ready(monkeypatch):
    fake = [
        {"name": "Python", "status": preflight.OK, "detail": "3.11"},
        {"name": "ffmpeg", "status": preflight.OK, "detail": "/usr/bin/ffmpeg"},
        {"name": "ffprobe", "status": preflight.OK, "detail": "/usr/bin/ffprobe"},
    ]
    monkeypatch.setattr(preflight, "collect_checks", lambda: (fake, [], []))
    assert preflight.run_preflight(mode="check", quiet=True) == 0


def test_run_preflight_warnings(monkeypatch):
    fake = [
        {"name": "Python", "status": preflight.OK, "detail": "3.11"},
        {"name": "ffmpeg", "status": preflight.WARN, "detail": "x"},
    ]
    monkeypatch.setattr(preflight, "collect_checks", lambda: (fake, [], ["ffmpeg"]))
    assert preflight.run_preflight(mode="check", quiet=True) == 2


def test_run_preflight_critical_blocks(monkeypatch):
    fake = [
        {"name": "Python", "status": preflight.OK, "detail": "3.11"},
        {"name": "ffmpeg", "status": preflight.FAIL, "detail": "missing"},
    ]
    monkeypatch.setattr(preflight, "collect_checks", lambda: (fake, ["ffmpeg"], []))
    assert preflight.run_preflight(mode="check", quiet=True) == 1


def test_run_preflight_autofix_installs_core(monkeypatch, tmp_path):
    """A missing core dep + auto-fix must trigger a pip install and end ready."""
    calls = []

    def fake_install(req_file, dry_run=False):
        calls.append(req_file)
        return True, "installed"

    monkeypatch.setattr(preflight, "install_requirements", fake_install)
    monkeypatch.setattr(preflight, "ensure_dirs", lambda: [("x", True, "ok")])
    monkeypatch.setattr(preflight, "ensure_api_config", lambda: (True, "ok"))
    monkeypatch.setattr(preflight, "fix_numpy_pin", lambda: (True, "ok"))

    def fake_collect():
        if not calls:
            return ([{"name": "gradio", "status": preflight.FAIL, "detail": "missing"}],
                    ["gradio"], [])
        return ([{"name": "gradio", "status": preflight.OK, "detail": "6.0"}], [], [])

    monkeypatch.setattr(preflight, "collect_checks", fake_collect)
    assert preflight.run_preflight(mode="auto-fix", quiet=True) == 0
    assert calls == ["requirements.txt"]


def test_main_off_flag(capsys):
    assert preflight.main(["--off"]) == 0


def test_main_json_output(capsys):
    code = preflight.main(["--check", "--json", "--quiet"])
    out = capsys.readouterr().out
    data = json.loads(out)
    assert "checks" in data
    assert data["exit"] in (0, 1, 2)
    assert code == data["exit"]


def test_run_pip_falls_back_to_uv_when_pip_missing(monkeypatch):
    calls = []
    monkeypatch.setattr(preflight, "_module_importable", lambda name: False)
    monkeypatch.setattr(preflight.shutil, "which", lambda name: "uv.exe" if name == "uv" else None)

    class Result:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return Result()

    monkeypatch.setattr(preflight.subprocess, "run", fake_run)
    result = preflight._run_pip(["--no-cache-dir", "numpy==1.26.4"])
    assert result.returncode == 0
    assert calls[0][:4] == ["uv.exe", "pip", "install", "--python"]
    assert "numpy==1.26.4" in calls[0]


def test_run_preflight_ensure_upload_installs_oauth(monkeypatch):
    calls = []

    def fake_install(req_file, dry_run=False):
        calls.append(req_file)
        return True, "installed upload"

    monkeypatch.setattr(preflight, "install_requirements", fake_install)
    monkeypatch.setattr(preflight, "ensure_dirs", lambda: [])
    monkeypatch.setattr(preflight, "ensure_api_config", lambda: (True, "ok"))
    monkeypatch.setattr(preflight, "fix_numpy_pin", lambda: (True, "ok"))

    def fake_collect():
        if not calls:
            return ([{"name": "google-auth-oauthlib", "status": preflight.FAIL, "detail": "missing"}],
                    ["google-auth-oauthlib"], [])
        return ([{"name": "google-auth-oauthlib", "status": preflight.OK, "detail": "installed"}], [], [])

    monkeypatch.setattr(preflight, "collect_checks", fake_collect)
    assert preflight.run_preflight(mode="auto-fix", quiet=True, ensure_upload=True) == 0
    assert calls == ["requirements-upload.txt"]


def test_run_preflight_ensure_upload_repairs_warning_only(monkeypatch):
    calls = []

    def fake_install(req_file, dry_run=False):
        calls.append(req_file)
        return True, "installed upload"

    monkeypatch.setattr(preflight, "install_requirements", fake_install)
    monkeypatch.setattr(preflight, "ensure_dirs", lambda: [])
    monkeypatch.setattr(preflight, "ensure_api_config", lambda: (True, "ok"))
    monkeypatch.setattr(preflight, "fix_numpy_pin", lambda: (True, "ok"))

    def fake_collect():
        if not calls:
            return ([{"name": "google-auth-oauthlib", "status": preflight.WARN, "detail": "optional - missing"}],
                    [], ["google-auth-oauthlib"])
        return ([{"name": "google-auth-oauthlib", "status": preflight.OK, "detail": "installed"}], [], [])

    monkeypatch.setattr(preflight, "collect_checks", fake_collect)
    assert preflight.run_preflight(mode="auto-fix", quiet=True, ensure_upload=True) == 0
    assert calls == ["requirements-upload.txt"]


def test_check_telegram_config_disabled():
    result = preflight.check_telegram_config({})
    assert result["status"] == preflight.WARN
    assert "disabled" in result["detail"]


def test_check_telegram_config_ready_hides_token():
    token = "123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklm"
    result = preflight.check_telegram_config({
        "VIRALCUTTER_TELEGRAM_ENABLED": "1",
        "VIRALCUTTER_TELEGRAM_BOT_TOKEN": token,
        "VIRALCUTTER_TELEGRAM_CHAT_IDS": "100 200, invalid",
    })
    assert result["status"] == preflight.OK
    assert "2 allowlisted" in result["detail"]
    assert token not in result["detail"]


def test_check_telegram_config_missing_allowlist_hides_token():
    token = "123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklm"
    result = preflight.check_telegram_config({
        "VIRALCUTTER_TELEGRAM_ENABLED": "true",
        "VIRALCUTTER_TELEGRAM_BOT_TOKEN": token,
        "VIRALCUTTER_TELEGRAM_CHAT_IDS": "../demo abc",
    })
    assert result["status"] == preflight.WARN
    assert "Chat ID" in result["detail"]
    assert token not in result["detail"]


def test_legacy_gemini_import_check_uses_metadata_without_import(monkeypatch):
    imported = []

    def fake_import(module):
        imported.append(module)
        raise AssertionError("legacy Gemini SDK should not be imported during preflight")

    monkeypatch.setattr(preflight.importlib, "import_module", fake_import)
    monkeypatch.setattr(preflight, "_dist_installed", lambda dist: dist == "google-generativeai")
    assert preflight._module_importable("google.generativeai") is True
    assert imported == []
