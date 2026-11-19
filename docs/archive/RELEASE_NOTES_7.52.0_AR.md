# ملاحظات إصدار OUSSAMA Cutter 7.52.0-pro

**الإصدار:** 7.52.0-pro
**التاريخ:** 2026-10-04
**النوع:** تقوية أمنية + جودة + CI (بدون تغيير في واجهة الاستخدام)

## الملخص
جولة تدقيق شاملة أغلقت ثغرات **fail-open** في بوابات الأمان ومنع التكرار، وشدّدت
صلاحيات CI، وأصلحت انحراف الإصدارات والوثائق، مع اختبارات انحدار لكل إصلاح سلوكي.

## الأمان
- **بوابات fail-closed:** عند تعذّر تشغيل فحص السلامة الدلالي (`content_guard`,
  `upload_gate`) يُمنع التصدير الآلي بدل تمريره بصمت.
- **منع التكرار:** فشل الفحص البصري (perceptual) والفحص عبر المشاريع صار يُسجّل
  **ويُمنع** (`perceptual_check_unavailable` / `cross_project_check_unavailable`).
- **حقوق الموسيقى:** فشل الفحص يُسجّل؛ وفي وضع `block` يرفض الرفع.
- **توكنات OAuth:** تُكتب بصلاحية `0600` من الإنشاء + `fsync` + استبدال ذرّي.
- **`.gitignore`:** يغطّي `token.json`, `client_secrets*.json`, `*.pem`, `.viralcutter/`.

## CI / سلسلة التوريد
- `contents: read` افتراضياً في كل الوظائف، و`contents: write` على وظيفة الإصدار فقط.
- تثبيت `softprops/action-gh-release` على SHA.
- `concurrency` + `timeout-minutes` لكل الـ workflows.

## الجودة
- `ruff target-version = py310` (مطابقةً لـ `requires-python >=3.10`) وإصلاح 18 موضع
  `zip(strict=False)`.
- اختبارات جديدة: بوابات fail-closed، صلاحيات التوكن، حارس انحراف قائمة الأمان
  (`safety_blocklist.json` == `safety_filter.BLOCKLIST`)، ووحدات القص/الترجمة
  منخفضة التغطية.

## التحقق
- `ruff check .` نظيف.
- `python -m compileall -q .` نظيف.
- `pytest`: كل الاختبارات ناجحة على Python 3.10/3.11/3.12 + Windows (CI).

## الترقية
- التثبيتات الحالية تستقبل التحديث من قناة الـ manifest الموقّعة (fail-closed).
- لا تغيير مطلوب في الإعدادات أو ملفات المشروع.
