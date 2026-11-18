# CHANGES — fix/audit-issues (2026-11-17)

Branch: `fix/audit-issues` (forked from `main` @ d3fb45b). Six commits, one per task.

## Task 1 — Android no longer fabricates results (cd40960)
- `app/src/main/java/com/example/oussamacutter/data/GeminiService.kt`: added `sealed class AnalysisResult { Success, NoApiKey, ApiError, InvalidInput }`; `analyzeAndGenerateViralClips` returns it instead of silently faking clips. URL input → `InvalidInput`; missing key → `NoApiKey`; API/parse failures → `ApiError` with a clear Arabic message. Deleted `generateLocalLinguisticClips` (fake scores 90+, fixed entrepreneurship transcript, fake hooks). Gemini fields missing `viralScore`/`hookStrength` now parse as `null`; missing `transcript` rejects the whole response as `ApiError`. Removed constant `audioLoudnessLUFS = -14.1`.
- `app/src/main/java/com/example/oussamacutter/model/Models.kt`: `ClipSegment.viralScore`/`hookStrength` are now `Int? = null`, `audioLoudnessLUFS` is `Double? = null`.
- `app/src/main/java/com/example/oussamacutter/data/ProjectRepository.kt`: `runRealAiVideoCuttingPipeline` now returns `Boolean`, maps every failure branch to a job status «فشل» + Arabic message, and no longer overwrites LUFS with `-14.0`; the demo `createClip` no longer sets a fake LUFS.
- `app/src/main/java/com/example/oussamacutter/ui/screens/CreateScreen.kt`: shows the Arabic failure message inline and does not navigate on failure.
- `HomeScreen.kt`, `ReviewScreen.kt`, `PublishScreen.kt`: missing scores display «غير متاح».
- `app/src/test/java/com/example/oussamacutter/data/SafetyEngineTest.kt`: added tests for the no-key case (`NoApiKey`) and URL input (`InvalidInput`).
- `app/build.gradle.kts`: `testOptions.unitTests.isReturnDefaultValues = true` so JVM tests can call `android.util.Log`.

## Task 2 — Docker works again (f92a58b)
- `docker-compose.yml`: `VIRALCUTTER_WEBUI_USER`/`VIRALCUTTER_WEBUI_PASSWORD` are required (`${VAR:?set in .env}` — compose fails fast with a clear message); healthcheck now accepts **200 or 401**.
- `Dockerfile`: multi-stage build (gcc/g++/make/python3-dev only in the builder; final image installs from prebuilt wheels), `requirements-dev.txt` removed from the final image, non-root `USER app`.
- `healthcheck.py` (new): shared probe — server is "up" if it answers 200 or 401 (401 = auth enabled, still proves liveness).
- `.env.example`: marks the two auth variables as required for Docker.

## Task 3 — Custom-route auth on Windows (bbf36b3)
Verified with a real probe (`tests/test_custom_route_auth.py`): before the fix, `GET /virals/secret.txt` without credentials returned **HTTP 200 with the file contents** — Gradio's `auth=` does not cover routes attached after `launch()`. Fix: extracted `_install_basic_auth(app, auth)` (pure-ASGI middleware, attachable post-startup) and installed it in **both** branches before `attach_extra_routes`. Verified: no-creds → 401, valid creds → 200.

## Task 4 — Auth hardening (d7ead7a)
- Credential comparison now uses `hmac.compare_digest` on UTF-8 bytes for both user and password.
- Per-IP failure throttle: 5 failed attempts within 60 s → `429` with `Retry-After` (in-process dict, lazily pruned).

## Task 5 — `/export_xml_api` contract (0619171)
- `webui/app.py`: proper status codes via `JSONResponse` — 400 (empty project / unknown format), 404 (unknown project or segment), 500 (exporter unavailable / internal failure); `segment` bounded by `Query(ge=0, le=9999)`; `format` validated against `_SUPPORTED_EXPORT_FORMATS`; internal exceptions logged with `logging` and replaced with generic messages (no `str(e)` leakage).
- `scripts/export_xml_lib/exporter.py`: `export_pack` validates `output_format` against `SUPPORTED_EXPORT_FORMATS = ("premiere",)` (the only format the code actually implements) and raises `ValueError` otherwise — the parameter now has real effect.
- `scripts/export_xml.py`: CLI `--format` restricted to `choices=["premiere"]`.
- `tests/test_export_xml_api.py` (new): launches the real WebUI (authenticated, loopback) and asserts 400/404/422 contracts plus no-traceback leakage.

## Task 6 — Quick cleanup (7b551ca)
- `app/build.gradle.kts`: `versionCode = 2`, `versionName`/`APP_VERSION` = `8.10.1-ultimate` (matches `app_version.py`).
- `main_improved.py`: the five specified `except Exception: pass` blocks (lines 31, 302, 307, 686, 1100) now log via `logging.debug`/`logging.warning`.
- `webui/subtitle_handler.py`, `webui/subtitle_editor.py`: `print(` → `logging` (commented-out debug prints left as-is).
- `requirements.txt`: lower bounds added — `fastapi>=0.115`, `uvicorn>=0.30`, `cryptography>=42.0`; Portuguese comment replaced.

## Deliberately left untouched
- Fake demo fixtures in `ProjectRepository.addBatchCutJob` (lines ~441) and placeholder demo clips — sample data, out of the audit scope.
- Word-timing approximation (500 ms/word) in `GeminiService.buildClipFromRealData`: the fix list did not request removing it, and the subtitle/SRT editor depends on word timings; making them nullable would ripple into `generateSrtSubtitles`. Flagged for a future decision.
- Other `print()` / `except: pass` occurrences outside the exact files/lines specified.

## Test results (this machine, Python 3.12, gradio 6.32)
- `python -m compileall -q .` → clean.
- `pytest -q` → **2491 passed, 2 skipped, 0 failed** (the one nominally failing packaging test passes once the `build` package is installed, as CI does via `requirements-dev.txt`; heavy deps like torch/mediapipe/insightface were not needed).

## التحقق عبر uv (بعد اكتمال الفرع)
- `uv sync --extra dev` → نجح (uv.lock يعمل).
- `uv run ruff check .` → All checks passed.
- `uv run pytest -q` → 2495 اختباراً: 2494 ناجحاً، فشل 1، تخطي 2.
  الفشل: `tests/test_ai_autorouter.py::test_detect_does_not_probe_the_same_url_twice`.
  السبب: `pyproject.toml` يدرج g4f في dev extra، فتُضاف مرشحة g4f الاحتياطية إلى
  `ai_autorouter.detect()` بينما الاختبار يفترض غيابها. CI لا يتأثر لأن
  `requirements-dev.txt` لا يثبت g4f. الفشل موجود على `main` أيضاً تحت نفس
  البيئة (مُتحقق منه) — ليس من هذا الفرع، والمعالجة خارج نطاقه.
