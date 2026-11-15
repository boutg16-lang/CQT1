# OUSSAMA Cutter 8.6.5-ultimate — الخطأ الحقيقي، لا «تعارض إصدارات» وهمي

_التاريخ: 2026-11-14 · النطاق: تشخيص WhisperX والتحويل عند أي فشل تحميل_

## 1. العَرَض

```
تعارض في تبعيات WhisperX: تتطلب مجموعة التفريغ المدعومة إصدار
Hugging Face Hub من 1.16.0 إلى أقل من 2.0...
```

بينما preflight يقول:

```
✅ huggingface-hub 1.33.0
✅ transformers 5.15.1
```

التعارض غير حقيقي. الرسالة كانت تُطبع لأن **أي** تتبّع يحتوي
`huggingface_hub` — حتى مسار ملف في `Traceback` — كان يُصنَّف كتعارض
إصدارات. الخطأ الحقيقي (401، نموذج محمي، checksum، أو `use_auth_token`)
كان يختفي.

## 2. الإصلاح

### أ. عرض الخطأ الجذري
`summarize_error` يأخذ الآن **آخر سطر استثناء** كعنوان:
`GatedRepoError: 401 ...` بدل `Traceback (most recent call last)`.
- لم يعد `huggingface_hub` وحده سبباً لادعاء تعارض إصدارات.
- ادعاء التعارض لا يظهر إلا إذا كانت الرسالة تتحدث فعلاً عن
  `required` / `found` / `no matching distribution` / `version`.

### ب. تلميحات دقيقة
| الخطأ | التلميح |
|---|---|
| `GatedRepoError` / `401` / `gated` | اقبل شروط النموذج على huggingface.co واضبط `HF_TOKEN` |
| `use_auth_token` | Hub 1.x أزال الوسيط؛ CQT1 يعالجه، وحدّث إن استمر |
| `checksum does not match` | أصل VAD التالف؛ أعد تثبيت whisperx |
| `model file not found` | أصل VAD مفقود؛ أعد تثبيت whisperx |
| تعارض إصدار حقيقي | نفس رسالة 1.16.0–2.0 |

### ج. التحويل عند أي فشل تحميل
سابقاً كان التحويل إلى faster-whisper يحدث فقط عند أخطاء VAD/Import.
الآن **أي استثناء أثناء بناء WhisperX** (VAD، pyannote، hub، ملفات النموذج)
يؤدي إلى:
```
WhisperX model load failed: ... — switching to the independent
faster-whisper backend.
```
- يُكتب نفس SRT/TSV/JSON.
- تُتخطى محاذاة wav2vec2 فقط.
- إن فشل faster-whisper أيضاً، تظهر **السببان** معاً.
- اسم النموذج غير المدعوم (`ValueError` invalid model size) يبقى مساراً
  منفصلاً يجرّب الحجم التالي.

## 3. اعرف السبب الحقيقي (اختياري)

```powershell
cd D:\OS
.\.venv\Scripts\python.exe -c "import torch, whisperx; from whisperx.vad import load_vad_model; load_vad_model(torch.device('cpu')); print('VAD OK')"
```

- `VAD OK` → المشكلة ليست VAD.
- `GatedRepoError`/`401` → اقبل شروط `pyannote/segmentation-3.0` واضبط
  `HF_TOKEN`.
- `checksum`/`model file not found` → أعد تثبيت whisperx:
  `uv pip install --reinstall --python ".venv\Scripts\python.exe" whisperx==3.3.1`.
- أي خطأ آخر: أرسل السطر الأخير؛ الـhint الجديد سيعرضه بدقة.

## 4. التحقق

- `tests/test_webui_utils.py` — 3 اختبارات جديدة:
  - مسار `huggingface_hub` في تتبّع 401 لا يُصنَّف تعارض إصدارات.
  - `use_auth_token` له تلميحه الخاص.
  - تعارض الإصدار الحقيقي ما زال يُعرَف.
- `tests/test_transcribe_vad_fallback.py` — اختبار جديد: أي فشل تحميل
  (RuntimeError/401) يتحول إلى faster-whisper بسبب `load`.
- المجموعة كاملة خضراء، `ruff` نظيف.

## 5. حدود

- faster-whisper بديل وظيفي، لكنه لا ينفّذ محاذاة wav2vec2 ولا diarization.
- إن كان الخطأ 401 لنموذج **التحويل** نفسه (لا VAD)، فالتفريغ قد يفشل في
  البديل أيضاً؛ هنا يلزم `HF_TOKEN` أو نموذج عام.
- الرسالة الجديدة لا تخفي الخطأ؛ إن كان غامضاً فأرسل آخر سطر.
