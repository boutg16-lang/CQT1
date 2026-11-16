# OUSSAMA Cutter 8.8.0-ultimate — ذاكرة تكيفية وملف صحة

_التاريخ: 2026-11-14 · النطاق: سلّم OOM + قرار الطبيب يصل للتطبيق_

## 1. لماذا

الإصلاحات السابقة كانت تتعامل مع كل عطل بعد ظهوره. بقي سؤالان:

- إذا كان OOM حتى مع نافذة 300 ثانية، هل يستسلم؟
- إذا قرّر الطبيب أن faster-whisper/CPU هو المسار الآمن، كيف يعرف
  التطبيق ذلك إن شُغّل دون `run_webui.bat`؟

## 2. سلّم الذاكرة التكيفي

`transcription_fallback` لا يستسلم عند أول OOM. يجرّب نوافذ أصغر
تلقائياً:

```
النافذة المطلوبة (افتراضي 300) → 180 → 120 → 90
```

- يكتشف `MemoryError` / `Unable to allocate` / `Out of memory` /
  `bad allocation`.
- يعيد التقسيم والتفريغ بالنافذة الأصغر، بنفس النموذج المحمّل.
- يسجّل في النتيجة `chunk_seconds` الفعلي و`chunks`.
- إذا فشلت كل النوافذ، يرفع آخر OOM مع رسالة صريحة.
- الخطأ غير المتعلق بالذاكرة لا يُعاد (لا إخفاء).

مثال: 29 دقيقة فشلت عند 383 MiB → 300ث (~65 MiB) → 180ث (~40 MiB).

## 3. ملف الصحة

الطبيب يكتب `.transcription_health.json`:

```json
{
  "backend": "faster-whisper",
  "force_cpu": true,
  "chunk_seconds": 120,
  "reason": "VAD probe failed: ...",
  "written_at": "2026-11-14T..."
}
```

- `backend`: whisperx عند نجاح VAD، وإلا faster-whisper.
- `force_cpu`: true عند فشل VAD (مسار الإنقاذ).
- `chunk_seconds`: 300، أو 180/120 حسب الذاكرة الحرة.
- `webui/app.py` يقرأ الملف عند الإقلاع ويطبّقه بـ`os.environ.setdefault`.
  - متغيرات المستخدم الصريحة **تتفوق دائماً**.
  - الملف ينتهي بعد 7 أيام، فلا يثبّت التطبيق على fallback قديم.
- `run_webui.bat` يظل يفرض faster-whisper/CPU عند الانهيار الأصلي.

## 4. التشغيل

```powershell
cd D:\OS
git pull
.\run_webui.bat
```

لا خطوات إضافية. لفحص الحالة:

```powershell
.\.venv\Scripts\python.exe -m scripts.transcription_diagnostics --ensure --json
Get-Content .transcription_health.json
```

لإلغاء قرار قديم:

```powershell
Remove-Item .transcription_health.json
```

## 5. التحقق

- `tests/test_transcription_health.py` — 4 اختبارات:
  - حجم النافذة حسب RAM.
  - التطبيق بـsetdefault وتفوق متغير المستخدم.
  - تجاهل ملف أقدم من 7 أيام.
  - الطبيب يكتب ملفاً صحيحاً.
- `tests/test_transcription_chunking.py` — اختبار OOM: النافذة 300 تفشل،
  180 تنجح، والنتيجة تحمل النافذة الفعلية.
- المجموعة كاملة خضراء، `ruff` نظيف.

## 6. حدود

- سلّم الذاكرة يقلّل النافذة، لا حجم النموذج. إذا كان النموذج نفسه لا
  يتسع في RAM، اختر `medium`/`small`.
- ملف الصحة قرار تشغيلي، لا إصلاح للأعطال الأصلية؛ الانهيار الأصلي
  يعالجه مشغّل التشغيل.
- النوافذ بلا تداخل؛ قد تُقطع كلمة عند الحد نادراً.
