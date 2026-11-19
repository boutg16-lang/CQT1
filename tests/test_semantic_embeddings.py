# -*- coding: utf-8 -*-
"""Direct tests for scripts/semantic_embeddings.py (optional vector similarity)."""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import openai_compat
from scripts import semantic_embeddings as se


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    se.reset_cache()
    monkeypatch.delenv("VIRALCUTTER_EMBEDDINGS", raising=False)
    monkeypatch.delenv("VIRALCUTTER_EMBEDDING_MODEL", raising=False)
    yield
    se.reset_cache()


LOCAL_CONFIG = {"openai": {"base_url": "http://localhost:11434/v1"}}
REMOTE_CONFIG = {"openai": {"base_url": "https://api.example.com/v1"}}


class TestIsLocalUrl:
    @pytest.mark.parametrize(
        "url",
        [
            "http://localhost:11434/v1",
            "https://localhost:8080/",
            "http://127.0.0.1:11434/v1",
            "http://[::1]:11434/v1",
            "http://0.0.0.0:11434/v1",
            "HTTP://LOCALHOST:11434/v1",
        ],
    )
    def test_local_urls(self, url):
        assert se._is_local_url(url)

    @pytest.mark.parametrize(
        "url",
        [
            "https://api.openai.com/v1",
            "http://example.com/v1",
            "http://localhost.evil.com/v1",
            "http://notlocalhost:11434/v1",
            "",
            None,
        ],
    )
    def test_non_local_urls(self, url):
        assert not se._is_local_url(url)


class TestAutoKey:
    def test_default_settings_base_url(self):
        assert se._auto_key(None) == "http://localhost:11434/v1"

    def test_config_base_url(self):
        assert se._auto_key(LOCAL_CONFIG) == "http://localhost:11434/v1"
        assert se._auto_key(REMOTE_CONFIG) == "https://api.example.com/v1"

    def test_unresolvable_config_returns_empty(self, monkeypatch):
        monkeypatch.setattr(
            openai_compat,
            "resolve_settings",
            lambda config: (_ for _ in ()).throw(RuntimeError("bad")),
        )
        assert se._auto_key(LOCAL_CONFIG) == ""


class TestEnabled:
    def test_env_true_values_enable(self, monkeypatch):
        for value in ("1", "true", "yes", "on", "TRUE", " On "):
            monkeypatch.setenv("VIRALCUTTER_EMBEDDINGS", value)
            assert se.enabled() is True

    def test_env_false_values_disable(self, monkeypatch):
        for value in ("0", "false", "no", "off"):
            monkeypatch.setenv("VIRALCUTTER_EMBEDDINGS", value)
            assert se.enabled() is False

    def test_env_wins_over_config(self, monkeypatch):
        monkeypatch.setenv("VIRALCUTTER_EMBEDDINGS", "0")
        assert se.enabled({"openai": {"embeddings": True}}) is False
        monkeypatch.setenv("VIRALCUTTER_EMBEDDINGS", "1")
        assert se.enabled({"openai": {"embeddings": False}}) is True

    def test_config_bool_wins(self):
        assert se.enabled({"openai": {"embeddings": True}}) is True
        assert se.enabled({"openai": {"embeddings": False}}) is False

    def test_config_string_values(self):
        assert se.enabled({"openai": {"embeddings": "yes"}}) is True
        assert se.enabled({"openai": {"embeddings": "no"}}) is False

    def test_config_without_openai_section_is_disabled(self):
        assert se.enabled({}) is False
        assert se.enabled(None) is False
        assert se.enabled({"openai": "not-a-dict"}) is False

    def test_no_auto_without_allow_auto(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            openai_compat, "list_models", lambda **kw: calls.append(1) or ["bge-m3"]
        )
        assert se.enabled(LOCAL_CONFIG) is False
        assert calls == []

    def test_auto_enable_local_server_with_embedding_model(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            openai_compat,
            "list_models",
            lambda **kw: calls.append(1) or ["llama3.1", "bge-m3"],
        )
        assert se.enabled(LOCAL_CONFIG, allow_auto=True) is True
        assert se._AUTO_MODELS["http://localhost:11434/v1"] == "bge-m3"
        # Second call reuses the cached detection (no second probe).
        assert se.enabled(LOCAL_CONFIG, allow_auto=True) is True
        assert len(calls) == 1

    def test_auto_enable_picks_longest_matching_model(self, monkeypatch):
        monkeypatch.setattr(
            openai_compat, "list_models", lambda **kw: ["bge-m3", "nomic-embed-text-v1"]
        )
        assert se.enabled(LOCAL_CONFIG, allow_auto=True) is True
        assert se._AUTO_MODELS["http://localhost:11434/v1"] == "nomic-embed-text-v1"

    def test_auto_enable_no_embedding_model(self, monkeypatch):
        monkeypatch.setattr(
            openai_compat, "list_models", lambda **kw: ["llama3.1", "mistral"]
        )
        assert se.enabled(LOCAL_CONFIG, allow_auto=True) is False
        assert "http://localhost:11434/v1" in se._AUTO_FAILED

    def test_auto_enable_never_probes_remote_servers(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            openai_compat, "list_models", lambda **kw: calls.append(1) or ["bge-m3"]
        )
        assert se.enabled(REMOTE_CONFIG, allow_auto=True) is False
        assert calls == []

    def test_auto_enable_list_failure_is_cached(self, monkeypatch):
        calls = []

        def boom(**kw):
            calls.append(1)
            raise ConnectionError("down")

        monkeypatch.setattr(openai_compat, "list_models", boom)
        assert se.enabled(LOCAL_CONFIG, allow_auto=True) is False
        assert se.enabled(LOCAL_CONFIG, allow_auto=True) is False
        assert len(calls) == 1

    def test_auto_enable_empty_config(self):
        assert se.enabled(None, allow_auto=True) is False
        assert se.enabled({}, allow_auto=True) is False


