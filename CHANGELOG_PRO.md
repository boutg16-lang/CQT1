# OUSSAMA Cutter 7.52.0-pro candidate

## Local browser pairing reliability — 2026-10-07

- The local browser tester identifies CAT through a loopback health marker, verifies the exact CAT page title for compatibility, and discovers the actual WebUI port in the launcher’s `7860–7879` range. The resolved URL is shown before pairing; a failed check does not consume the one-time pairing code.
- Added regression tests for the health endpoint, fallback-port discovery, and rejection of unrelated local services.

# ViralCutter 7.0.1-pro

## Advanced Production & Retention Upgrades — Aug 2026

- **Dynamic Captions (الترجمات الحركية):** تأثيرات ASS حركية متوقتة على الكلمات (`pop`, `scale`, `pop_scale`, `bounce`) لزيادة معدل الاحتفاظ بالمشاهد (Retention).
- **Auto-Emoji (الإيموجي التلقائي):** إدراج رموز تعبيرية متحفظة تلقائياً للكلمات المفتاحية والعاطفية الداعمة.
- **Active Speaker Switching (تبديل المتحدث النشط):** خوارزمية hysteresis وhold-time ذكية لمنع القفز المزعج بين المتحدثين في المقابلات الثنائية وضمان استقرار الانتقال.
- **تكامل الواجهة والـ CLI:** إتاحة التحكم الكامل في هذه الميزات من تبويب **Subtitle Editor** ومن سطر الأوامر عبر `--caption-animation` و`--auto-emoji` و`--focus-active-speaker`.

## Security hardening (review pass — Aug 2026)

- **WebUI no longer serves the repo root.** Gradio `allowed_paths`/static
  mounts are restricted to `VIRALS/` (extras via `VIRALCUTTER_EXTRA_STATIC_DIRS`)
  so `api_config.json`, crash logs and OAuth tokens can no longer be fetched
  over `/file/...`.
- **Loopback by default.** The WebUI now binds `127.0.0.1` unless
  `VIRALCUTTER_HOST` is set, and prints a warning when binding to the
  network. Optional HTTP basic auth via `VIRALCUTTER_WEBUI_USER` /
  `VIRALCUTTER_WEBUI_PASSWORD` (both Gradio-launch and Uvicorn paths).
- **`/export_xml_api` path containment.** The `project` parameter is
  basenamed and validated to stay inside `VIRALS/` (no more `../` escape).
- **Gallery XSS fixed.** All user-derived strings (titles, scores, file
  names, errors) are HTML-escaped; absolute-path `/file/` URLs are no
  longer emitted for files outside the allowed static dirs.
- **Gemini key pass-through restored (env, not argv).** The pro build
  removed `--api-key` from child-process argv but never delivered the key
  to the CLI; the WebUI now injects it via `VIRALCUTTER_GEMINI_KEY` in the
  child environment (never clobbering an explicit user export).
- **Encrypted credential storage preferred by the WebUI.** When
  `VIRALCUTTER_CONFIG_PASSPHRASE` is set, saved keys go to the encrypted
  store and the plaintext `api_config.json` stays clean.
- **`api_config.json` is now gitignored** (real keys must not be committed);
  `api_config.example.json` is provided instead. Existing installs keep
  their file; new clones start from defaults.
- **Auto-updater verifies downloads.** Release assets must match a
  published `checksums.txt`/`SHA256SUMS` manifest before being installed;
  updates without a manifest are refused unless
  `VIRALCUTTER_ALLOW_UNSIGNED_UPDATE=1`. The blind "grab the first asset"
  fallback was removed.
- **`torch.load` no longer disables PyTorch's guard globally.** It first
  loads with `weights_only=True` + registered safe globals; the legacy
  `weights_only=False` path requires an explicit
  `VIRALCUTTER_ALLOW_UNSAFE_LOAD=1` opt-in.
- **ffmpeg pipe failures are no longer silent.** `generate_short_fallback`
  detects a dead encoder, surfaces the ffmpeg stderr tail and raises
  instead of finalizing a truncated clip.

## Reliability

