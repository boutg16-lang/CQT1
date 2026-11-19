"""Resolve which runtime backend/device a run should use.

Extracted verbatim from ``main_improved.py`` (monolith split, step 5).
Pure resolution helpers: string/environment in, structured value out —
no I/O, no prints, no global state. ``torch`` is imported lazily inside
``describe_transcription_device`` so this module stays stdlib-only at
import time. The original module re-exports these names so existing
import paths keep working unchanged.
"""

import os


def describe_transcription_device(requested):
    """Resolve the actual transcription device for the live WebUI status."""
    requested = str(requested or "auto").strip().lower()
    if requested not in {"auto", "cpu", "cuda"}:
        requested = "auto"
    try:
        import torch
        cuda_available = bool(torch.cuda.is_available())
        if requested == "cuda" and not cuda_available:
            return {"actual": "unavailable", "requested": requested, "name": "", "message": "CUDA مطلوبة لكن بطاقة NVIDIA غير متاحة"}
        actual = "cuda" if requested == "cuda" or (requested == "auto" and cuda_available) else "cpu"
        name = ""
        if actual == "cuda":
            try:
                name = torch.cuda.get_device_name(0)
            except Exception:
                name = "NVIDIA GPU"
        label = "NVIDIA GPU / CUDA" if actual == "cuda" else "CPU"
        detail = " — {}".format(name) if name else ""
        return {"actual": actual, "requested": requested, "name": name, "message": "يعمل الآن بواسطة {}{}".format(label, detail)}
    except Exception:
        return {"actual": "cpu", "requested": requested, "name": "", "message": "يعمل الآن بواسطة CPU (Torch/CUDA غير قابل للفحص)"}


def resolve_safety_backend(requested, ai_backend, api_key=None):
    """Choose the contextual safety reviewer without changing viral analysis."""
    requested = str(requested or "auto").strip().lower()
    openai_key = os.getenv("OPENAI_API_KEY", "").strip() or os.getenv("OPENAI_MODERATION_API_KEY", "").strip()
    gemini_key = str(api_key or "").strip() if ai_backend == "gemini" else os.getenv("GEMINI_API_KEY", "").strip()

    if requested == "auto":
        # A custom OpenAI-compatible server (Ollama/LM Studio/OpenRouter) is
        # not guaranteed to expose /moderations, so those backends never
        # auto-select it. A plain OPENAI_API_KEY still does for manual/gemini/
        # g4f runs, preserving the documented behaviour.
        if ai_backend in {"openai", "auto"}:
            if ai_backend == "auto" and (gemini_key or api_key):
                return "gemini", gemini_key or api_key
            return "local", None
        if openai_key:
            return "openai-moderation", openai_key
        if ai_backend in {"gemini", "g4f"}:
            return ai_backend, gemini_key or api_key
        if gemini_key:
            return "gemini", gemini_key
        return "local", None
    if requested == "openai-moderation":
        return requested, openai_key or (api_key if ai_backend == "openai-moderation" else None)
    if requested == "gemini":
        return requested, gemini_key or (api_key if ai_backend == "gemini" else None)
    if requested == "g4f":
        return requested, api_key
    return "local", None
