# -*- coding: utf-8 -*-
"""openai_compat — one client for every OpenAI-compatible LLM server.

Why this exists: the pipeline used to speak only Gemini, g4f and a bundled
GGUF file. The modern local/self-hosted ecosystem (Ollama, LM Studio, vLLM,
llama.cpp server) and most hosted providers (OpenRouter, Groq, Together,
DeepSeek) expose the same ``/v1/chat/completions`` and ``/v1/embeddings``
surface. One small client unlocks all of them without adding a dependency:
``requests`` is already core.

Configuration precedence (first non-empty wins):

* ``VIRALCUTTER_OPENAI_BASE_URL`` / ``OPENAI_BASE_URL`` / api_config.openai.base_url
* ``VIRALCUTTER_OPENAI_KEY`` / ``OPENAI_API_KEY`` / api_config.openai.api_key
* ``VIRALCUTTER_OPENAI_MODEL`` / api_config.openai.model
* ``VIRALCUTTER_EMBEDDING_MODEL`` / api_config.openai.embedding_model

Security: the key is only ever sent in the Authorization header. Error
messages never echo headers, request bodies or the key itself.
"""

from __future__ import annotations

import json
import os
import re
import time

try:
    import requests
except Exception:  # pragma: no cover - requests is a core dependency
    requests = None

DEFAULT_BASE_URL = "http://localhost:11434/v1"  # Ollama's OpenAI-compatible API
DEFAULT_MODEL = "llama3.1"
DEFAULT_EMBEDDING_MODEL = "bge-m3"
DEFAULT_TIMEOUT = 180.0
MAX_RETRIES = 3

_SECRET_RE = re.compile(r"(sk-[A-Za-z0-9_\-]{8,}|AIza[A-Za-z0-9_\-]{10,})")


class OpenAICompatError(RuntimeError):
    """Raised with an operator-readable, secret-free message."""


def _first_env(*names: str) -> str:
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    return ""


def _section(config: dict | None) -> dict:
    section = (config or {}).get("openai")
    return section if isinstance(section, dict) else {}


def _clean_base_url(value: str) -> str:
    value = str(value or "").strip().rstrip("/")
    if not value:
        return DEFAULT_BASE_URL
    if not re.match(r"^https?://", value, flags=re.IGNORECASE):
        raise OpenAICompatError(
            "OpenAI-compatible base URL must start with http:// or https://")
    return value


def resolve_settings(config: dict | None = None) -> dict:
    """Resolve base URL, key, model and embedding model from env + config."""
    section = _section(config)
    return {
        "base_url": _clean_base_url(
            _first_env("VIRALCUTTER_OPENAI_BASE_URL", "OPENAI_BASE_URL")
            or section.get("base_url")
            or DEFAULT_BASE_URL),
        "api_key": _first_env("VIRALCUTTER_OPENAI_KEY", "OPENAI_API_KEY")
        or str(section.get("api_key") or "").strip(),
        "model": _first_env("VIRALCUTTER_OPENAI_MODEL")
        or str(section.get("model") or "").strip()
        or DEFAULT_MODEL,
        "embedding_model": _first_env("VIRALCUTTER_EMBEDDING_MODEL")
        or str(section.get("embedding_model") or "").strip()
        or DEFAULT_EMBEDDING_MODEL,
        "timeout": _timeout(section.get("timeout")),
    }


def _timeout(value) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT
    return parsed if 5.0 <= parsed <= 3600.0 else DEFAULT_TIMEOUT


def _sanitize(text, limit=300) -> str:
    cleaned = _SECRET_RE.sub("<redacted>", str(text or ""))
    cleaned = " ".join(cleaned.split())
    return cleaned[:limit]


def _headers(api_key: str) -> dict:
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if api_key:
        headers["Authorization"] = "Bearer " + api_key
    return headers