- `main_improved.py` loads the API config unconditionally (fixes the
  resume-path `NameError` where `--ai-backend` was silently ignored when
  `viral_segments.txt` already existed).
- The pipeline no longer re-runs the whole job on user-input errors
  (`ValueError`/`TypeError` e.g. malformed `--chunk-size`).
- `--chunk-size` parsing is defensive (`_safe_chunk_size`) instead of
  crashing the run.
- Subtitle filter paths escape `'` and `:` for the ffmpeg filtergraph.
- `tests/test_preflight.py` numpy pin test is deterministic (no longer
  depends on the ambient numpy version).

# ViralCutter 7.0.0-pro

## Production hardening
- Added dependency-aware Pipeline Engine with atomic state and cancellation.
- Added non-destructive Professional Editor state with validation and undo/redo.
- Added persistent Render Queue with crash recovery of interrupted jobs.
- Removed insecure XOR credential fallback; secure credential storage now fails closed.
- Secure credential writes are atomic and use restrictive file permissions where supported.
- WebUI no longer places API keys in process arguments.
- Added regression-testable building blocks for Editor, Queue, Pipeline and credential security.

## Compatibility
- Existing `checkpoint.json` remains supported.
- Existing WebUI/CLI entry points are preserved.


# ViralCutter 7.0.1+pro — Production workflow upgrade — 12 أغسطس 2026

تم تحويل المشروع إلى مسار تشغيل أكثر ملاءمة للعمل اليومي مع الحفاظ على بنية المشاريع القديمة. أضيفت طبقات للحفظ الآمن، التتبع، التحقق، الطوابير، والتحرير دون كسر نقاط التشغيل الحالية.

| المجال | التحسين المنفذ |
|---|---|
| التشغيل | دعم تشغيل WebUI كملف مباشر أو كحزمة Python، وإضافة `viralcutter` و`viralcutter-webui`. |
| المشاريع | `project_manifest.json` و`project_events.jsonl` لحفظ المصدر والإعدادات والحالة والمخرجات وسجل الأحداث. |
| الطوابير | Worker دائم، تقدم، إلغاء، إعادة محاولة، استرجاع بعد التعطل، وتنظيف تلقائي للمهام القديمة. |
| الإعدادات | استرجاع آمن من JSON تالف، التحقق من backend وchunk size، وحفظ ذري مع `fsync`. |
| الذكاء الاصطناعي | تفضيل `google-genai` مع fallback مضبوط، وتحمل ردود Gemini داخل Markdown fences. |
| التفريغ | تحقق من SRT/TSV قبل إنشاء المقاطع، مع رسائل واضحة عند غياب التوقيت أو النص. |
| الترجمة | إصلاح import asyncio، وإعادة محاولة محدودة بدل حلقة لا نهائية عند تعطل الخدمة. |
| التحرير | تحقق من حدود transform وrollback عند الخطأ، حفظ ذري لملفات subtitles، وتحسين rounding للتوقيت. |
| الفيديو | تحقق ffprobe من وجود streams وحجم الملف والمدة وaspect ratio؛ واختبار فعلي لإعادة الإطار إلى 9:16. |
| الأمان | منع Path Traversal في مسارات المشاريع، والتحقق من روابط YouTube في Batch Queue. |
| التشخيص | Doctor يعرض إصدار FFmpeg ومساحة القرص الحرة، مع توافق أفضل مع Python 3.15. |

## التحقق النهائي

تم تشغيل `ruff check .` و`python -m compileall -q .` و`pytest -q` و`python -m webui.app --help` و`python main_improved.py --help`، كما تم تنفيذ `python -m pip install --no-deps -e .` واختبار الأمرين `viralcutter` و`viralcutter-webui`. بعد إضافة تكامل YouTube واختباراته، اكتملت مجموعة الاختبارات الحالية بنجاح، مع نجاح فحص التكامل الحقيقي الخاص بـ FFmpeg وإعادة الإطار والصوت.

## YouTube API v3 — الرفع المباشر والجدولة — 12 أغسطس 2026

