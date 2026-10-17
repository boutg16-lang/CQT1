import ast
import json
from pathlib import Path

import pytest

from scripts import create_viral_segments, secure_config
from scripts.gemini_models import (
    DEFAULT_GEMINI_MODEL,
    GEMINI_MODELS,
    resolve_gemini_model,
)
from webui import settings_store


def test_supported_gemini_models_exclude_retired_ids():
    assert DEFAULT_GEMINI_MODEL == "gemini-3.5-flash-lite"
    assert DEFAULT_GEMINI_MODEL in GEMINI_MODELS
    assert len(GEMINI_MODELS) == len(set(GEMINI_MODELS))
    assert not {
        "gemini-2.5-flash-lite-preview-09-2025",
        "gemini-2.0-flash",
        "gemini-1.5-flash",
        "gemini-1.5-pro",
        "gemini-pro",
    }.intersection(GEMINI_MODELS)


def test_unsupported_saved_model_resolves_to_the_current_default():
    assert resolve_gemini_model("gemini-2.5-flash-lite-preview-09-2025") == DEFAULT_GEMINI_MODEL
    assert resolve_gemini_model("gemini-3.8-flash") == "gemini-3.8-flash"


def test_cli_config_migrates_a_retired_model_without_changing_app_version(tmp_path, capsys):
    config = tmp_path / "api_config.json"
    config.write_text(json.dumps({
        "gemini": {"model": "gemini-2.5-flash-lite-preview-09-2025"},
    }), encoding="utf-8")

    loaded = secure_config.load_api_config(base_dir=str(tmp_path))

    assert loaded["gemini"]["model"] == DEFAULT_GEMINI_MODEL
    assert "Unsupported Gemini model" in capsys.readouterr().out


def test_webui_settings_migrate_and_reject_unknown_models(tmp_path):
    config = tmp_path / "api_config.json"
    config.write_text(json.dumps({
        "gemini": {"model": "gemini-2.5-flash-lite-preview-09-2025"},
    }), encoding="utf-8")

    loaded = settings_store.load_ui_settings(base_dir=str(tmp_path))
    ok, message = settings_store.save_ui_settings(
        ai_backend="gemini", ai_model="gemini-not-a-model", base_dir=str(tmp_path))

    assert loaded["ai_model"] == DEFAULT_GEMINI_MODEL
    assert ok is False
    assert "unavailable" in message


def test_gemini_404_explains_how_to_recover(monkeypatch):
    def unavailable(*args, **kwargs):
        raise RuntimeError("404 NOT_FOUND: model is not found for generateContent")

    monkeypatch.setattr(create_viral_segments, "HAS_GEMINI", True)
    monkeypatch.setattr(create_viral_segments, "_gemini_generate", unavailable)

    with pytest.raises(RuntimeError, match="unavailable \\(HTTP 404\\)") as error:
        create_viral_segments.call_gemini(
            "prompt", "test-key", model_name="gemini-retired-model")

    assert "WebUI settings" in str(error.value)
    assert DEFAULT_GEMINI_MODEL in str(error.value)


def test_g4f_ui_choices_do_not_offer_retired_gemini_models():
    source = Path(__file__).resolve().parents[1] / "webui" / "app.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    assignment = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "G4F_MODELS"
                for target in node.targets)
    )
    choices = ast.literal_eval(assignment.value)

    assert "gemini-1.5-flash" not in choices
    assert "gemini-pro" not in choices

def test_malformed_api_config_warns_and_uses_supported_default(tmp_path, caplog):
    (tmp_path / "api_config.json").write_text("{broken", encoding="utf-8")
    loaded = secure_config.load_api_config(base_dir=str(tmp_path))
    assert loaded["gemini"]["model"] == DEFAULT_GEMINI_MODEL
    assert any("Could not read api_config.json" in record.message for record in caplog.records)
