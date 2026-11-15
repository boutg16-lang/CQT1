# OUSSAMA Cutter 8.7.0-ultimate — طبيب التفريغ ذاتي الإصلاح

_التاريخ: 2026-11-14 · النطاق: فحص VAD الحقيقي، تثبيت النواقص تلقائياً، وكاش النجاح_

## 1. لماذا

preflight كان يفحص **الاستيراد** فقط:

```
✅ whisperx 3.3.1
✅ tokenizers 0.22.2
✅ transformers 5.15.1
```

ومع ذلك يفشل التشغيل عند `load_vad_model` أو `model.transcribe`. الاستيراد
لا يعني أن حزمة VAD تعمل: `asteroid_filterbanks` قد يكون مفقوداً، أو
امتداد tokenizers الأصلي تالفاً، أو ملف النموذج غير مكتمل.

المستخدم لا يريد تشخيصاً؛ يريد أن **يُثبَّت الناقص ويُكمَل العمل**.

## 2. طبيب التفريغ

`python -m scripts.transcription_diagnostics --ensure`

1. **يفحص الحزمة فعلياً**:
   - يحمّل `whisperx.vad.load_vad_model(torch.device("cpu"))` — نفس المسار
     الذي يسقط في التشغيل.
   - يسجّل الاستثناء الحقيقي، لا تلميحاً.
2. **يصلح الناقص تلقائياً**:
   - خطأ `asteroid_filterbanks`/`pyannote` → يثبّت
     `asteroid-filterbanks`, `einops`, `omegaconf`, `soundfile`,
     `pyannote.core/database/metrics/pipeline`.
   - خطأ `tokenizers` الأصلي → يعيد تثبيت `tokenizers==0.22.2`
     بـ`--force-reinstall --no-deps` (لا يمسّ Torch).
   - إن لم يكفِ الإصلاح الموجّه → يعيد تثبيت مجموعة التفريغ الكاملة
     (`repair(cpu/gpu)`) مع الحفاظ على Torch CUDA إن كان يعمل.
3. **يخزّن النجاح**:
   - `.transcription_ready.json` يحمل بصمة إصدارات الحزمة.
   - كل تشغيل لاحق بنفس الإصدارات يتخطى الفحص الثقيل فوراً.
   - أي تغيير في Torch/WhisperX/tokenizers يبطل الكاش ويعيد الفحص.
4. **لا يمنع التشغيل**:
   - إذا فشل الإصلاح، يطبع السبب ويعمل التطبيق عبر faster-whisper إن توفر.

## 3. عند التشغيل

`run_webui.bat` صار يشغّل الطبيب بعد preflight وقبل الواجهة:

```
[preflight] Checking the real transcription stack (VAD/tokenizers)...
[doctor] repairing VAD dependencies: asteroid-filterbanks>=0.4, ...
✅ VAD: loaded
```

- أول تشغيل قد يستغرق دقائق إذا كان الإصلاح مطلوباً.
- التشغيلات التالية أسرع لأن الكاش يطابق البصمة.

## 4. أوامر

```powershell
# فحص وإصلاح تلقائي
.\.venv\Scripts\python.exe -m scripts.transcription_diagnostics --ensure

# فحص فقط، بلا تثبيت
.\.venv\Scripts\python.exe -m scripts.transcription_diagnostics --ensure --no-repair

# تقرير JSON
.\.venv\Scripts\python.exe -m scripts.transcription_diagnostics --ensure --json
```

## 5. رسائل الأخطاء

`build_error_message` لم يعد يكتب «tokenizers 0.23.1 مثبت» أو
«Hub 0.36.2» إلا إذا كان الإصدار الفعلي كذلك. الرسالة تقرأ الإصدار
المثبت من `diagnose()` وتفرّق بين:
- تعارض إصدار حقيقي.
- فشل أصلي في tokenizers رغم الإصدار الصحيح.
- فشل VAD لا علاقة له بالإصدارات.

## 6. التحقق

- `tests/test_transcription_doctor.py` — 8 اختبارات:
  - تحميل VAD الناجح والفاشل.
  - تخطي عند غياب WhisperX.
  - الكاش يمنع إعادة الفحص.
  - إصلاح تبعيات pyannote.
  - إعادة تثبيت tokenizers.
  - فشل الإصلاح يُبلَّغ بلا استثناء.
  - الرسائل تستخدم الإصدارات الفعلية.
- المجموعة كاملة خضراء، `ruff` نظيف.

## 7. حدود

- الإصلاح التلقائي يحتاج إنترنت ومساحة قرص (4GB احتياطاً).
- إذا كان `tokenizers` تالفاً على مستوى الملفات، فسيفشل WhisperX
  وfaster-whisper معاً حتى تُعاد التثبيت.
- الطبيب لا يثبّت النماذج (large-v3 وغيرها)؛ تلك تُنزَّل عند أول تفريغ.
