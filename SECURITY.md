# Security Policy

ViralCutter is a local-first desktop app, not an offline-only app. Video and audio processing runs on your machine, but selected or configured online features send the data needed for that task to external services. Do not assume that every workflow keeps all data on-device.

## Network and data flow

Known network-enabled paths include:

- **Cloud AI and contextual safety review:** send the prompt and relevant transcript/title text to the selected provider; these code paths send text, not the source video. Local models keep this request on-device when their endpoint is local.
- **Microsoft Edge TTS:** sends subtitle text to Microsoft and is disabled unless network use is explicitly enabled with `VIRALCUTTER_TTS_ALLOW_NETWORK=1`.
- **Publishing:** sends the rendered clip and its title/caption/hashtags to the selected platform. Instagram may temporarily host the clip on a public file host when no `IG_VIDEO_URL` is supplied; set `IG_HOST_DISABLE=1` and provide your own URL to prevent that fallback.
- **Pexels B-roll:** sends the search query to Pexels and downloads the selected stock footage when configured.
- **Updates and analytics:** update/word-list checks contact their configured providers; the optional read-only channel analytics feature calls Google APIs.

Review the selected provider's terms before sending sensitive material. Features and their network behavior can change; this list describes the current implementation, not a guarantee that the app is entirely offline.

## Reporting a vulnerability

Please do **not** open a public issue for security problems. Report privately:

- GitHub: use the repo's "Report a vulnerability" (Security tab), or
- Open a **private** issue, or contact the maintainers via the Discord
  community link in the README.

We'll acknowledge within 5 business days and work on a fix before disclosure.

## Scope

- The YouTube-strike safety layer (`scripts/safety_filter.py`,
  `scripts/censor_engine.py`, `scripts/risk_scorecard.py`,
  `scripts/safety_updater.py`) — weaknesses that could let violating content
  through are top priority.
- Credential handling (`api_config.json`, `secure_config.py`, OAuth tokens in
  `~/.viralcutter/`).
- The pre-flight checker (`scripts/preflight.py`) — anything that could make
  it silently install the wrong thing.

## Out of scope

- The AI providers' own services (Gemini/OpenAI) and their rate limits.
- Known platform API limitations (e.g. Instagram Reels requiring a public
  video URL — documented in `requirements-upload.txt`).
