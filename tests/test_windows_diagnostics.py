import json
from pathlib import Path

from scripts.windows_diagnostics import (
    _telegram_check,
    _version_range_check,
    collect,
    render,
)


def test_windows_diagnostics_is_json_serializable(tmp_path):
    report = collect(tmp_path)
    encoded = json.dumps(report, ensure_ascii=False)
    assert '"checks"' in encoded
    assert report["summary"]["status"] in {"ok", "warn", "fail"}
    assert report["root"] == str(Path(tmp_path).resolve())


def test_windows_diagnostics_contains_runtime_checks(tmp_path):
    report = collect(tmp_path)
    names = {item["name"] for item in report["checks"]}
    assert {"Python", "Project root", "Project drive space", "Torch", "WhisperX", "Faster-whisper fallback", "setuptools", "pkg_resources", "google-auth-oauthlib"} <= names
    text = render(report)
    assert "OUSSAMA Cutter Windows diagnostics" in text
    assert "Summary:" in text


def test_windows_diagnostics_does_not_modify_root(tmp_path):
    before = sorted(path.name for path in tmp_path.iterdir())
    collect(tmp_path)
    after = sorted(path.name for path in tmp_path.iterdir())
    assert before == after


def test_windows_telegram_check_is_optional_and_secret_free():
    token = "123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklm"
    result = _telegram_check({
        "VIRALCUTTER_TELEGRAM_ENABLED": "on",
        "VIRALCUTTER_TELEGRAM_BOT_TOKEN": token,
        "VIRALCUTTER_TELEGRAM_CHAT_IDS": "100, 200",
    })
    assert result["status"] == "ok"
    assert result["critical"] is False
    assert "2 allowlisted" in result["detail"]
    assert token not in result["detail"]


def test_windows_diagnostics_accepts_supported_huggingface_hub():
    result = _version_range_check("huggingface-hub", "1.33.0", "1.16.0", "2.0")
    assert result["status"] == "ok"


def test_windows_diagnostics_rejects_transformers_4_after_hub_only_downgrade():
    result = _version_range_check("Transformers", "4.57.6", "5.14.1", "5.16")
    assert result["status"] == "warn"
    assert "5.14.1" in result["detail"]


def test_windows_diagnostics_flags_whisperx_3_2_outside_locked_stack(monkeypatch):
    import scripts.windows_diagnostics as diagnostics

    versions = {
        "whisperx": "3.2.0",
        "transformers": "5.15.1",
        "huggingface_hub": "1.33.0",
        "tokenizers": "0.22.2",
        "setuptools": "81.0.0",
    }
    monkeypatch.setattr(
        diagnostics,
        "_import_probe",
        lambda module_name, distribution=None: (True, versions.get(module_name, "installed")),
    )

    checks = diagnostics._huggingface_stack_checks()

    whisperx = next(item for item in checks if item["name"] == "WhisperX version")
    assert whisperx["status"] == "warn"
    assert "3.3.1" in whisperx["detail"]

def test_windows_diagnostics_rejects_huggingface_hub_0_x():
    result = _version_range_check("huggingface-hub", "0.36.2", "1.16.0", "2.0")
    assert result["status"] == "warn"
    assert "1.16.0" in result["detail"]

def test_windows_diagnostics_rejects_invalid_huggingface_version():
    result = _version_range_check("huggingface-hub", "not-a-version", "1.16.0", "2.0")
    assert result["status"] == "warn"


def test_windows_diagnostics_flags_setuptools_82_without_pkg_resources():
    result = _version_range_check("setuptools", "82.0.0", "70.0.0", "82.0.0")
    assert result["status"] == "warn"
    assert "82.0.0" in result["detail"]


def test_windows_diagnostics_warns_when_pkg_resources_is_missing(monkeypatch):
    import scripts.windows_diagnostics as diagnostics

    versions = {
        "whisperx": "3.3.1",
        "transformers": "5.15.1",
        "huggingface_hub": "1.33.0",
        "tokenizers": "0.22.2",
        "setuptools": "81.0.0",
    }

    def probe(module_name, distribution=None):
        if module_name == "pkg_resources":
            return False, "ModuleNotFoundError: No module named 'pkg_resources'"
        return True, versions[module_name]

    monkeypatch.setattr(diagnostics, "_import_probe", probe)
    checks = diagnostics._huggingface_stack_checks()
    pkg_resources = next(item for item in checks if item["name"] == "pkg_resources")
    assert pkg_resources["status"] == "warn"
    assert "No module named 'pkg_resources'" in pkg_resources["detail"]
