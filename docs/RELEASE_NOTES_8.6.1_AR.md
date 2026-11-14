# OUSSAMA Cutter 8.6.1-ultimate — إنقاذ تثبيت Windows

_التاريخ: 2026-11-14 · النطاق: venv نصف مثبّت، وحزم اختيارية تمنع التشغيل_

## 1. العَرَض

```
error: Failed to read metadata for: charset-normalizer==3.5.2
Caused by: failed to open file
  D:\OS\.venv\Lib\site-packages\charset_normalizer-3.5.2.dist-info\METADATA:
  Le fichier spécifié est introuvable. (os error 2)
```

ثم سلسلة أعطال مضللة في preflight:

```
❌ requests  installed but import failed (requests; ModuleNotFoundError: missing module idna)
❌ gradio    installed but import failed (gradio; ModuleNotFoundError: missing module idna)
⚠️ whisperx  installed but import failed (... missing module idna)
❌ mediapipe ... ❌ google-genai ...
```

## 2. السبب

تثبيت سابق انقطع في منتصفه، فبقي مجلد
`charset_normalizer-3.5.2.dist-info` موجوداً **بلا ملف METADATA**.

- `uv` يقرأ بيانات كل حزمة في البيئة قبل أن يثبّت أي شيء.
- وجد مجلداً بلا METADATA → أجهض العملية.
- لم يستطع إصلاح الحزمة التي تمنعه، ولا تثبيت `requests` التي تعتمد عليها.
- كل الأعطال الأخرى (`idna`, `certifi`, `requests`, `gradio`, `whisperx`)
  كانت أثراً متتابعاً لهذا المجلد الواحد، لا أعطالاً مستقلة.

## 3. الإصلاح

### أ. تنظيف تلقائي قبل uv
- `scripts/repair_venv_metadata.ps1`: يفحص `site-packages` ويحذف كل
  `*.dist-info` بلا METADATA أو بـMETADATA فارغ. يدعم `-DryRun`.
- `scripts/preflight.py`:
  - `repair_incomplete_dist_info()` والفحص `venv metadata` — يظهر كخطأ
    حرج قبل أن يصل uv إلى طريق مسدود.
  - `python -m scripts.preflight --repair-metadata` للتنظيف اليدوي.
- `install_dependencies.bat`:
  - ينظّف قبل التثبيت الأساسي.
  - إذا فشل التثبيت، ينظّف ويعيد المحاولة **مرة واحدة** تلقائياً.
- `run_webui.bat`: ينظّف عند كل تشغيل قبل preflight، فحتى لو تلفت البيئة
  لاحقاً لا تحتاج إلى إعادة تثبيت يدوية من الصفر.

### ب. الحزم الاختيارية لم تعد تمنع التشغيل
كانت `mediapipe` و`google-genai` و`insightface` تُصنَّف «حرجة» لأنها في
`requirements.txt`، فيتوقف التشغيل رغم أن لكل منها بديلاً موثقاً:

| الحزمة | البديل عند غيابها |
|---|---|
| `mediapipe` | insightface أو Haar |
| `insightface` | mediapipe أو Haar |
| `google-genai` | بقية مزوّدي AI |
| `g4f` | مزوّد أخير فقط |
| `onnxruntime` | الفحص البصري فقط |
| `scenedetect` | كشف المشاهد فقط |
| `cryptography` | تشفير مفاتيح API فقط |

صارت تُعرض كتحذير مع سبب صريح، لا كخطأ يمنع الواجهة. الحزم الأساسية
(`gradio`, `fastapi`, `uvicorn`, `websockets`, `requests`, `yt-dlp`…)
تبقى حرجة.

## 4. الإنقاذ الفوري (قبل سحب الإصلاح)

```powershell
# 1) احذف مجلد البيانات التالف المشار إليه في الرسالة
Remove-Item -Recurse -Force "D:\OS\.venv\Lib\site-packages\charset_normalizer-3.5.2.dist-info"

# 2) أعد التثبيت
cd D:\OS
.\setup_on_d.ps1
```

بعد سحب 8.6.1:

```powershell
git pull
.\.venv\Scripts\python.exe -m scripts.preflight --repair-metadata
.\setup_on_d.ps1
```

## 5. التحقق

- `tests/test_preflight_metadata.py` — 6 اختبارات:
  - حذف المجلدات الناقصة/الفارغة فقط، وترك السليمة والملفات.
  - `-DryRun` لا يغيّر شيئاً.
  - مسار غير موجود لا يُسقط الفحص.
  - الحزم الاختيارية WARN مع سبب، والأساسية FAIL.
  - `check_venv_metadata` يكشف ويُصلح.
- المجموعة كاملة خضراء، `ruff` نظيف.

## 6. ملاحظة

هذا الإصلاح لا يخفي عطلاً حقيقياً: الحزم الأساسية ما زالت تُوقف التشغيل.
لكنه يمنع خطأ metadata واحداً من الظهور كعشرة أعطال، ويمنع ميزة اختيارية
من إغلاق التطبيق.