- أضيفت خانة إرفاق **Google OAuth client-secrets JSON** في Publish مع تحقق من بنية الملف ونسخه إلى مخزن خاص بصلاحيات مقيدة.
- أضيف OAuth refresh-token storage ذري إلى `~/.viralcutter/youtube/token.json` مع محاولة تثبيت mode `600` وعدم تسجيل أسرار OAuth في سجل النشر.
- أضيف رفع YouTube Data API v3 حقيقي بعد عبور Upload Gate، مع عنوان، وصف، هاشتاغات مطبّعة، `categoryId` قابل للتخصيص، و`privacyStatus` بقيم `private`/`unlisted`/`public`.
- أضيفت الجدولة عبر `publishAt` بصيغة ISO 8601 مع timezone؛ التوقيت يجب أن يكون مستقبليًا، والجدولة تُرفض ما لم تكن الخصوصية `private`.
- أضيف `publish_history.jsonl` لكل مشروع لتسجيل النجاح والفشل والحالة وVideo ID دون client secrets أو refresh tokens.
- أضيف خيار **Full YouTube OAuth access** المتقدم في WebUI و`--full-youtube-access` في CLI؛ النطاق الآمن `youtube.upload` هو الافتراضي.
- بقي Dry Run مفعّلًا افتراضيًا في الواجهة، وتبقى فحوصات blocklist وsafety report وmetadata والموسيقى قبل أي طلب API.

## التشغيل

```bash
python -m pip install -r requirements.txt
python -m scripts.preflight --auto-fix
python -m webui.app
```

أو بعد التثبيت:

```bash
viralcutter-webui
viralcutter --help
```

WhisperX وTorch وGPU اختيارية لأنها تعتمد على نظام التشغيل وبنية CUDA. أما النشر المباشر إلى المنصات الاجتماعية فيحتاج إعداد OAuth منفصلًا؛ النسخة الحالية توفر التصدير والمعاينة والتحقق وmetadata وdry-run دون تخزين بيانات النشر تلقائيًا.


## ViralCutter 7.1.0-pro — AI editing extensions — 14 أغسطس 2026

### تحرير متعدد الوجوه

- وسّع `scripts/two_face.py` التخطيط من وجهين إلى شبكة portrait تصل إلى أربعة وجوه، مع أوضاع `vertical` و`grid` و`speaker` وclamping للحدود.
- يدعم `--face-mode` الآن `3` و`4` و`multi` و`grid`، مع ترتيب left-to-right مستقر وتوافق مع MediaPipe fallback.
- أضيفت اختبارات أبعاد الناتج، boxes خارج الإطار، التخطيط الشبكي، وتوافق دالة الوجهين القديمة.
- أصلح fallback InsightFace التقاط أخطاء native dependencies مثل عدم توافق NumPy/ONNX بدل إسقاط WebUI بالكامل.

### B-Roll اختياري

- أضيف `scripts/broll_engine.py` لاستخراج الكلمات المفتاحية، إنشاء خطة زمنية، البحث الاختياري عبر Pexels Video API، تنزيل الأصول بحد حجم، وإدراجها منخفضة الشفافية عبر FFmpeg.
- لا توجد مكالمات شبكة دون `PEXELS_API_KEY`؛ يحتفظ التقرير ببيانات Pexels attribution وrate-limit metadata، ويدعم أصلًا محليًا عبر `--broll`.
- أضيفت حقول B-Roll إلى WebUI وتمريريها إلى CLI دون وضع أي مفتاح API داخل argv.

### Visual Hooks وAuto SFX

- أضيف `scripts/visual_hooks.py` لاكتشاف كلمات hook والعاطفة وإنشاء تأثير accent/brightness زمني خفيف.
- أضيف `scripts/auto_sfx.py` لمزامنة `pop` و`whoosh` و`impact` المحلية مع word timings عبر `adelay` و`amix`، مع تخطي آمن عند غياب الأصول.
- أضيفت إعدادات WebUI وCLI لمجلد SFX ومستوى الصوت، وأصبحت مراحل `visual_hooks` و`auto_sfx` ضمن مسار polish الاختياري الآمن.

### التحقق

