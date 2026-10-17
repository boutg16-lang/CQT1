"""Supported Gemini text-generation models for configuration and the UI."""

DEFAULT_GEMINI_MODEL = "gemini-3.5-flash-lite"

GEMINI_MODELS = (
    DEFAULT_GEMINI_MODEL,
    "gemini-3.8-flash",
    "gemini-3.6-flash",
    "gemini-3.1-flash-lite",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-2.5-pro",
)


def resolve_gemini_model(model_name):
    """Keep configured Gemini models on the supported text-model allowlist."""
    model = str(model_name or "").strip()
    return model if model in GEMINI_MODELS else DEFAULT_GEMINI_MODEL
