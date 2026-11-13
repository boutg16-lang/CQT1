# -*- coding: utf-8 -*-
"""semantic_embeddings — optional vector similarity on top of openai_compat.

The deterministic surface similarity in ``transcript_window`` is good at the
same words in a different order. It cannot see that «الصبر يصنع النتيجة» and
«الاستمرارية هي التي توصل» are the same idea. When the user runs an
OpenAI-compatible server with an embedding model (Ollama's ``bge-m3``,
``nomic-embed-text``, OpenAI ``text-embedding-3-small`` …), this module adds
real semantic similarity for de-duplication and repetition penalties.

Design rules:

* OFF by default — enabling it makes network calls, so it is explicit
  (``VIRALCUTTER_EMBEDDINGS=1`` or ``api_config.openai.embeddings=true``).
* Never fatal — any failure logs once and returns None; callers keep the
  lexical path.
* Bounded cache — the same clip text is only embedded once per process.
"""

from __future__ import annotations

import logging
import os

from scripts import openai_compat

logger = logging.getLogger(__name__)

_CACHE: dict[tuple[str, str], list[float]] = {}
_CACHE_LIMIT = 1024
_DISABLED_REASON = ""

TRUE_VALUES = {"1", "true", "yes", "on"}


def enabled(config: dict | None = None) -> bool:
    """True when the user explicitly opted into embeddings."""
    env = os.getenv("VIRALCUTTER_EMBEDDINGS", "").strip().lower()
    if env:
        return env in TRUE_VALUES
    section = (config or {}).get("openai")
    if isinstance(section, dict):
        value = section.get("embeddings")
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in TRUE_VALUES
    return False


def model_name(config: dict | None = None) -> str:
    return openai_compat.resolve_settings(config)["embedding_model"]


def _cache_key(text: str, config: dict | None) -> tuple[str, str]:
    return model_name(config), text


def embed_texts(texts, config: dict | None = None) -> list[list[float]] | None:
    """Embed the texts, or return None (never raise) when unavailable.

    The caller is expected to fall back to lexical similarity on None.
    """
    global _DISABLED_REASON
    items = [str(text or "").strip() for text in (texts or [])]
    if not items or not enabled(config):
        return None
    if _DISABLED_REASON:
        return None

    missing = []
    seen = set()
    for text in items:
        key = _cache_key(text, config)
        if text and key not in _CACHE and key not in seen:
            seen.add(key)
            missing.append(text)
    if missing:
        try:
            vectors = openai_compat.embed(missing, config=config)
        except openai_compat.OpenAICompatError as exc:
            _DISABLED_REASON = str(exc)
            logger.warning(
                "Embeddings unavailable (%s); using lexical similarity for this run.",
                exc)
            return None
        except Exception as exc:  # never let an optimization break a run
            _DISABLED_REASON = type(exc).__name__
            logger.warning("Embeddings failed (%s); using lexical similarity.",
                           type(exc).__name__)
            return None
        if len(vectors) != len(missing):
            _DISABLED_REASON = "vector count mismatch"
            logger.warning("Embeddings returned %d vectors for %d texts; "
                           "using lexical similarity.", len(vectors), len(missing))
            return None
        if len(_CACHE) + len(missing) > _CACHE_LIMIT:
            _CACHE.clear()
        for text, vector in zip(missing, vectors, strict=False):
            _CACHE[_cache_key(text, config)] = vector

    result = []
    for text in items:
        vector = _CACHE.get(_cache_key(text, config))
        if vector is None:
            return None
        result.append(vector)
    return result


def similarity(left_vector, right_vector) -> float:
    return openai_compat.cosine(left_vector, right_vector)


def reset_cache() -> None:
    """Test hook / memory release between projects."""
    global _DISABLED_REASON
    _CACHE.clear()
    _DISABLED_REASON = ""


def disabled_reason() -> str:
    return _DISABLED_REASON
