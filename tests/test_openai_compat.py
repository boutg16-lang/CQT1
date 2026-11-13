# -*- coding: utf-8 -*-
"""Tests for the OpenAI-compatible backend and the optional semantic layer."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import create_viral_segments as cvs
from scripts import openai_compat, semantic_embeddings


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text or (json.dumps(payload) if payload is not None else "")

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


def _chat_payload(content):
    return {"choices": [{"message": {"role": "assistant", "content": content}}]}


def test_chat_sends_the_configured_model_and_returns_text(monkeypatch):
    calls = []

    def fake_post(url, json=None, headers=None, timeout=None):
        calls.append((url, json, headers))
        return FakeResponse(200, _chat_payload("{\"segments\": []}"))

    monkeypatch.setattr(openai_compat.requests, "post", fake_post)
    config = {"openai": {"base_url": "http://box:1234/v1", "api_key": "sk-test",
                         "model": "qwen2.5:7b"}}
    result = openai_compat.chat("hello", config=config)
    assert result == "{\"segments\": []}"
    url, payload, headers = calls[0]
    assert url == "http://box:1234/v1/chat/completions"
    assert payload["model"] == "qwen2.5:7b"
    assert headers["Authorization"] == "Bearer sk-test"


def test_chat_retries_transient_429_then_succeeds(monkeypatch):
    responses = [FakeResponse(429, text="busy"), FakeResponse(200, _chat_payload("ok"))]

    def fake_post(*args, **kwargs):
        return responses.pop(0)

    monkeypatch.setattr(openai_compat.requests, "post", fake_post)
    monkeypatch.setattr(openai_compat.time, "sleep", lambda _s: None)
    assert openai_compat.chat("hi", retries=2) == "ok"


def test_auth_error_is_immediate_and_never_leaks_the_key(monkeypatch):
    calls = []

    def fake_post(*args, **kwargs):
        calls.append(1)
        return FakeResponse(401, text="bad key sk-super-secret-value")

    monkeypatch.setattr(openai_compat.requests, "post", fake_post)
    config = {"openai": {"api_key": "sk-super-secret-value"}}
    with pytest.raises(openai_compat.OpenAICompatError) as info:
        openai_compat.chat("hi", config=config, retries=3)
    assert len(calls) == 1  # no pointless retries on a bad key
    assert "sk-super-secret-value" not in str(info.value)


def test_embeddings_are_batched_and_ordered_by_index(monkeypatch):
    seen = []

    def fake_post(url, json=None, headers=None, timeout=None):
        seen.append(json["input"])
        return FakeResponse(200, {"data": [
            {"index": 1, "embedding": [0.0, 1.0]},
            {"index": 0, "embedding": [1.0, 0.0]},
        ]})

    monkeypatch.setattr(openai_compat.requests, "post", fake_post)
    vectors = openai_compat.embed(["first", "second"], batch_size=8)
    assert vectors == [[1.0, 0.0], [0.0, 1.0]]
    assert seen == [["first", "second"]]


def test_base_url_must_be_http(monkeypatch):
    monkeypatch.setenv("VIRALCUTTER_OPENAI_BASE_URL", "localhost:11434/v1")
    with pytest.raises(openai_compat.OpenAICompatError):
        openai_compat.resolve_settings({})


def test_env_overrides_config(monkeypatch):
    monkeypatch.setenv("VIRALCUTTER_OPENAI_BASE_URL", "http://127.0.0.1:9999/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "env-key")
    settings = openai_compat.resolve_settings(
        {"openai": {"base_url": "http://other/v1", "api_key": "file-key"}})
    assert settings["base_url"] == "http://127.0.0.1:9999/v1"
    assert settings["api_key"] == "env-key"


def test_semantic_embeddings_are_off_by_default(monkeypatch):
    monkeypatch.delenv("VIRALCUTTER_EMBEDDINGS", raising=False)
    assert semantic_embeddings.enabled({}) is False
    assert semantic_embeddings.embed_texts(["x"]) is None


def test_semantic_embeddings_cache_avoids_a_second_request(monkeypatch):
    monkeypatch.setenv("VIRALCUTTER_EMBEDDINGS", "1")
    calls = []

    def fake_embed(texts, config=None):
        calls.append(list(texts))
        return [[float(len(text)), 1.0] for text in texts]

    monkeypatch.setattr(semantic_embeddings.openai_compat, "embed", fake_embed)
    semantic_embeddings.reset_cache()
    first = semantic_embeddings.embed_texts(["a", "bb"], config={})
    second = semantic_embeddings.embed_texts(["a", "bb"], config={})
    assert first == second
    assert calls == [["a", "bb"]]


def test_embedding_vector_is_used_for_semantic_dedup(monkeypatch):
    def segment(title, start, text, vector):
        return {
            "title": title,
            "start_time": start,
            "end_time": start + 10,
            "transcript_text": text,
            "selection_score": 80,
            "_embedding": vector,
        }

    # Disjoint vocabulary, near-identical vectors: lexical similarity would
    # keep both, embeddings must drop one.
    left = segment("a", 0, "الصبر يصنع النتيجة", [1.0, 0.0, 0.0])
    right = segment("b", 100, "الاستمرارية هي التي توصل", [0.99, 0.01, 0.0])
    kept = cvs.deduplicate_segments([left, right])
    assert len(kept) == 1


def test_settings_store_round_trips_openai_backend(tmp_path):
    from webui import settings_store

    ok, error = settings_store.save_ui_settings(
        ai_backend="openai", ai_model="qwen2.5:7b", chunk_size=9000,
        api_keys=[], base_dir=str(tmp_path))
    assert ok, error
    loaded = settings_store.load_ui_settings(base_dir=str(tmp_path))
    assert loaded["ai_backend"] == "openai"
    assert loaded["ai_model"] == "qwen2.5:7b"
    assert loaded["chunk_size"] == 9000
    assert loaded["openai_base_url"].startswith("http")


def test_create_uses_the_openai_backend(tmp_path, monkeypatch):
    (tmp_path / "input.srt").write_text(
        "1\n00:00:00,000 --> 00:00:06,000\nمرحبا بكم في حلقة جديدة عن النجاح\n\n"
        "2\n00:00:06,000 --> 00:00:12,000\nالنجاح يحتاج صبرا والتزاما يوميا\n\n",
        encoding="utf-8")
    captured = {}

    def fake_chat(prompt, model=None, config=None):
        captured["model"] = model
        captured["prompt"] = prompt
        return json.dumps({"segments": [{
            "title": "الصبر",
            "start_time": 6,
            "end_time": 12,
            "score": 80,
        }]})

    def fake_process(raw, *args, **kwargs):
        captured["raw"] = raw
        captured["llm_config"] = kwargs.get("llm_config")
        return {"segments": []}

    monkeypatch.setattr(openai_compat, "chat", fake_chat)
    monkeypatch.setattr(cvs, "process_segments", fake_process)

    result = cvs.create(1, True, "", 5, 30, ai_mode="openai",
                        project_folder=str(tmp_path), model_name_arg="qwen2.5:7b")
    assert result == {"segments": []}
    assert captured["model"] == "qwen2.5:7b"
    assert captured["raw"][0]["title"] == "الصبر"
    assert captured["llm_config"]["openai"]["base_url"]