- نجحت اختبارات multi-face وB-Roll وVisual Hooks وAuto SFX وpolish وpipeline المستهدفة.
- نجح `ruff` و`compileall` على الوحدات المعدلة.
- الاختبارات لا تنفذ Pexels أو رفعًا خارجيًا حقيقيًا؛ الشبكة وOAuth يظلان opt-in ويتطلبان مفاتيح المستخدم.

## ViralCutter 7.2.0-pro — Ultimate Safety & Audio Intelligence — 14 أغسطس 2026

### الأمان الدلالي والرقابة الذكية (Semantic Safety)
- أضيف `scripts/semantic_safety.py`: محرك تحليل دلالي متطور يكتشف خطاب الكراهية والتحريض سياقياً (عربي/إنجليزي) دون الاعتماد فقط على الكلمات المحظورة.
- أضيف وضع `Auto-Censor`: إمكانية تشويش الكلمات الحساسة مع الحظر التلقائي للمقاطع التي تشكل خطراً سياقياً عالياً لا يمكن علاجه بالتشويش.
- تحديث `scripts/upload_gate.py`: فرض قيود صارمة تمنع رفع المقاطع المرفوضة دلالياً أو التي تتطلب مراجعة يدوية.
- تحديث `scripts/risk_scorecard.py`: إضافة محور **Semantic Policy** لتقرير المخاطر، مما يرفع دقة التنبؤ بمخالفات YouTube.

### ذكاء الصوت (Audio Intelligence)
- أضيف `scripts/audio_analysis.py`: وحدة لاستخراج طاقة الصوت (RMS) وتعيينها لكل إطار فيديو.
- تحسين **Active Speaker Switching**: ربط طاقة الصوت بحركة الفم (MAR) لزيادة دقة التركيز على المتحدث الفعلي وتجنب التبديل الخاطئ أثناء الصمت أو الضوضاء البصرية.

### التحقق النهائي
- نجحت جميع اختبارات الوحدة والتكامل (`pytest`).
- تم تحديث التوثيق العربي الشامل في `FINAL_REPORT_AR.md`.
- تم إصلاح خلل في واجهة Gradio عند التعامل مع المشاريع الفارغة.

## ViralCutter 7.3.0-pro — Production Reports & Render Reliability — 15 أغسطس 2026

أضيفت في هذا الإصدار طبقة إنتاجية جديدة تساعد على مراجعة كل مشروع قبل النشر، إلى جانب إصلاحات موثوقية في مسار إخراج الفيديو. أصبح الأمر `viralcutter-report` ينشئ `project_report.json` و`project_report.html` محلياً، ويجمع حالة الـ checkpoint، وسلامة المحتوى، وبطاقة المخاطر، وصلاحية الملفات النهائية، وسجل النشر دون تضمين مفاتيح API أو رموز OAuth.

أصبح التقرير يُنشأ تلقائياً بعد اكتمال التشغيل من واجهة WebUI، وتظهر بطاقة الجاهزية داخل معرض المشروع مع عدد المقاطع المحجوبة والمقاطع التي تتطلب مراجعة يدوية. كما أُصلح خلل كان يستدعي `finalize_video` ثلاث مرات في مسار MediaPipe، وأصبح المكسج يتحقق من return code ومن وجود مساري الفيديو والصوت والمدة قبل اعتماد الملف.

يستخدم المكسج الآن ملفاً مؤقتاً مع `os.replace` حتى لا يترك ملفاً نهائياً ناقصاً عند فشل FFmpeg، ولا تُحذف الملفات المؤقتة إلا بعد نجاح التحقق. نجحت اختبارات المشروع الكاملة، وفحص lint، وcompileall، واختبارات تشغيل CLI وتقرير المشروع.

## ViralCutter 7.3.1-pro — Windows Python compatibility hotfix — 15 أغسطس 2026

أُصلحت مشكلة تثبيت Windows التي كانت تسمح بإنشاء البيئة الافتراضية باستخدام Python 3.14، ثم تجعل pip يحاول بناء `numpy 1.26.4` من المصدر ويفشل بسبب غياب مترجم Visual C++. أصبح نطاق Python المعلن والمفحوص من 3.9 إلى 3.12، ويعرض preflight رسالة حرجة واضحة عند استخدام إصدار أحدث.

