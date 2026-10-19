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
    assert {"Python", "Project root", "Project drive space", "Torch", "WhisperX", "Faster-whisper fallback", "google-auth-oauthlib"} <= names
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

def test_windows_diagnostics_rejects_huggingface_hub_0_x():
    result = _version_range_check("huggingface-hub", "0.36.2", "1.16.0", "2.0")
    assert result["status"] == "warn"
    assert "1.16.0" in result["detail"]

def test_windows_diagnostics_rejects_invalid_huggingface_version():
    result = _version_range_check("huggingface-hub", "not-a-version", "1.16.0", "2.0")
    assert result["status"] == "warn"
