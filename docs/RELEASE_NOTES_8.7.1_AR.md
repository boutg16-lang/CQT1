# OUSSAMA Cutter 8.7.1-ultimate — نجاة من الانهيار الأصلي 0xC0000409

_التاريخ: 2026-11-14 · النطاق: انهيارات Windows الأصلية في التفريغ_

## 1. العَرَض

```
3221226505
💡 مكوّن التفريغ الصوتي غير مثبّت — أعد تشغيل install_dependencies.bat واختر تثبيت whisperx
```

مع أن preflight يعرض WhisperX مثبتاً. الرقم `3221226505` هو
`0xC0000409` = **STATUS_STACK_BUFFER_OVERRUN**: انهيار في مكتبة أصلية
(tokenizers/CTranslate2/WhisperX)، وليس حزمة ناقصة.

Python لا يستطيع التقاط هذا النوع؛ العملية تموت قبل أن يعمل أي `try/except`
أو fallback. والتلميح كان يرى كلمة `whisperx` في مسار التتبّع فيقول
«غير مثبّت».

## 2. الإصلاح

### أ. عند التشغيل (run_webui.bat)
بعد طبيب VAD:

- إذا خرج الطبيب بالرمز `3221226505` أو `-1073740791`:
  1. يعيد تثبيت `tokenizers==0.22.2` بـ`--force-reinstall --no-deps`.
  2. يفعّل `VIRALCUTTER_TRANSCRIPTION_BACKEND=faster-whisper`.
  3. يفعّل `VIRALCUTTER_TRANSCRIPTION_FORCE_CPU=1`.
  4. يبدأ الواجهة — أي أن أول تشغيل بعد الانهيار يعمل على المسار الآمن.
- أي فشل آخر في الطبيب → faster-whisper على CPU أيضاً.

### ب. لا استيراد لـWhisperX في وضع faster-whisper
`transcribe_video.py` كان يستورد `whisperx` في أعلى الملف حتى عند طلب
faster-whisper، فقد يموت عند الاستيراد نفسه. الآن:

```python
if VIRALCUTTER_TRANSCRIPTION_BACKEND == "faster-whisper":
    whisperx = None  # لا تلمس المكتبة الأصلية إطلاقاً
```

فيصل التنفيذ إلى faster-whisper دون المرور بالمسار المنهار.

### ج. تلميح حقيقي للانهيار
- `3221226505` / `0xC0000409` → «انهيار أصلي؛ CQT1 سيستخدم faster-whisper،
  وإن تكرر أعد تثبيت tokenizers أو استخدم faster-whisper. لا تعد تثبيت
  المشروع كله.»
- `whisperx` في مسار ملف لم يعد يُفسَّر كحزمة ناقصة؛ فقط
  `No module named 'whisperx'` يفعل.

## 3. ماذا تفعل الآن

```powershell
cd D:\OS
git pull
.\run_webui.bat
```

راقب:

```
[doctor] native crash detected (0xC0000409); reinstalling tokenizers...
[doctor] using faster-whisper on CPU for this run to avoid the native crash.
```

ثم أعد المعالجة. إذا نجح، فالمشكلة كانت في المسار الأصلي لـWhisperX/
tokenizers، وقد تجاوزها التطبيق.

للاستمرار على faster-whisper يدوياً:

```powershell
$env:VIRALCUTTER_TRANSCRIPTION_BACKEND = "faster-whisper"
$env:VIRALCUTTER_TRANSCRIPTION_FORCE_CPU = "1"
.\run_webui.bat
```

## 4. التحقق

- `tests/test_webui_utils.py` — 3 اختبارات جديدة:
  - رمز الانهيار يعطي تلميح 0xC0000409 وfaster-whisper.
  - مسار `whisperx` في تتبّع لا يُصنَّف «غير مثبّت».
  - `No module named 'whisperx'` ما زال يعطي أمر التثبيت.
- المجموعة كاملة خضراء، `ruff` نظيف.

## 5. حدود

- إن كان الانهيار في **CTranslate2 نفسه** وليس tokenizers/pyannote، فقد
  يفشل faster-whisper على CUDA أيضاً؛ لذلك يُفعَّل CPU في وضع الإنقاذ.
- إذا تكرر الانهيار على CPU مع faster-whisper، فالسبب أعمق (تعريف/مكتبة
  نظام). عندها أرسل آخر سطر من `--ensure --json`؛ لن يخمّن التطبيق.
- هذا لا يصلح ملفات نموذج تالفة؛ إن كان `large-v3-turbo` تالفاً، جرّب
  `large-v3`.
