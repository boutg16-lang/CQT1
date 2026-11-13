# -*- coding: utf-8 -*-
"""ai_autorouter — pick a working LLM automatically and fail over.

«Auto» means the operator should not have to know which provider is
installed. The router probes, in order:

1. OpenAI-compatible servers (Ollama / LM Studio / vLLM / llama.cpp /
   OpenRouter / Groq) — the configured URL, then env, then the local default.
2. Gemini, when a key is resolvable.
3. g4f, when the package is installed.

Local servers rank before remote ones (privacy + no cost), and a model is
chosen from the server's own ``/v1/models`` list with a conservative size cap
so an auto-run never tries to load a 70B model on a laptop. During a run the
router fails over to the next provider when one dies; it never switches for a
single transient error that the provider's own retry already handled.
"""

from __future__ import annotations

import importlib.util
import os
import re
from dataclasses import dataclass, field

from scripts import openai_compat

try:
    from scripts.gemini_models import DEFAULT_GEMINI_MODEL, resolve_gemini_model
except Exception:  # pragma: no cover
    DEFAULT_GEMINI_MODEL = "gemini-3.5-flash-lite"
    resolve_gemini_model = lambda value: str(value or DEFAULT_GEMINI_MODEL)  # noqa: E731

PROBE_TIMEOUT = 2.0
DEFAULT_MAX_PARAMS_B = 14.0  # conservative laptop/consumer-GPU ceiling

_EMBEDDING_NAME_RE = re.compile(
    r"(embed|bge|gte|e5[-_]|nomic|mxbai|paraphrase|rerank|all-minilm)", re.IGNORECASE)
_SIZE_RE = re.compile(r"(?<![a-z0-9])(\d+(?:\.\d+)?)\s*b(?![a-z])", re.IGNORECASE)
_MOE_RE = re.compile(r"(\d+)\s*x\s*(\d+(?:\.\d+)?)\s*b", re.IGNORECASE)

_FAMILY_BONUS = {
    "qwen2.5": 40, "qwen3": 45, "qwen2": 35,
    "llama3.3": 42, "llama3.1": 38, "llama3": 34,
    "gpt-oss": 44, "mistral-nemo": 30, "mistral": 26,
    "gemma3": 32, "gemma2": 28, "deepseek-r1": 36,
    "phi4": 27, "yi": 24, "command-r": 25,
}


@dataclass
class Candidate:
    backend: str                 # "openai" | "gemini" | "g4f"
    model: str = ""
    base_url: str = ""
    label: str = ""
    local: bool = False
    chunk_size: int = 12000
    config: dict = field(default_factory=dict)

    def describe(self) -> str:
        if self.backend == "openai":
            return "{} ({}{})".format(self.model or "model",
                                      "local " if self.local else "",
                                      self.base_url)
        if self.backend == "gemini":
            return "Gemini / {}".format(self.model)
        return "g4f / {}".format(self.model or "default")


def _is_local_url(url: str) -> bool:
    return bool(re.search(r"^https?://(localhost|127\.0\.0\.1|\[::1\]|0\.0\.0\.0)(:|\/)",
                          str(url or ""), re.IGNORECASE))


def _max_params() -> float:
    try:
        value = float(os.getenv("VIRALCUTTER_AUTO_MAX_PARAMS", ""))
    except (TypeError, ValueError):
        return DEFAULT_MAX_PARAMS_B
    return value if 1.0 <= value <= 1000.0 else DEFAULT_MAX_PARAMS_B


def _model_params(name: str) -> float | None:
    moe = _MOE_RE.search(name)
    if moe:
        try:
            return float(moe.group(1)) * float(moe.group(2))
        except ValueError:
            return None
    match = _SIZE_RE.search(name)
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            return None
    return None