def _post(url: str, payload: dict, api_key: str, timeout: float):
    if requests is None:
        raise OpenAICompatError(
            "requests is not installed; install the core requirements")
    try:
        return requests.post(url, json=payload, headers=_headers(api_key),
                             timeout=timeout)
    except requests.exceptions.Timeout as exc:
        raise OpenAICompatError(
            "request timed out after {:.0f}s: {}".format(timeout, url)) from exc
    except requests.exceptions.RequestException as exc:
        raise OpenAICompatError(
            "could not reach {}: {}".format(url, _sanitize(exc))) from exc


def _raise_for_response(response, label: str) -> None:
    status = int(getattr(response, "status_code", 0) or 0)
    if status == 200:
        return
    try:
        body = response.text
    except Exception:
        body = ""
    detail = _sanitize(body)
    if status in (401, 403):
        raise OpenAICompatError(
            "{} rejected the credentials (HTTP {}). Set the provider key."
            .format(label, status))
    if status == 404:
        raise OpenAICompatError(
            "{} endpoint not found (HTTP 404). Check the base URL — for Ollama "
            "it is http://localhost:11434/v1.".format(label))
    raise OpenAICompatError("{} failed (HTTP {}): {}".format(label, status, detail))


def chat(prompt: str, *, model: str | None = None, config: dict | None = None,
         temperature: float = 0.4, max_tokens: int | None = None,
         timeout: float | None = None, retries: int = MAX_RETRIES) -> str:
    """Return the assistant text for one prompt, with bounded retries.

    Retries only transient failures (429 and 5xx). Authentication, malformed
    URL and 4xx request errors are surfaced immediately — retrying a bad key
    just wastes the user's time.
    """
    settings = resolve_settings(config)
    model = str(model or settings["model"])
    base_url = settings["base_url"]
    api_key = settings["api_key"]
    timeout = _timeout(timeout or settings["timeout"])
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": str(prompt or "")}],
        "temperature": float(temperature),
        "stream": False,
    }
    if max_tokens:
        payload["max_tokens"] = int(max_tokens)

    last_error = None
    for attempt in range(max(1, int(retries))):
        try:
            response = _post(base_url + "/chat/completions", payload, api_key, timeout)
        except OpenAICompatError as exc:
            last_error = exc
            if attempt + 1 >= retries:
                break
            time.sleep(2 ** attempt)
            continue
        status = int(getattr(response, "status_code", 0) or 0)
        if status == 200:
            try:
                data = response.json()
            except Exception as exc:
                raise OpenAICompatError(
                    "provider returned non-JSON response") from exc
            choices = data.get("choices") if isinstance(data, dict) else None
            if not isinstance(choices, list) or not choices:
                raise OpenAICompatError("provider returned no choices")
            message = choices[0].get("message") if isinstance(choices[0], dict) else None
            content = message.get("content") if isinstance(message, dict) else None
            if isinstance(content, list):
                # Some servers return content parts instead of a string.
                content = "".join(
                    str(part.get("text") or "") for part in content
                    if isinstance(part, dict))
            content = str(content or "").strip()
            if not content:
                raise OpenAICompatError("provider returned an empty message")
            return content
        if status == 429 or 500 <= status < 600:
            last_error = OpenAICompatError(
                "provider busy (HTTP {}) after {} attempt(s)".format(status, attempt + 1))
            if attempt + 1 < retries:
                time.sleep(2 ** attempt)
                continue
            break
        _raise_for_response(response, "chat completion")
    raise last_error or OpenAICompatError("chat completion failed")


