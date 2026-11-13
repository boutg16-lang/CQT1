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
_AUTO_MODELS: dict[str, str] = {}   # base_url -> detected embedding model
_AUTO_FAILED: set[str] = set()      # base_url -> no embedding model

TRUE_VALUES = {"1", "true", "yes", "on"}
FALSE_VALUES = {"0", "false", "no", "off"}

# Model families that are actually embedding models. Auto-detection only
# enables the feature when the local server advertises one of these.
_EMBEDDING_NAME_RE = __import__("re").compile(
    r"(embed|bge|gte|e5[-_]|nomic|mxbai|paraphrase|all-minilm)", __import__("re").IGNORECASE)


def _is_local_url(url: str) -> bool:
    return bool(__import__("re").search(
        r"^https?://(localhost|127\.0\.0\.1|\[::1\]|0\.0\.0\.0)(:|/)",
        str(url or ""), __import__("re").IGNORECASE))


def _auto_key(config: dict | None) -> str:
    try:
        return openai_compat.resolve_settings(config)["base_url"]
    except Exception:
        return ""


def _auto_enable(config: dict | None) -> bool:
    """Enable embeddings automatically for a LOCAL server that has a model.

    Detection is cached per base URL: a second project pointing at another
    server must not reuse the first server's embedding model name.
    """
    if not config:
        return False
    base_url = _auto_key(config)
    if not base_url:
        return False
    if base_url in _AUTO_MODELS:
        return True
    if base_url in _AUTO_FAILED:
        return False
    try:
        if not _is_local_url(base_url):
            _AUTO_FAILED.add(base_url)
            return False
        models = openai_compat.list_models(config=config, timeout=2.0)
    except Exception:
        _AUTO_FAILED.add(base_url)
        return False
    matches = [model for model in models if _EMBEDDING_NAME_RE.search(model)]
    if not matches:
        _AUTO_FAILED.add(base_url)
        return False
    _AUTO_MODELS[base_url] = sorted(matches, key=len, reverse=True)[0]
    logger.info("Auto-enabled semantic embeddings with %s on %s",
                _AUTO_MODELS[base_url], base_url)
    return True


def enabled(config: dict | None = None, *, allow_auto: bool = False) -> bool:
    """True when embeddings should run.

    Explicit ``VIRALCUTTER_EMBEDDINGS=1|0`` always wins. Otherwise an explicit
    api_config.openai.embeddings value wins. With neither set, ``allow_auto``
    enables the feature only for a LOCAL OpenAI-compatible server that
    advertises a real embedding model — cloud providers are never probed or
    billed without an explicit opt-in.
    """
    env = os.getenv("VIRALCUTTER_EMBEDDINGS", "").strip().lower()
    if env in TRUE_VALUES:
        return True
    if env in FALSE_VALUES:
        return False
    section = (config or {}).get("openai")
    if isinstance(section, dict):
        value = section.get("embeddings")
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            text = value.strip().lower()
            if text in TRUE_VALUES:
                return True
            if text in FALSE_VALUES:
                return False
    return _auto_enable(config) if allow_auto else False


def model_name(config: dict | None = None) -> str:
    section = (config or {}).get("openai") if isinstance(config, dict) else {}
    section = section if isinstance(section, dict) else {}
    explicit = (os.getenv("VIRALCUTTER_EMBEDDING_MODEL", "").strip()
                or str(section.get("embedding_model") or "").strip())
    if explicit:
        return explicit
    detected = _AUTO_MODELS.get(_auto_key(config))
    if detected:
        return detected
    return openai_compat.resolve_settings(config)["embedding_model"]


def _cache_key(text: str, config: dict | None) -> tuple[str, str]:
    return model_name(config), text


def embed_texts(texts, config: dict | None = None, *,
                allow_auto: bool = False) -> list[list[float]] | None:
    """Embed the texts, or return None (never raise) when unavailable.

    The caller is expected to fall back to lexical similarity on None.
    """
    global _DISABLED_REASON
    items = [str(text or "").strip() for text in (texts or [])]
    if not items or not enabled(config, allow_auto=allow_auto):
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
    _AUTO_MODELS.clear()
    _AUTO_FAILED.clear()


def disabled_reason() -> str:
    return _DISABLED_REASON
