# Supported Gemini text models

Last verified: 2026-10-17 against Google's official model catalogue and deprecation page.

`scripts/gemini_models.py` is the single allowlist for the CLI, WebUI, preflight defaults, and AI safety review. The default is `gemini-3.5-flash-lite`; supported text-generation IDs are:

- `gemini-3.5-flash-lite` (default)
- `gemini-3.8-flash`
- `gemini-3.6-flash`
- `gemini-3.1-flash-lite`
- `gemini-2.5-flash`
- `gemini-2.5-flash-lite`
- `gemini-2.5-pro`

The Gemini 2.5 IDs remain for users who already have access; Google's catalogue notes access restrictions for new users. The allowlist intentionally excludes preview, image-generation, audio/TTS, and retired IDs because this pipeline uses text generation. Old saved model IDs resolve to the default and produce a warning. A provider HTTP 404 for a model reports the recovery path without exposing the API key.

Check the official catalogue and deprecations before changing the list:

- https://ai.google.dev/gemini-api/docs/models
- https://ai.google.dev/gemini-api/docs/deprecations
