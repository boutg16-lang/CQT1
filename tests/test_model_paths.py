# -*- coding: utf-8 -*-
"""Models live on the project drive, not the Windows C: user cache."""
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import model_paths

CACHE_ENVS = (
    "VIRALCUTTER_MODELS_DIR", "HF_HOME", "HUGGINGFACE_HUB_CACHE",
    "HF_HUB_CACHE", "TRANSFORMERS_CACHE", "HF_DATASETS_CACHE", "TORCH_HOME",
)


def _clear(monkeypatch):
    for name in CACHE_ENVS:
        monkeypatch.delenv(name, raising=False)


def test_configure_points_every_cache_at_the_project(tmp_path, monkeypatch):
    _clear(monkeypatch)
    paths = model_paths.configure_model_cache(root=tmp_path, force=True)
    assert paths["hf_home"] == str(tmp_path / "huggingface")
    assert paths["hf_hub_cache"] == str(tmp_path / "huggingface" / "hub")
    assert paths["torch_home"] == str(tmp_path / "torch")
    assert os.environ["HF_HOME"] == str(tmp_path / "huggingface")
    assert os.environ["HUGGINGFACE_HUB_CACHE"] == str(
        tmp_path / "huggingface" / "hub")
    assert os.environ["TORCH_HOME"] == str(tmp_path / "torch")
    assert Path(paths["hf_hub_cache"]).is_dir()


def test_explicit_custom_hf_home_is_respected(tmp_path, monkeypatch):
    _clear(monkeypatch)
    custom = tmp_path / "my-own-cache"
    monkeypatch.setenv("HF_HOME", str(custom))
    model_paths.configure_model_cache(root=tmp_path / "models")
    assert os.environ["HF_HOME"] == str(custom)


def test_huggingface_default_path_is_moved(tmp_path, monkeypatch):
    _clear(monkeypatch)
    default = Path(os.path.expanduser("~")) / ".cache" / "huggingface"
    monkeypatch.setenv("HF_HOME", str(default))
    model_paths.configure_model_cache(root=tmp_path / "models")
    assert os.environ["HF_HOME"] != str(default)
    assert os.environ["HF_HOME"].endswith(".models/huggingface") or \
        os.environ["HF_HOME"] == str(tmp_path / "models" / "huggingface")


def test_models_dir_env_override(tmp_path, monkeypatch):
    _clear(monkeypatch)
    target = tmp_path / "D-models"
    monkeypatch.setenv("VIRALCUTTER_MODELS_DIR", str(target))
    assert model_paths.models_root() == target.resolve()
    model_paths.configure_model_cache()
    assert os.environ["HF_HOME"] == str(target / "huggingface")


def test_current_paths_are_strings(tmp_path, monkeypatch):
    _clear(monkeypatch)
    model_paths.configure_model_cache(root=tmp_path, force=True)
    paths = model_paths.current_paths()
    assert all(isinstance(value, str) and value for value in paths.values())


def test_migrate_cache_copies_without_removing_source(tmp_path):
    source = tmp_path / "old-cache"
    (source / "hub" / "models--x").mkdir(parents=True)
    (source / "hub" / "models--x" / "blob").write_text("weights", encoding="utf-8")
    destination = tmp_path / "new-cache"
    result = model_paths.migrate_cache(source, destination=destination)
    assert result["ok"] is True and result["copied"] is True
    assert result["moved"] is False
    assert (destination / "hub" / "models--x" / "blob").read_text(
        encoding="utf-8") == "weights"
    assert source.is_dir()


def test_migrate_cache_move_removes_the_old_copy(tmp_path):
    source = tmp_path / "old-cache"
    source.mkdir()
    (source / "file.bin").write_bytes(b"x")
    destination = tmp_path / "new-cache"
    result = model_paths.migrate_cache(source, move=True, destination=destination)
    assert result["ok"] is True and result["moved"] is True
    assert not source.exists()
    assert (destination / "file.bin").exists()


def test_models_dir_override_beats_a_custom_hf_home(tmp_path, monkeypatch):
    _clear(monkeypatch)
    target = tmp_path / "forced"
    monkeypatch.setenv("VIRALCUTTER_MODELS_DIR", str(target))
    monkeypatch.setenv("HF_HOME", str(tmp_path / "custom-hf"))
    model_paths.configure_model_cache()
    assert os.environ["HF_HOME"] == str(target / "huggingface")


def test_xet_is_disabled_by_default(tmp_path, monkeypatch):
    _clear(monkeypatch)
    monkeypatch.delenv("HF_HUB_DISABLE_XET", raising=False)
    monkeypatch.delenv("VIRALCUTTER_ENABLE_XET", raising=False)
    model_paths.configure_model_cache(root=tmp_path, force=True)
    assert os.environ["HF_HUB_DISABLE_XET"] == "1"
    assert os.environ["HF_HUB_DOWNLOAD_TIMEOUT"] == "60"


def test_xet_can_be_opted_back_in(tmp_path, monkeypatch):
    _clear(monkeypatch)
    monkeypatch.delenv("HF_HUB_DISABLE_XET", raising=False)
    monkeypatch.setenv("VIRALCUTTER_ENABLE_XET", "1")
    model_paths.configure_model_cache(root=tmp_path, force=True)
    assert "HF_HUB_DISABLE_XET" not in os.environ


def test_explicit_xet_setting_wins(tmp_path, monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("HF_HUB_DISABLE_XET", "0")
    model_paths.configure_model_cache(root=tmp_path, force=True)
    assert os.environ["HF_HUB_DISABLE_XET"] == "0"