def model_score(name: str, cap: float | None = None) -> float:
    """Rank a model id for automatic selection (higher is better)."""
    text = str(name or "").strip()
    if not text or _EMBEDDING_NAME_RE.search(text):
        return float("-inf")
    cap = _max_params() if cap is None else cap
    params = _model_params(text)
    if params is None:
        effective = cap
    elif params <= cap:
        effective = params
    else:
        # An oversized model may not fit the machine: prefer a smaller one
        # rather than always picking the biggest id the server lists.
        effective = max(1.0, cap - (params - cap) * 0.25)
    bonus = 0.0
    lowered = text.lower()
    for family, value in _FAMILY_BONUS.items():
        if family in lowered:
            bonus = max(bonus, float(value))
            break
    # Instruction-tuned / chat variants are preferred over base models.
    if re.search(r"(instruct|chat|it\b)", lowered):
        bonus += 4.0
    if re.search(r"(vision|vl\b|audio|tts)", lowered):
        bonus -= 6.0
    return bonus * 100.0 + effective


def select_model(models, cap: float | None = None) -> str:
    ranked = sorted((model for model in models or [] if model),
                    key=lambda name: model_score(name, cap), reverse=True)
    return ranked[0] if ranked else ""


def _config_for(base_url: str, config: dict | None) -> dict:
    payload = dict(config or {})
    section = dict(payload.get("openai") or {})
    section["base_url"] = base_url
    payload["openai"] = section
    return payload


def _url_key(url: str) -> str:
    """Canonical probe key: localhost aliases are the same server."""
    text = str(url or "").strip().rstrip("/")
    match = re.match(r"^(https?)://([^/:]+)(?::(\d+))?(/.*)?$", text,
                     flags=re.IGNORECASE)
    if not match:
        return text
    scheme, host, port, path = match.groups()
    host = host.lower()
    if host in {"localhost", "127.0.0.1", "0.0.0.0", "::1", "[::1]"}:
        host = "localhost"
    return "{}://{}{}{}".format(scheme.lower(), host,
                                ":" + port if port else "", path or "")


def _openai_candidates(config: dict | None, timeout: float) -> list[Candidate]:
    section = (config or {}).get("openai") if isinstance(config, dict) else {}
    section = section if isinstance(section, dict) else {}
    explicit = []
    for value in (
        os.getenv("VIRALCUTTER_OPENAI_BASE_URL", "").strip(),
        os.getenv("OPENAI_BASE_URL", "").strip(),
        str(section.get("base_url") or "").strip(),
    ):
        if value and value.rstrip("/") not in {url.rstrip("/") for url in explicit}:
            explicit.append(value)
    # Probe the local default only when the operator named no endpoint:
    # an explicit OpenRouter/Groq URL must not be silently outranked by an
    # Ollama instance that happens to be running on the same machine.
    urls = explicit or [openai_compat.DEFAULT_BASE_URL]
    key = (os.getenv("VIRALCUTTER_OPENAI_KEY", "").strip()
           or os.getenv("OPENAI_API_KEY", "").strip()
           or str(section.get("api_key") or "").strip())
    candidates = []
    seen_urls = set()
    for url in urls:
        try:
            clean_url = openai_compat._clean_base_url(url)
        except openai_compat.OpenAICompatError:
            continue
        url_key = _url_key(clean_url)
        if url_key in seen_urls:
            continue
        seen_urls.add(url_key)
        cfg = _config_for(clean_url, config)
        cfg["openai"]["api_key"] = key
        try:
            models = openai_compat.list_models(
                config=cfg, timeout=timeout, base_url=clean_url, api_key=key)
        except openai_compat.OpenAICompatError:
            continue
        # The configured model wins when it exists on this server; otherwise
        # rank the server's own list.
        configured = str(section.get("model") or "").strip()
        if (configured and configured in models
                and not _EMBEDDING_NAME_RE.search(configured)):
            model = configured
        else:
            model = select_model(models)
        if not model:
            continue
        candidates.append(Candidate(
            backend="openai", model=model, base_url=clean_url,
            label="OpenAI-compatible", local=_is_local_url(clean_url),
            chunk_size=int(section.get("chunk_size") or 12000),
            config=cfg,
        ))
    return candidates


