# قناة التحديث البديلة — `scripts/update_manifest.py`

> **المشكلة التي تحلّها (Issue #27):** `auto_updater` يقرأ إصدارات GitHub بطلبات
> **مجهولة**، والمستودع **خاص** — فالمحدّث لا يرى شيئاً، وحتى بعد إصلاح #25/#26
> يبقى زرّ التحديث بلا وظيفة. الحل هنا: **قناة مستقلة عن GitHub Releases**.

---

## 1) الفكرة

ينشر المالك الملف التنفيذي **في أي مكان** (موقعه، تخزين سحابي، قرص مشترك، مستودع
إصدارات عام منفصل) بجانب ملف صغير:

```json
{
  "schema": 1,
  "version": "7.50.0-pro",
  "published_at": "2026-09-29T…",
  "notes": "…",
  "assets": {
    "windows": {"url": "https://example.com/dl/OUSSAMA-Cutter.exe", "sha256": "…64 hex…"},
    "linux":   {"url": "https://example.com/dl/oussama-cutter-linux",  "sha256": "…"}
  },
  "signature": "base64(Ed25519 over canonical JSON)"
}
```

التوقيع يغطي **البصمات**، والبصمات تغطي **الملف** — فسلسلة الثقة كاملة:
توقيع ← manifest ← sha256 ← البرنامج. و`download_update` يتحقق من الـsha256
قبل تبنّي أي ملف (fail-closed كما كان).

## 2) عند المالك — إنشاء الـ manifest

```powershell
# توليد مفاتيح التوقيع مرة واحدة (تُستعمل أيضاً لتوقيع artifacts)
python -m scripts.sign_artifacts init --key-dir keys

python -m scripts.update_manifest create `
  --version 7.50.0-pro `
  --windows dist\OUSSAMA-Cutter.exe `
  --base-url https://example.com/dl `
  --notes "تحصين + دبلجة" `
  --out dist\update_manifest.json `
  --sign --key keys\maintainer-signing.key
```

ارفع `dist\OUSSAMA-Cutter.exe` و`update_manifest.json` إلى أي مكان عام.

## 3) عند المستخدم — التفعيل

```powershell
$env:VIRALCUTTER_UPDATE_CHANNEL="manifest"
$env:VIRALCUTTER_UPDATE_MANIFEST="https://example.com/dl/update_manifest.json"
$env:VIRALCUTTER_UPDATE_PUBLIC_KEY="keys\maintainer-signing.pub"
python -m scripts.auto_updater --check
```

| المتغيّر | القيمة | الأثر |
|---|---|---|
| `VIRALCUTTER_UPDATE_CHANNEL` | `github` (افتراضي) / `manifest` / `off` | اختيار القناة أو إيقاف الفحص |
| `VIRALCUTTER_UPDATE_MANIFEST` | رابط أو مسار محلي | مصدر الـ manifest |
| `VIRALCUTTER_UPDATE_PUBLIC_KEY` | مسار المفتاح العام | **إلزامي** إن كان الـ manifest موقَّعاً |
| `VIRALCUTTER_ALLOW_UNSIGNED_UPDATE` | `1` | قبول manifest بلا توقيع (غير موصى به، اختياري صريح) |

## 4) قواعد الفشل المغلق

- manifest **بلا توقيع** ⇒ يُرفض، إلا مع `VIRALCUTTER_ALLOW_UNSIGNED_UPDATE=1`.
- manifest **موقَّع وبلا مفتاح عام** ⇒ يُرفض (لا يمكن التحقق).
- **تعديل أي حقل** بعد التوقيع ⇒ التوقيع يسقط ⇒ يُرفض.
- **لا أصل لهذه المنصة** في الـ manifest ⇒ يُرفض بلا تنزيل.
- انقطاع الشبكة أو ملف مفقود ⇒ لا عملية (تطبيقك يقلع عادياً كما كان).

### تحصينات إضافية (v7.52)

- **مكافحة الترجيع (anti-rollback):** `--download` يرفض ما ليس أحدث من النسخة
  الحالية، و`--apply` يرفض أي ملف مُجهَّز وسيمه ليس أحدث — فإعادة بثّ manifest
  قديم موقَّع لا تُنزِّل النسخة (تجاوز يدوي واعٍ عبر `--force`).
- **إصدارات صارمة:** لاحق الإصدار (`rc1`, `pro2`…) لا يتسرَّب إلى المقارنة —
  `7.51.0-rc1` أقدم من `7.51.0-pro` كما يجب.
- **لا وحدات بايت غير موقَّعة:** `fetch_verified_manifest` يُرجع النسخة
  **المُطبَّعة** التي وقَّعها المفتاح فعلاً؛ أي مفتاح JSON دخيل يُطرح.
- **إعادة فحص عند التطبيق:** الـ sha256 المسجَّل وقت التنزيل يُعاد حسابه قبل
  الاستبدال، فأي تعديل للملف المُجهَّز بعد التحقق يُرفض.
- **سقف حجم الـ manifest** (2 MiB) ولا إعادة طلب بلا مهلة عند خطأ داخلي.
- **`VIRALCUTTER_ALLOW_UNSIGNED_UPDATE`** بمُحلِّل موحَّد بين الوحدتين.
- **جذر ثقة مثبَّت داخل الحزمة:** عندما يولّد المالك مفاتيح التوقيع
  (`python -m scripts.sign_artifacts keygen --key-dir keys`) يُضمَّن
  `keys/maintainer-signing.pub` **تلقائياً** في بناء PyInstaller (راجع
  `packaging/viralcutter.spec`)، فيتحقق المحدّث به افتراضياً بلا أي متغيّر
  بيئة. المفتاح الخاص (`*.key`) محميّ بـ `.gitignore` ولا يُنشَر أبداً؛
  العام (`*.pub`) يمكن تتبّعه في المستودع. ويظل `VIRALCUTTER_UPDATE_PUBLIC_KEY`
  تجاوزاً صريحاً للمشغّل — مع إشعار مرّة واحدة على stderr لأنه يعيد تجذير
  الثقة (ويُذكَر صراحةً إن كان قد حلّ محل مفتاح مثبَّت).

## 5) لماذا هذا مهم أكثر من كونه خياراً

القناة الحالية (`github`) تحتاج: مستودعاً عاماً + Releases + `checksums.txt`.
هذه القناة تحتاج: **رابط ملف واحد**. لذلك:

- تعمل والمستودع خاص.
- تعمل وبلا GitHub إطلاقاً (سيرفرك، Drive، أي شيء).
- لا تعتمد على حصة artifacts ولا على صلاحية Workflows (#25/#26/#27).

## 6) التحقق

```powershell
python -m scripts.update_manifest verify dist\update_manifest.json --key keys\maintainer-signing.pub
```

ويوجد **31 اختباراً** تغطي: البناء، التوقيع، كشف التعديل، رفض غير الموقَّع، اختيار
أصل المنصة، التحميل من ملف/رابط، وتكامل `auto_updater` مع مسارات الفشل.
