# -*- coding: utf-8 -*-
"""Tests for automatic provider/model routing and failover."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import ai_autorouter, semantic_embeddings
from scripts import create_viral_segments as cvs


def _candidate(backend="openai", model="qwen2.5:7b", local=True, base="http://localhost:11434/v1"):
    return ai_autorouter.Candidate(
        backend=backend, model=model, base_url=base, local=local,
        chunk_size=9000, config={"openai": {"base_url": base, "model": model}})


def test_model_selection_prefers_chat_models_and_ignores_embeddings():
    models = ["bge-m3", "nomic-embed-text", "qwen2.5:7b", "llama3.1:8b"]
    assert ai_autorouter.select_model(models) == "qwen2.5:7b"


def test_model_selection_penalises_models_over_the_param_cap(monkeypatch):
    monkeypatch.setenv("VIRALCUTTER_AUTO_MAX_PARAMS", "14")
    score_7b = ai_autorouter.model_score("qwen2.5:7b")
    score_70b = ai_autorouter.model_score("qwen2.5:70b")
    assert score_7b > score_70b


def test_router_orders_local_before_remote_and_gemini(monkeypatch):
    monkeypatch.setattr(ai_autorouter, "_openai_candidates", lambda config, timeout: [
        _candidate(local=False, base="https://openrouter.ai/api/v1"),
        _candidate(local=True),
    ])
    monkeypatch.setattr(ai_autorouter, "_gemini_candidate",
                        lambda config: _candidate("gemini", "gemini-3.5-flash-lite"))
    monkeypatch.setattr(ai_autorouter, "_g4f_candidate", lambda config: None)
    chain = ai_autorouter.detect({})
    assert [item.backend for item in chain] == ["openai", "openai", "gemini"]
    assert chain[0].local is True
    assert chain[1].local is False


def test_router_fails_over_and_remembers_the_working_provider():
    calls = []

    def broken(candidate, prompt):
        calls.append("broken")
        raise RuntimeError("server down")

    def working(candidate, prompt):
        calls.append("working")
        return "{\"segments\": []}"

    router = ai_autorouter.AutoRouter(
        [_candidate(model="first"), _candidate(model="second", base="http://localhost:9999/v1")],
        {"openai": None, "gemini": None})
    # Same backend key means both candidates share the callback; simulate the
    # first failing per-call by making the callback stateful.
    state = {"n": 0}

    def flaky(candidate, prompt):
        state["n"] += 1
        if state["n"] == 1:
            return broken(candidate, prompt)
        return working(candidate, prompt)

    router.callbacks["openai"] = flaky
    assert router.chat("hi") == "{\"segments\": []}"
    assert router.used.model == "second"
    # The successful provider is tried first on the next chunk.
    assert router.chat("again") == "{\"segments\": []}"
    assert router.used.model == "second"
    assert calls.count("broken") == 1


def test_router_raises_when_every_provider_fails():
    router = ai_autorouter.AutoRouter([_candidate()], {
        "openai": lambda candidate, prompt: (_ for _ in ()).throw(RuntimeError("no")),
    })
    try:
        router.chat("hi")
    except RuntimeError as exc:
        assert "automatic AI routing failed" in str(exc)
    else:
        raise AssertionError("expected failure")


def test_embedding_auto_enable_only_for_local_server(monkeypatch):
    semantic_embeddings.reset_cache()
    monkeypatch.delenv("VIRALCUTTER_EMBEDDINGS", raising=False)
    config = {"openai": {"base_url": "http://localhost:11434/v1"}}
    monkeypatch.setattr(semantic_embeddings.openai_compat, "resolve_settings",
                        lambda cfg: {"base_url": cfg["openai"]["base_url"]})
    monkeypatch.setattr(semantic_embeddings.openai_compat, "list_models",
                        lambda config=None, timeout=None: ["qwen2.5:7b", "bge-m3"])
    assert semantic_embeddings.enabled(config, allow_auto=True) is True
    assert semantic_embeddings.model_name(config) == "bge-m3"

    semantic_embeddings.reset_cache()
    remote = {"openai": {"base_url": "https://openrouter.ai/api/v1"}}
    assert semantic_embeddings.enabled(remote, allow_auto=True) is False


def test_auto_create_uses_the_router(tmp_path, monkeypatch):
    (tmp_path / "input.srt").write_text(
        "1\n00:00:00,000 --> 00:00:06,000\nمرحبا بكم في حلقة جديدة عن النجاح\n\n"
        "2\n00:00:06,000 --> 00:00:12,000\nالنجاح يحتاج صبرا والتزاما يوميا\n\n",
        encoding="utf-8")
    captured = {}

    monkeypatch.setattr(ai_autorouter, "detect", lambda config: [_candidate()])

    def fake_chat(prompt, model=None, config=None):
        captured["model"] = model
        return json.dumps({"segments": [{"title": "الصبر", "start_time": 6,
                                        "end_time": 12, "score": 80}]})

    monkeypatch.setattr(cvs.openai_compat, "chat", fake_chat)

    def fake_process(raw, *args, **kwargs):
        captured["raw"] = raw
        captured["allow_auto_embeddings"] = kwargs.get("allow_auto_embeddings")
        return {"segments": []}

    monkeypatch.setattr(cvs, "process_segments", fake_process)
    result = cvs.create(1, True, "", 5, 30, ai_mode="auto",
                        project_folder=str(tmp_path))
    assert result == {"segments": []}
    assert captured["model"] == "qwen2.5:7b"
    assert captured["raw"][0]["title"] == "الصبر"
    assert captured["allow_auto_embeddings"] is True


def test_autopilot_auto_mode_reports_the_chain(monkeypatch):
    from scripts import autopilot

    monkeypatch.setattr(ai_autorouter, "detect", lambda config: [_candidate()])
    report = autopilot.check_readiness(
        type("Args", (), {"ocr_check": "off", "visual_check": "off"})(),
        "auto", None)
    assert report["ok"] is True
    assert any(item["code"] == "auto_chain" for item in report["warnings"])


def test_detect_does_not_probe_the_same_url_twice(monkeypatch):
    calls = []

    def fake_models(config=None, timeout=None, base_url=None, api_key=None):
        calls.append(base_url)
        return ["qwen2.5:7b"]

    monkeypatch.setenv("VIRALCUTTER_OPENAI_BASE_URL", "http://127.0.0.1:11434/v1")
    monkeypatch.setattr(ai_autorouter.openai_compat, "list_models", fake_models)
    chain = ai_autorouter.detect({"openai": {"base_url": "http://127.0.0.1:11434/v1"}})
    assert len(chain) == 1
    assert calls.count("http://127.0.0.1:11434/v1") == 1


def test_configured_embedding_model_is_never_used_for_chat(monkeypatch):
    def fake_models(config=None, timeout=None, base_url=None, api_key=None):
        return ["bge-m3", "qwen2.5:7b"]

    monkeypatch.setattr(ai_autorouter.openai_compat, "list_models", fake_models)
    chain = ai_autorouter.detect(
        {"openai": {"base_url": "http://localhost:11434/v1", "model": "bge-m3"}})
    assert chain and chain[0].model == "qwen2.5:7b"


def test_explicit_url_disables_the_local_default_probe(monkeypatch):
    calls = []

    def fake_models(config=None, timeout=None, base_url=None, api_key=None):
        calls.append(base_url)
        return ["qwen2.5:7b"]

    monkeypatch.setenv("VIRALCUTTER_OPENAI_BASE_URL", "https://openrouter.ai/api/v1")
    monkeypatch.setattr(ai_autorouter.openai_compat, "list_models", fake_models)
    ai_autorouter.detect({"openai": {"base_url": "https://openrouter.ai/api/v1"}})
    assert calls == ["https://openrouter.ai/api/v1"]


def test_auto_embedding_detection_is_cached_per_server(monkeypatch):
    semantic_embeddings.reset_cache()
    monkeypatch.delenv("VIRALCUTTER_EMBEDDINGS", raising=False)
    monkeypatch.setattr(semantic_embeddings.openai_compat, "resolve_settings",
                        lambda cfg: {"base_url": cfg["openai"]["base_url"]})
    responses = {
        "http://localhost:11434/v1": ["qwen2.5:7b", "bge-m3"],
        "http://localhost:9999/v1": ["qwen2.5:7b"],
    }
    monkeypatch.setattr(semantic_embeddings.openai_compat, "list_models",
                        lambda config=None, timeout=None: responses[
                            config["openai"]["base_url"]])
    first = {"openai": {"base_url": "http://localhost:11434/v1"}}
    second = {"openai": {"base_url": "http://localhost:9999/v1"}}
    assert semantic_embeddings.enabled(first, allow_auto=True) is True
    assert semantic_embeddings.model_name(first) == "bge-m3"
    assert semantic_embeddings.enabled(second, allow_auto=True) is False