class TestModelName:
    def test_env_override_wins(self, monkeypatch):
        monkeypatch.setenv("VIRALCUTTER_EMBEDDING_MODEL", "my-embed")
        assert se.model_name(LOCAL_CONFIG) == "my-embed"

    def test_config_embedding_model(self):
        config = {"openai": {"embedding_model": "nomic-embed-text"}}
        assert se.model_name(config) == "nomic-embed-text"

    def test_default_model(self):
        assert se.model_name(None) == "bge-m3"

    def test_auto_detected_model(self, monkeypatch):
        monkeypatch.setattr(openai_compat, "list_models", lambda **kw: ["bge-m3"])
        assert se.enabled(LOCAL_CONFIG, allow_auto=True) is True
        assert se.model_name(LOCAL_CONFIG) == "bge-m3"


class TestEmbedTexts:
    def _enable(self, monkeypatch):
        monkeypatch.setenv("VIRALCUTTER_EMBEDDINGS", "1")

    def test_disabled_returns_none(self, monkeypatch):
        monkeypatch.setenv("VIRALCUTTER_EMBEDDINGS", "0")
        assert se.embed_texts(["hello"], config=None) is None

    def test_empty_input_returns_none(self, monkeypatch):
        self._enable(monkeypatch)
        assert se.embed_texts([], config=None) is None
        assert se.embed_texts(None, config=None) is None

    def test_happy_path_and_cache(self, monkeypatch):
        self._enable(monkeypatch)
        calls = []

        def fake_embed(texts, config=None):
            calls.append(list(texts))
            return [[float(i)] * 3 for i in range(len(texts))]

        monkeypatch.setattr(openai_compat, "embed", fake_embed)
        result = se.embed_texts(["hello", "world"], config=None)
        assert result == [[0.0, 0.0, 0.0], [1.0, 1.0, 1.0]]
        assert calls == [["hello", "world"]]
        # Second call serves from cache (embed not called again).
        assert se.embed_texts(["hello"], config=None) == [[0.0, 0.0, 0.0]]
        assert len(calls) == 1

    def test_duplicate_texts_embedded_once(self, monkeypatch):
        self._enable(monkeypatch)
        calls = []
        monkeypatch.setattr(
            openai_compat,
            "embed",
            lambda texts, config=None: (
                calls.append(list(texts)) or [[0.1]] * len(texts)
            ),
        )
        assert se.embed_texts(["same", "same", "other"], config=None) is not None
        assert calls == [["same", "other"]]

    def test_whitespace_is_stripped_before_embedding(self, monkeypatch):
        self._enable(monkeypatch)
        calls = []
        monkeypatch.setattr(
            openai_compat,
            "embed",
            lambda texts, config=None: calls.append(list(texts)) or [[0.1]],
        )
        se.embed_texts(["  padded  "], config=None)
        assert calls == [["padded"]]

    def test_empty_string_text_never_resolves(self, monkeypatch):
        # NOTE (verified behaviour): blank texts are never fetched, so the
        # result loop finds no cached vector for them and returns None.
        self._enable(monkeypatch)
        monkeypatch.setattr(openai_compat, "embed", lambda texts, config=None: [[0.1]])
        assert se.embed_texts(["", "hello"], config=None) is None

    def test_compat_error_disables_for_the_run(self, monkeypatch):
        self._enable(monkeypatch)
        calls = []

        def boom(texts, config=None):
            calls.append(1)
            raise openai_compat.OpenAICompatError("server down")

        monkeypatch.setattr(openai_compat, "embed", boom)
        assert se.embed_texts(["hello"], config=None) is None
        assert se.disabled_reason() == "server down"
        # Disabled for the rest of the run: embed is not retried.
        assert se.embed_texts(["hello"], config=None) is None
        assert len(calls) == 1

    def test_generic_exception_disables_with_type_name(self, monkeypatch):
        self._enable(monkeypatch)
        monkeypatch.setattr(
            openai_compat,
            "embed",
            lambda texts, config=None: (_ for _ in ()).throw(ValueError("x")),
        )
        assert se.embed_texts(["hello"], config=None) is None
        assert se.disabled_reason() == "ValueError"

    def test_vector_count_mismatch_returns_none(self, monkeypatch):
        self._enable(monkeypatch)
        monkeypatch.setattr(
            openai_compat, "embed", lambda texts, config=None: [[0.1]]
        )  # 1 vector for 2 texts
        assert se.embed_texts(["a", "b"], config=None) is None
        assert se.disabled_reason() == "vector count mismatch"

    def test_reset_cache_clears_reason_and_cache(self, monkeypatch):
        self._enable(monkeypatch)
        monkeypatch.setattr(
            openai_compat,
            "embed",
            lambda texts, config=None: (_ for _ in ()).throw(ValueError("x")),
        )
        se.embed_texts(["hello"], config=None)
        assert se.disabled_reason() == "ValueError"
        se.reset_cache()
        assert se.disabled_reason() == ""
        assert se._CACHE == {}


class TestSimilarity:
    def test_identical_vectors(self):
        assert se.similarity([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)

    def test_orthogonal_vectors(self):
        assert se.similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)

    def test_opposite_vectors(self):
        assert se.similarity([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(-1.0)

    def test_empty_vectors(self):
        assert se.similarity([], []) == 0.0