أصبح `install_dependencies.bat` ينشئ البيئة باستخدام Python 3.12 صراحة، ويتحقق من إصدار البيئة الموجودة ويعيد إنشاءها إذا كانت غير مدعومة. كما أضيف دليل `docs/WINDOWS_SETUP_FIX_AR.md` الذي يشرح سبب الخطأ وخطوات الإصلاح اليدوية والآلية.

## ViralCutter 7.3.2-pro — Windows installer stability hotfix — 15 أغسطس 2026

أعيد بناء `install_dependencies.bat` لتجنب كتل `cmd.exe` الحساسة وإلغاء `set /p` الذي كان يؤدي إلى رسالة `... était inattendu.` بعد سؤال GPU. أصبح الوضع الافتراضي CPU ولا يحتاج إلى إدخال تفاعلي، بينما يمكن اختيار CUDA عبر `install_dependencies.bat gpu` أو `VIRALCUTTER_GPU=1`، وإضافة اعتماديات YouTube عبر `install_dependencies.bat upload`.

أصبح `run.bat` و`run_webui.bat` يستخدمان Python الموجود داخل `.venv` مباشرة بدلاً من الاعتماد على PATH بعد activate، مع خيار `VIRALCUTTER_NO_PAUSE=1` للتشغيل الآلي. يحتفظ المثبت بالتحقق من Python 3.12 وFFmpeg وpreflight، ويعرض رسائل خطأ قابلة للتنفيذ بدلاً من التوقف الغامض.

## ViralCutter 7.3.3-pro — Low-disk resilient Windows installer — 15 أغسطس 2026

أعيد تصميم مثبت Windows ليبدأ بوضع Lightweight افتراضياً، فلا يقوم بتنزيل PyTorch أو WhisperX الضخمين إلا عند طلب `full`. ينقل ملفات temp إلى مجلد المشروع، يعطل كاش uv أثناء التثبيت، ويفحص وجود مساحة حرة لا تقل عن 8GB قبل بدء التنزيل حتى لا يفشل الاستخراج في منتصف العملية.

أُصلحت مسارات `goto` التي كانت قد تعرض رسالة `The system cannot find the batch label` بعد فشل PyTorch. كما أصبح CUDA اختيارياً عبر `install_dependencies.bat gpu full`، وتثبيت YouTube اختيارياً عبر `install_dependencies.bat upload`. عند فشل مكوّن اختياري يستمر المثبت في إعداد WebUI الأساسي بدلاً من إيقاف التثبيت كله.

## ViralCutter 7.3.4-pro — WebUI preflight warning fix — 15 أغسطس 2026

أُصلح `run_webui.bat` و`run.bat` بحيث يميزان بين أكواد preflight: الكود 0 يعني جاهز، والكود 2 يعني تحذيرات اختيارية مع الاستمرار، والكود 1 يبقى خطأً حرجاً يوقف التشغيل. كان المشغل السابق يعامل الكود 2 كفشل، ولذلك كان يتوقف بعد رسالة `System ready` رغم أن WebUI قابل للعمل.

## ViralCutter 7.3.5-pro — Arabic professional workbench UI — 15 أغسطس 2026

أُعيد تنظيم واجهة WebUI العربية لتصبح أقرب إلى مساحة عمل احترافية: تحديث الرأس ليعرض `ViralCutter Pro` والإصدار الفعلي، إضافة وصف عربي موجز، طي لوحة مراقبة التشغيل افتراضياً، وطي سجل التشغيل حتى لا يزاحم خطوات العمل الأساسية.

تم تعريب بطاقات التقدم بالكامل، بما في ذلك أسماء مراحل التنزيل والتفريغ وتحليل الذكاء الاصطناعي والقص والمونتاج والترجمة، مع تحسين التفاف التبويبات والمسافات والاستجابة للشاشات الصغيرة. أُعيد تشغيل الخادم والتحقق بصرياً من الواجهة على المنفذ 7860.

## ViralCutter 7.3.6-pro — Create New layout refinement — 15 أغسطس 2026