def embed(texts, *, model: str | None = None, config: dict | None = None,
          timeout: float | None = None, retries: int = MAX_RETRIES,
          batch_size: int = 32) -> list[list[float]]:
    """Embed texts in one or more batches; returns one vector per input.

    Raises ``OpenAICompatError`` on any failure. Callers that treat embeddings
    as an optimisation (the default) must catch it and fall back to lexical
    similarity.
    """
    items = [str(text or "") for text in (texts or [])]
    if not items:
        return []
    settings = resolve_settings(config)
    model = str(model or settings["embedding_model"])
    base_url = settings["base_url"]
    api_key = settings["api_key"]
    timeout = _timeout(timeout or settings["timeout"])
    vectors: list[list[float]] = []
    for start in range(0, len(items), max(1, int(batch_size))):
        batch = items[start:start + batch_size]
        payload = {"model": model, "input": batch}
        response = None
        last_error = None
        for attempt in range(max(1, int(retries))):
            try:
                response = _post(base_url + "/embeddings", payload, api_key, timeout)
            except OpenAICompatError as exc:
                last_error = exc
                if attempt + 1 >= retries:
                    break
                time.sleep(2 ** attempt)
                continue
            status = int(getattr(response, "status_code", 0) or 0)
            if status == 200:
                break
            if status == 429 or 500 <= status < 600:
                last_error = OpenAICompatError(
                    "embedding endpoint busy (HTTP {})".format(status))
                if attempt + 1 < retries:
                    time.sleep(2 ** attempt)
                    continue
            _raise_for_response(response, "embedding")
        if response is None or int(getattr(response, "status_code", 0) or 0) != 200:
            raise last_error or OpenAICompatError("embedding failed")
        try:
            data = response.json()
        except Exception as exc:
            raise OpenAICompatError("embedding endpoint returned non-JSON") from exc
        entries = data.get("data") if isinstance(data, dict) else None
        if not isinstance(entries, list) or len(entries) != len(batch):
            raise OpenAICompatError(
                "embedding endpoint returned {} vectors for {} inputs".format(
                    len(entries) if isinstance(entries, list) else 0, len(batch)))
        ordered = sorted(enumerate(entries), key=lambda pair: (
            pair[1].get("index", pair[0]) if isinstance(pair[1], dict) else pair[0]))
        for _index, entry in ordered:
            vector = entry.get("embedding") if isinstance(entry, dict) else None
            if not isinstance(vector, list) or not vector:
                raise OpenAICompatError("embedding vector is malformed")
            try:
                vectors.append([float(value) for value in vector])
            except (TypeError, ValueError) as exc:
                raise OpenAICompatError("embedding vector is not numeric") from exc
    return vectors


def list_models(*, config: dict | None = None, timeout: float | None = None) -> list[str]:
    """Return the server's model ids (``/v1/models``)."""
    settings = resolve_settings(config)
    timeout = _timeout(timeout or settings["timeout"])
    if requests is None:
        raise OpenAICompatError("requests is not installed")
    url = settings["base_url"] + "/models"
    try:
        response = requests.get(url, headers=_headers(settings["api_key"]),
                                timeout=timeout)
    except requests.exceptions.RequestException as exc:
        raise OpenAICompatError(
            "could not reach {}: {}".format(url, _sanitize(exc))) from exc
    _raise_for_response(response, "model listing")
    try:
        data = response.json()
    except Exception as exc:
        raise OpenAICompatError("model listing returned non-JSON") from exc
    entries = data.get("data") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        return []
    return sorted(str(entry.get("id")) for entry in entries
                  if isinstance(entry, dict) and entry.get("id"))


def probe(config: dict | None = None) -> tuple[bool, str]:
    """Cheap connectivity check used by the settings UI / CLI diagnostics."""
    try:
        models = list_models(config=config)
    except OpenAICompatError as exc:
        return False, str(exc)
    if models:
        return True, "connected; {} model(s) available".format(len(models))
    return True, "connected; server returned no model list"


def cosine(left, right) -> float:
    """Cosine similarity of two numeric vectors; 0.0 on unusable input."""
    try:
        if not left or not right or len(left) != len(right):
            return 0.0
        dot = sum(float(a) * float(b) for a, b in zip(left, right, strict=False))
        norm_a = sum(float(a) * float(a) for a in left) ** 0.5
        norm_b = sum(float(b) * float(b) for b in right) ** 0.5
        if norm_a <= 0.0 or norm_b <= 0.0:
            return 0.0
        return max(-1.0, min(1.0, dot / (norm_a * norm_b)))
    except (TypeError, ValueError, OverflowError):
        return 0.0


def dumps_for_log(value) -> str:
    """JSON helper that never raises on odd provider payloads."""
    try:
        return json.dumps(value, ensure_ascii=False)[:500]
    except (TypeError, ValueError):
        return "<unserializable>"
