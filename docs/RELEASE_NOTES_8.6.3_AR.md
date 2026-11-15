# OUSSAMA Cutter 8.6.3-ultimate — التفريغ لا يسقط بسبب VAD

_التاريخ: 2026-11-14 · النطاق: تحويل تلقائي إلى faster-whisper عند عطل pyannote_

## 1. المشكلة

8.6.2 أضاف التبعيات الصريحة والتحقق منها، لكن إن فشل تثبيت
`asteroid_filterbanks` لأي سبب (شبكة، صلاحيات، نسخة، حصة)، كان WhisperX
يرفع الخطأ الخام ويتوقف التفريغ كله:

```
ModuleNotFoundError: No module named 'asteroid_filterbanks'
```

المستخدم لا يهمّه أي مكتبة سقطت؛ يهمّه أن يحصل على تفريغ.

## 2. الحل

### أ. تحويل تلقائي عند عطل VAD
`transcribe_video.py` يميّز أعطال بيئة VAD عن أخطاء الاستخدام:

- `_is_vad_dependency_error()`: `ImportError`/`ModuleNotFoundError`، أو أي
  خطأ يذكر `asteroid_filterbanks` أو `pyannote`.
- عند اكتشافها، ينتقل فوراً إلى `faster-whisper` الذي يملك VAD خاصاً
  (Silero) ولا يحتاج pyannote إطلاقاً.
- تُكتب نفس ملفات `SRT/TSV/JSON` بنفس الأسماء، فلا تتغير أي مرحلة لاحقة.
- يُطبع السبب بوضوح:
  `pyannote VAD is unavailable — switching to the independent faster-whisper backend`.
- المرحلة الوحيدة التي تُتخطى هي محاذاة wav2vec2؛ faster-whisper يعطي
  توقيتات كلمات بنفسه.
- إن فشل CUDA داخل faster-whisper (cuDNN/CTranslate2)، يعيد المحاولة على
  CPU بدل إسقاط الجلسة.

### ب. فشل مزدوج = رسالة واضحة
إذا فشل faster-whisper أيضاً، تُرفع `TranscriptionUnavailableError` تحمل
**الخطأين** معاً (سبب WhisperX وسبب البديل)، لا خطأ واحداً مضللاً.

### ج. لا يغيّر السلوك الافتراضي
- من يريد WhisperX صراحةً: `VIRALCUTTER_TRANSCRIPTION_BACKEND=whisperx`
  (أو من الواجهة) — لا تحويل صامت.
- من يريد faster-whisper مباشرةً: `VIRALCUTTER_TRANSCRIPTION_BACKEND=faster-whisper`.
- الافتراضي `auto`: WhisperX أولاً، والبديل عند الحاجة.

## 3. الإصلاح الفوري عندك

الأفضل أن تثبّت الحزمة لتعود جودة WhisperX الكاملة:

```powershell
cd D:\OS
uv pip install --python ".venv\Scripts\python.exe" `
  "asteroid-filterbanks>=0.4" "einops>=0.6.0" `
  "omegaconf>=2.1,<3.0" "soundfile>=0.12.1"
```

وتحقق:

```powershell
.\.venv\Scripts\python.exe -c "import asteroid_filterbanks; from pyannote.audio.models.segmentation import PyanNet; print('VAD OK')"
```

إن تعذّر ذلك، حدّث إلى 8.6.3 وشغّل التفريغ: سيكمل تلقائياً عبر
faster-whisper. ابحث في السجل عن:

```
[transcribe] Backend: faster-whisper fallback
```

## 4. التحقق

- `tests/test_transcribe_vad_fallback.py` — اختباران:
  - تمييز أخطاء VAD عن أخطاء الاستخدام.
  - `transcribe()` ينتقل فعلياً إلى البديل عند
    `ModuleNotFoundError: asteroid_filterbanks` ويمرّر `reason="vad"`.
- المجموعة كاملة خضراء، `ruff` نظيف.

## 5. حدود

- faster-whisper لا يقوم بمحاذاة wav2vec2، لكنه يعطي توقيتات كلمات؛
  اختيار المقاطع يعمل، وقد تقل دقة حدود الكلمة قليلاً.
- إن كنت تستخدم diarization (تمييز المتحدثين) فهي تعتمد pyannote؛ ستبقى
  معطلة حتى تُصلح الحزمة. التفريغ العادي لا يحتاجها.