تم عكس التخطيط البصري لعمودي تبويب `إنشاء جديد`: أصبحت إعدادات المصدر والقص والترجمة في اليسار، وأصبحت إعدادات الذكاء الاصطناعي والأمان والمونتاج في اليمين. نُقلت أزرار `بدء المعالجة` و`إيقاف المعالجة` من الشريط العلوي إلى شريط إجراءات واضح أسفل جميع إعدادات النموذج. تم تشغيل الخادم والتحقق بصرياً من الترتيب الجديد.

## ViralCutter 7.3.7-pro — Arabic RTL polish — 15 أغسطس 2026

تم تحسين تنسيق RTL في تبويب `إنشاء جديد`: توحيد اتجاه ومحاذاة العناوين والحقول والقوائم العربية إلى اليمين، إبقاء روابط يوتيوب بصيغة LTR، زيادة المسافة بين العمودين، وتنسيق شريط الإجراءات باتجاه RTL. أصبح زر `بدء المعالجة` في أقصى اليمين، وزر `إيقاف المعالجة` إلى يساره، مع محاذاة وارتفاع متناسقين.

## OUSSAMA Cutter 7.4.0-pro — إعادة الهوية وإصلاح التفريغ — 15 أغسطس 2026

تم اعتماد هوية **OUSSAMA Cutter** في واجهة WebUI وCLI ومثبت Windows وmetadata الحزمة، مع إبقاء أوامر `viralcutter` القديمة كاختصارات توافقية. أضيفت وحدة `scripts/transcription_diagnostics.py` لتشخيص Torch وTorchaudio وWhisperX، فحص المساحة، حفظ تقرير JSON، وتنفيذ إصلاح صريح CPU أو GPU عند طلب المستخدم.

أصبح خطأ غياب WhisperX/Torch يعرض رسالة عربية قصيرة ومباشرة، ويوقف العملية دون إعادة محاولة غير مفيدة أو traceback طويل، مع إنشاء `transcription_diagnostic.json` داخل مجلد المشروع. يظل وضع المونتاج والأمان متاحاً بدون التفريغ، بينما يتطلب مسار YouTube الكامل تثبيت حزمة التفريغ صراحة عبر `setup_on_d.ps1 -Mode Full -Transcription cpu` أو `gpu`.

## 7.12.0-pro — 22 أغسطس 2026

- دورة checkpoint قابلة للاستئناف مع active stage وlast error وhistory.
- طابور دائم بالأولوية مع pause/resume/cancel/retry failed/recovery.
- منع الرفع المكرر ببصمة SHA-256 وحاجز Public صريح وDry Run آمن.
- تشخيص Windows لمسارات D وTEMP وFFmpeg وDeno وTorch/CUDA وWhisperX وOAuth.
- فحص إعدادات قبل البدء، تنظيم RTL، بطاقة backup/restore، وتحليل publish history المحلي.
- ملاحظات الإصدار التفصيلية: docs/RELEASE_NOTES_7.12.0_PRO_AR.md.

## 7.45.0-pro — تحصين طبقة السلامة والموثوقية — 29 سبتمبر 2026

جولة مراجعة مستقلة عدائية على الطبقات الحرجة أثبتت 24 خللاً حقيقياً بإعادة
الإنتاج، وأخطرها فتحات fail-open في بوابة الرفع. أُغلقت كلها مع اختبارات انحدار.

- **fail-closed:** تعطُّل `content_guard` يمنع الرفع؛ حذف `censor_map.json` القديمة
  قبل أي تشغيل؛ احترام حكم `review` السياقي حتى مع الكتم؛ OCR بلا دليل = فشل.
- **الأمان الدلالي:** الأنماط تُطبَّع بنفس دالة النص — العبارات الرئيسية
  («هؤلاء الناس لا يستحقون الحياة»، «إبادة»، «تطهير عرقي») تُلتقط الآن فعلاً.
- **الخصوصية:** `effective_privacy()` مصدر حقيقي واحد للتأكيد والرفع والسجل
  (يسدّ تجاوز `YT_PRIVACY=public` للنشر العام).
