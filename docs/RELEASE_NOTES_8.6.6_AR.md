# OUSSAMA Cutter 8.6.6-ultimate — تلميح tokenizers الكاذب

_التاريخ: 2026-11-14 · النطاق: تشخيص أخطاء tokenizers والتحويل أثناء التفريغ_

## 1. العَرَض

```
WhisperX dependency conflict: transformers caps tokenizers at 0.23.0 ...
run: uv pip install "tokenizers==0.22.2"
```

بينما preflight يقول:

```
✅ tokenizers 0.22.2
✅ transformers 5.15.1
```

التعارض غير حقيقي. أي تتبّع يحتوي كلمة `tokenizers` — حتى مسار ملف
`...\tokenizers\__init__.py` — كان يُترجم إلى رسالة تعارض الإصدارات.
الخطأ الأصلي (مثل فشل الامتداد الأصلي Rust، أو ملف tokenizer تالف) كان يختفي.

## 2. الإصلاح

### أ. لا تعارض بلا دليل
- حُذف التلميح العام `("tokenizers", ...)` و`("0.23.1", ...)`.
- لا تُطبع رسالة 0.23.0 إلا إذا ذكر النص فعلاً:
  `0.23.1` أو `required` أو `no matching distribution` أو `found 0.2`.
- مسار `tokenizers` وحده لم يعد كافياً.

### ب. تلميح للفشل الأصلي
إذا فشل الامتداد الأصلي (`rust`, `panicked`, `native`, `failed to load`,
`ImportError`, `TypeError`, `Exception`)، يظهر:

> The tokenizers native extension failed. Reinstall it:
> `uv pip install --reinstall "tokenizers==0.22.2"`. If it persists, send the last error line.

### ج. التحويل أثناء التفريغ
سابقاً كان التحويل إلى faster-whisper يحدث عند فشل **التحميل** فقط.
الآن إذا فشل `model.transcribe` نفسه بخطأ في
`tokenizers/transformers/huggingface/pyannote`، يتحول فوراً إلى
faster-whisper بدل إسقاط الجلسة.
- أخطاء الذاكرة لها مسارها الحالي (batch أصغر ثم بديل).
- أخطاء الاستخدام الأخرى تبقى كما هي.

## 3. اعرف الخطأ الحقيقي

في بطاقة الخطأ، السطر الأحمر الأول هو الآن **آخر سطر استثناء** (وليس
`Traceback`). افتح «التفاصيل التقنية» وانسخ آخر 5 أسطر.

أو شغّل:

```powershell
cd D:\OS
.\.venv\Scripts\python.exe -c "import tokenizers; print('tokenizers', tokenizers.__version__); from tokenizers import Tokenizer; print('native ok')"
```

- `native ok` → المشكلة ليست في تحميل الامتداد.
- `ImportError`/`DLL load failed`/`panicked` → أعد التثبيت:
  ```powershell
  uv pip install --reinstall --python ".venv\Scripts\python.exe" "tokenizers==0.22.2"
  ```
- إن استمر، أرسل آخر سطر؛ قد يكون توكنزير النموذج نفسه.

## 4. بديل فوري
```powershell
cd D:\OS
$env:VIRALCUTTER_TRANSCRIPTION_BACKEND = "faster-whisper"
.\run_webui.bat
```

## 5. التحقق

- `tests/test_webui_utils.py` — 3 اختبارات جديدة:
  - مسار `tokenizers` في تتبّع لا يُصنَّف تعارض إصدارات.
  - تعارض الإصدار الحقيقي ما زال يُعرَف.
  - فشل الامتداد الأصلي يعطي أمر إعادة التثبيت.
- `tests/test_transcribe_vad_fallback.py` — اختبار جديد: فشل
  `model.transcribe` بخطأ tokenizers يتحول إلى faster-whisper.
- المجموعة كاملة خضراء، `ruff` نظيف.

## 6. حدود

- إن كان `tokenizers` نفسه تالفاً على مستوى الملفات، فكل من WhisperX
  وfaster-whisper سيفشل حتى يُعاد تثبيته.
- الرسالة لم تعد تخمّن؛ إن كان الخطأ غامضاً، آخر سطر هو الحقيقة.
