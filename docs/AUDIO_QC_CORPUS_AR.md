# corpus عربي مرخّص لمعايرة Audio QC — الدليل العملي

> **الفجوة التي يغلقها هذا الدليل:** عتبات Audio QC الافتراضية (-16 LUFS / -1.5 dBTP)
> قيم بث عامة، لا قيم مُعايَرة على صوت عربي حقيقي. `scripts/audio_qc_calibrate.py`
> يحسب العتبات من corpus مرجعي، و`scripts/audio_qc_corpus.py` يبني ذلك الـ corpus
> **مع إثبات الترخيص** — فلا معايرة على بيانات مجهولة المصدر.

---

## 1) اختر مصدر الـ corpus

| المصدر | الترخيص | ملاحظات |
|---|---|---|
| **تسجيلاتك الخاصة** (موصى به) | `own-recording` | الأفضل عملياً: صوتك/محتواك هو ما سيُقاس في الإنتاج، وبلا أي مشكلة ترخيص |
| **Mozilla Common Voice (العربية)** | CC0 | نزّلها بنفسك من <https://commonvoice.mozilla.org/ar/datasets> (حساب مجاني) — التنزيل مقصود كي تبقى موافقة الترخيص قراراً بشرياً |
| أي مجموعة مرخّصة أخرى | `cc-by-4.0` / `custom` | `custom` يتطلب `--license-note` تصف الشروط |

> `audio_qc_corpus.py` لا يُنزّل أي شيء تلقائياً. هذا مقصود: لا يجوز أن يقرر
> البرنامج نيابة عنك قبول شروط مجموعة بيانات.

## 2) بناء الـ corpus

### من تسجيلاتك الخاصة

```powershell
python -m scripts.audio_qc_corpus init corpus_ar --source "تسجيلات القناة 2026" --license own-recording
python -m scripts.audio_qc_corpus add corpus_ar D:\recordings\*.wav
python -m scripts.audio_qc_corpus status corpus_ar
```

### من Common Voice

بعد فك ضغط التنزيل الرسمي (يحتوي `clips/` و`validated.tsv`):

```powershell
python -m scripts.audio_qc_corpus import-cv corpus_ar D:\datasets\cv-ar --limit 300
python -m scripts.audio_qc_corpus status corpus_ar
```

يُستورد من `validated.tsv` فقط، ويُنشأ manifest بترخيص CC0 تلقائياً.

## 3) المعايرة

```powershell
python -m scripts.audio_qc_corpus calibrate corpus_ar
```

- ينتج `corpus_ar/audio_qc_thresholds.json` بعتبات مبنية على المئينات
  (P5/P50/P95) مع حدود أمان — حتى corpus منحرف لا يجرّ العتبات إلى قيم خطرة.
- يحتاج **5 مقاطع قابلة للقياس على الأقل** (ارفعها عبر `--min-files` للدقة).
- يفشل مغلقاً إذا لم يكن للـ corpus manifest بترخيص معروف.

## 4) استخدام العتبات

```powershell
# معايرة لكل مشروع
python -m scripts.audio_qc --project VIRALS\my-project --thresholds-file corpus_ar\audio_qc_thresholds.json
```

كل مقطع يُقاس بعدها على صوتك الحقيقي بدل ثوابت عامة.

## 5) التفاصيل التقنية

- **التطبيع:** كل ملف يُحوَّل إلى MP4 صغير (إطار أسود 1fps + صوت أحادي 16kHz AAC) —
  سطح قياس موحّد للجهارة والذروة والصمت، وحجم ضئيل، وحاوية يقبلها ماسح المعايرة.
- **القراءة فقط على المصدر:** لا يُعدَّل ملفك الأصلي؛ النسخة المطبوعة تُبنى في
  `corpus_ar/clips/`.
- **الحتمية:** نفس الـ corpus يعطي نفس العتبات.
- **الأمان:** manifest بترخيص معروف إلزامي قبل `add` وقبل `calibrate`؛
  `custom` بلا وصف يُرفض.

## 6) حدود هذا الحل

- تنزيل Common Voice يحتاج حساباً مجانياً وقبول الشروط منك — لذلك لا يُؤتمت.
- معايرة **جودة المحتوى** (هل يبدو الصوت «احترافياً») خارج النطاق؛ العتبات تقيس
  الجهارة والذروة والصمت فقط.