- **الجدولة:** الرفع يبدأ قبل الموعد بمهلة (`--lead-minutes`، افتراضي 15) و
  `publishAt` مستقبلي؛ `timezone_offset_hours` مطبَّق؛ `--plan` Dry Run افتراضياً
  و`--live` تفعيل صريح.
- **بلا تكرار:** الرفع الدفعي بأسبقية مصدر واحدة؛ نجاح نسخة `variant_of` يُحتسب
  نجاحاً للأصل؛ `publish_history` كتابة إضافية تحت قفل.
- **موثوقية:** كتابة إعدادات ذرّية بـ`mkstemp`، طابور يستعيد `retrying` ولا يترك
  عمالاً أمواتاً، checkpoint لا ينهار على `version` تالف + قفل كتابة، ffprobe
  `"size": "N/A"` يُعالج بلطف، تنقيح أسرار تقارير الأعطال، وتقييد مسارات
  المشاريع بـ`realpath`.
- **الاختيار:** لا انهيار على تفريغ فارغ، عقوبة التكرار تُطبَّق على `selection_score`،
  `completion_score` القديم يُقرأ، كشف الحشو لا يتأثر بالترقيم، و`editor_review`
  يحقق عقد «لا يرفع استثناءً».
- **Issue #25:** رقعة الـ workflow جاهزة ومختبَرة
  (`docs/ISSUE_25_workflow_fix.patch` + `docs/ISSUE_25_WORKFLOW_FIX_AR.md`) واختبار
  `test_release_workflow_consistency.py` يقفل العقد بين الـ workflow والـ spec.

الملاحظات التفصيلية: `docs/RELEASE_NOTES_7.45.0_AR.md`. المجموعة الكاملة:
1612 اختباراً ناجحاً (+1 متخطّى)، `ruff check .` نظيف.

## 7.46.0-pro — فحص قبول Windows + RTX 3060 — 29 سبتمبر 2026

- `scripts/windows_acceptance.py`: حارس قبول يشغّل وحدات الإنتاج الحقيقية على
  وسائط مولَّدة محلياً بـ FFmpeg (فحص الوسائط، إعادة إطار 9:16، Audio QC،
  كشف الصمت، صورة مصغّرة) — بلا تنزيل ولا OAuth ولا رفع.
- `--gpu-test` يقلع faster-whisper على CUDA ويفرغ مقطعاً حقيقياً مع قياس الأزمنة؛
  `--require-gpu` يجعل غياب CUDA فشلاً حرجاً؛ `--clip` يفحص مقطع المستخدم.
- تقرير ذري `windows_acceptance.json` بأكواد خروج آلية، ودليل PowerShell كامل
  في `docs/WINDOWS_RTX3060_ACCEPTANCE_AR.md`.
- +14 اختباراً (فحص GPU والتفريغ مُقلَّدان، واختبارا قبول حقيقيان بـ FFmpeg).
  المجموعة: 1626 ناجحاً، `ruff check .` نظيف.

## 7.47.0-pro — corpus عربي مرخّص لمعايرة Audio QC — 29 سبتمبر 2026

- `scripts/audio_qc_corpus.py`: إدارة corpus مرجعي بإثبات ترخيص — `init` (manifest
  إلزامي)، `add` (تطبيع إلى MP4 معياري 1fps + أحادي 16kHz)، `import-cv` (استيراد
  Mozilla Common Voice العربية CC0 من تخطيطها الرسمي)، `status`، و`calibrate`
  الذي يفشل مغلقاً بلا ترخيص ثم ينتج `audio_qc_thresholds.json`.
- لا تنزيل تلقائي لأي dataset — اقتناء البيانات قرار بشري؛ تسجيلات القناة هي
  المصدر الموصى به. الدليل: `docs/AUDIO_QC_CORPUS_AR.md`.
- +11 اختباراً حقيقياً بـ FFmpeg؛ المجموعة: 1637 ناجحاً، `ruff check .` نظيف.

## 7.48.0-pro — بطارية هجومية دائمة + قياس جودة الاختيار — 29 سبتمبر 2026

- `tests/test_upload_gate_adversarial.py` (72 اختباراً): عنصر تحكم موثَّق يُسمح له،
  فشل مغلق على كل خلل منفرد، مصفوفة «لا يرفع أبداً» (6 ملفات × 8 حمولات مكسورة)،
  واختبار تزامن على قاعدة الحارس.