def _gemini_candidate(config: dict | None) -> Candidate | None:
    try:
        from scripts.secure_config import resolve_api_keys
        keys = resolve_api_keys(warn=False)
    except Exception:
        keys = []
    if not keys:
        return None
    section = (config or {}).get("gemini") if isinstance(config, dict) else {}
    section = section if isinstance(section, dict) else {}
    model = resolve_gemini_model(section.get("model") or DEFAULT_GEMINI_MODEL)
    return Candidate(
        backend="gemini", model=model, label="Gemini",
        chunk_size=int(section.get("chunk_size") or 20000),
        config={"gemini": {"api_key": keys[0], "api_keys": keys,
                           "model": model}},
    )


def _g4f_candidate(config: dict | None) -> Candidate | None:
    if importlib.util.find_spec("g4f") is None:
        return None
    section = (config or {}).get("g4f") if isinstance(config, dict) else {}
    section = section if isinstance(section, dict) else {}
    return Candidate(
        backend="g4f", model=str(section.get("model") or "gpt-4o-mini"),
        label="g4f", chunk_size=int(section.get("chunk_size") or 2000),
        config={"g4f": section},
    )


def detect(config: dict | None = None, *, prefer: str = "",
           timeout: float = PROBE_TIMEOUT) -> list[Candidate]:
    """Return every usable provider, best first. Never raises."""
    candidates = []
    try:
        candidates.extend(_openai_candidates(config, timeout))
    except Exception:
        pass
    try:
        gemini = _gemini_candidate(config)
        if gemini:
            candidates.append(gemini)
    except Exception:
        pass
    try:
        g4f = _g4f_candidate(config)
        if g4f:
            candidates.append(g4f)
    except Exception:
        pass

    # Local first, then remote, then Gemini, then g4f. Stable within groups.
    def rank(candidate: Candidate):
        if candidate.backend == "openai":
            return 0 if candidate.local else 1
        return 2 if candidate.backend == "gemini" else 3

    candidates.sort(key=rank)
    prefer = str(prefer or "").strip().lower()
    if prefer:
        preferred = [candidate for candidate in candidates
                     if candidate.backend == prefer]
        if preferred:
            candidates = preferred + [candidate for candidate in candidates
                                      if candidate.backend != prefer]
    return candidates


def describe(candidates) -> str:
    if not candidates:
        return "no automatic provider available"
    return " -> ".join(candidate.describe() for candidate in candidates)


class AutoRouter:
    """Try the primary provider, fail over down the list, remember the win."""

    def __init__(self, candidates, callbacks):
        self.candidates = list(candidates or [])
        self.callbacks = dict(callbacks or {})
        self.used = None
        self.errors = []

    @property
    def chunk_size(self) -> int:
        source = self.used or (self.candidates[0] if self.candidates else None)
        return int(source.chunk_size) if source else 12000

    @property
    def model(self) -> str:
        source = self.used or (self.candidates[0] if self.candidates else None)
        return source.model if source else ""

    def chat(self, prompt: str) -> str:
        if not self.candidates:
            raise RuntimeError("no AI provider is available for automatic mode")
        # Put the last successful provider first on subsequent chunks.
        ordered = list(self.candidates)
        if self.used in ordered:
            ordered.remove(self.used)
            ordered.insert(0, self.used)
        for candidate in ordered:
            callback = self.callbacks.get(candidate.backend)
            if callback is None:
                continue
            try:
                output = callback(candidate, prompt)
            except Exception as exc:
                self.errors.append("{}: {}".format(
                    candidate.describe(), type(exc).__name__))
                continue
            text = str(output or "").strip()
            if not text or text == "{}":
                self.errors.append("{}: empty response".format(
                    candidate.describe()))
                continue
            self.used = candidate
            return text
        detail = "; ".join(self.errors[-4:]) or "all providers returned nothing"
        raise RuntimeError("automatic AI routing failed: " + detail)


def embedding_model_for(candidates) -> str:
    """Best available embedding model on the selected OpenAI-compatible server."""
    for candidate in candidates:
        if candidate.backend != "openai":
            continue
        try:
            models = openai_compat.list_models(config=candidate.config, timeout=PROBE_TIMEOUT)
        except openai_compat.OpenAICompatError:
            continue
        matches = [model for model in models if _EMBEDDING_NAME_RE.search(model)]
        if matches:
            return sorted(matches, key=len, reverse=True)[0]
    return ""