- اصطادت البطارية خطأً حقيقياً وأُصلح: JSON صالح من نوع خاطئ في ملفات التقارير
  كان يكسر البوابة بـ AttributeError — محمّل `_load_json_dict` الجديد يعامله كملف
  مفقود في كل مستهلكي التقارير.
- `scripts/selection_benchmark.py`: recall@k حتمي دون اتصال على fixtures موسومة
  (الـ demo المدمج = 1.0) — ضبط طبقات الترتيب بمقياس بدل الحدس.
  الدليل: `docs/SELECTION_BENCHMARK_AR.md`.
- Issue #27 مسجَّلة: auto_updater لا يمكن أن يعمل والمستودع خاص (قرار توزيع مطلوب).
- المجموعة: 1722 ناجحاً، `ruff check .` نظيف.

## 7.49.0-pro — مُطبِّق بأمر واحد لإصلاح CI (#25 + #26) — 29 سبتمبر 2026

- `scripts/apply_ci_workflow_fix.py`: أمر واحد للمالك ينسخ ملفَّي الـ workflow
  النهائيين من `packaging/ci-fix/`، ويزيل الـ shim من `packaging/viralcutter.spec`
  في نفس العملية (الترتيب الصحيح)، ويتحقق (YAML، checksums.txt، تطابق EXE_NAME،
  إزالة الـ shim)، ثم `--push` ينشئ commit ويدفعه. آمن للتكرار.
- `packaging/ci-fix/`: الملفان النهائيان كبيانات مصدرية مطابقة للرقعة الموثَّقة.
- توثيق المسارات المستنفدة (git push / Contents API / Git Data API) في
  `docs/ISSUE_25_WORKFLOW_FIX_AR.md`.
- تحقق طرف-لطرف: الإصلاح طُبّق على نسخة كاملة → 1738 اختباراً ناجحاً، ودفع فعلي
  إلى remote محلي أنتج commit صحيحاً. +16 اختباراً للأداة.

## 7.50.0-pro — دبلجة متعددة اللغات + قناة تحديث موقَّعة — 29 سبتمبر 2026

- `scripts/tts_providers.py` + `scripts/dubbing.py`: دبلجة المقاطع المترجمة (Edge TTS
  اختياري صريح لأنه يرسل النص للخارج، أو ملفات صوت مسجَّلة)، ملاءمة زمنية بـ`atempo`،
  فشل مفتوح لكل سطر، فحص ذاتي ضد الدوبلاج الصامت، ولا تجاوز لبوابات النشر.
  الدليل: `docs/DUBBING_AR.md`.
- `scripts/update_manifest.py`: قناة تحديث موقَّعة Ed25519 تعمل بلا GitHub Releases
  (تُغلق مسار #27)، مع `sign_detached`/`verify_detached` في `sign_artifacts`،
  وفشل مغلق على غير الموقَّع/المعدَّل/المفقود. الدليل: `docs/UPDATE_CHANNEL_AR.md`.
- +59 اختباراً (31 manifest + 28 dubbing، منها قياس جهارة فعلية في نوافذ زمنية).
  المجموعة: 1797 ناجحاً، `ruff check .` نظيف.

## 7.51.0-pro — الدبلجة في الواجهة — 29 سبتمبر 2026

- قسم دبلجة كامل في تبويب «رفع ونشر»: المزوّد (file بلا شبكة افتراضياً / edge)،
  الوضع (replace/mix)، الصوت/المجلد، ومربّع «السماح بالشبكة» كموافقة صريحة مرئية.
- `publish_panel.dub_clip()` يفضّل الترجمة المترجمة إن وُجدت، ويعيد (ok, message)
  مثل translate_clip؛ و`allow_network` قابل للتمرير برمجياً مع بقاء بوابة البيئة.
- `requirements-dubbing.txt` (edge-tts) اختياري ومسجَّل كذلك في preflight.
- +13 اختباراً؛ المجموعة: 1810 ناجحاً، ruff نظيف.
