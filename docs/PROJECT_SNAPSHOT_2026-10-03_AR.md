# OUSSAMA Cutter — حزمة محسّنة (2026-10-03)

هذه نسخة كاملة من المشروع عند آخر حالة محلية بعد جولات التحسين، **تشمل إصلاحات لم تُدفع بعد إلى GitHub** (لأن وصول التطبيق إلى المستودع كان منقطعاً وقت التسليم).

- الإصدار: `7.51.0-pro` (app_version.py)
- آخر commit محلي: `5710035`
- الملفات: 420 ملف مصدري (+ هذا التوثيق)
- لم يُضمَّن: المجلد `.venv` والـ `__pycache__` و`dist/` (تُبنى عند التشغيل) والمفاتيح الخاصة.

---

## ما تم تحسينه في هذه الحزمة (فوق آخر نسخة على GitHub)

### 1) تحصين أمني لقناة التحديث الموقَّعة (`scripts/update_manifest.py`, `scripts/auto_updater.py`)
- **مكافحة الترجيع:** لاحق الإصدار لم يعد يتسرّب إلى المقارنة — `7.51.0-rc1` صار **أقدم** من `7.51.0-pro` (سابقاً كان يُحسب أحدث فتحاً لمسار ترقية-رجوع بإعادة بثّ manifest موقَّع قديم).
- `--download` يرفض ما ليس أحدث، و`--apply` يتحقق من الإصدار قبل الاستبدال (تجاوز واعٍ بـ `--force`).
- إعادة حساب sha256 للملف المُجهَّز قبل الاستبدال (إغلاق TOCTOU).
- `fetch_verified_manifest` يُرجع **النسخة الموقَّعة المُطبَّعة** فقط — لا تمرّ حقول JSON دخيلة.
- سقف 2 MiB لقراءة الـ manifest، ومُحلِّل موحَّد لـ `VIRALCUTTER_ALLOW_UNSIGNED_UPDATE`.

### 2) جذر ثقة مثبَّت داخل الحزمة
- عندما ينشئ المالك مفاتيح التوقيع، يُضمَّن `keys/maintainer-signing.pub` **تلقائياً** في بناء PyInstaller، ويتحقق المحدّث به افتراضياً بلا متغيّر بيئة.
- `.gitignore` يمنع `keys/*.key` نهائياً (المفتاح الخاص لا يُنشر أبداً).

### 3) الرقعة الجاهزة للإشو #25 و #26 (مُدمجة في `.github/workflows/`)
- `build-exe.yml`: متغير `EXE_NAME` موحّد مع `packaging/viralcutter.spec`، فحص فوري عند التعارض، smoke test محصّن بمهلة نهائية، توليد `dist/checksums.txt` (SHA-256)، وخطوة الـ Release لم تعد تُحجب بفشل رفع الـ artifacts.
- `ci.yml`: تشغيل `pytest` **قبل** رفع الـ artifacts + `retention-days` — فلا يخفي امتلاء الحصة نتيجة الاختبارات.
- إزالة الـ shim المؤقت من `packaging/viralcutter.spec`.

### 4) تفكيك الملف العملاق `main_improved.py`
- **2263 ← 2021 سطراً** بلا أي تغيير في السلوك:
  - `scripts/pipeline_utils.py` — أدوات الأنبوب (بروتوكول `PROGRESS|`، بوابة verbose، تنظيف الملفات المؤقتة، لوحة ألوان الترجمة، مساعدات JSON).
  - `scripts/cli_args.py` — محلّل سطر الأوامر كاملاً (دالة نقية).

### 5) شبكة أمان وتغطية اختبارية جديدة
- `tests/test_export_xml_lib.py` — **44 اختباراً** لـ `scripts/export_xml_lib/` (837 سطراً حياً كانت بلا أي تغطية) حتى التصدير النهائي لـ Premiere.
- `tests/test_pipeline_utils.py` و`tests/test_cli_args.py`.

### 6) إصلاح علّة حقيقية في تصدير Premiere
- `scripts/export_xml_lib/xml_generator.py` كان يُدرج أسماء المشاريع والملفات بلا تهريب XML، فمشروع اسمه `A & B` ينتج ملف XMEML **لا يفتحه Premiere**. الآن كل النصوص المستخدمية مُهرَّبة.

### 7) ترجمات كاملة
- **البرتغالية (pt_BR): 180 سلسلة** و**التركية (tr_TR): 173 سلسلة** كانت تظهر بالإنجليزية في الواجهة — تُرجمت كاملة مع الحفاظ على العلامات والـ placeholders.

### 8) تنظيف
- حُذف كود ميت بلا أي مرجع: `scripts/generate_premiere_xml.py` (نسخة منحرفة 277 سطراً) و`scripts/transcribe_cuts.py`.
- `.gitignore` صار يغطي مخرجات تقارير CI.

---

## التحقق
- **1889 اختباراً — كلها ناجحة** (0 فشل، 1 متخطّى) على Python 3.10.
- `ruff check .` نظيف.

## التشغيل
1. ويندوز: شغّل `install_dependencies.bat` ثم `run.bat` (أو `run_webui.bat` للواجهة).
2. لينكس/ماك: `./install_linux.sh` أو `./install_macos.sh` ثم `./run.sh`.
3. للتحقق محلياً: `python -m pytest -q` و`python -m ruff check .`.

## خطوات مطلوبة منك على GitHub (لتفعيل CI والإصدارات)
1. **صلاحية Workflows:** `github.com/settings/installations` ← وافق على طلب صلاحية Workflows المعلَّق (أو أعد تثبيت التطبيق)، وراجع أن المستودع `cat` ضمن نطاق التثبيت.
2. **حصة الـ artifacts:** من صفحة المستودع → Actions → Artifacts → احذف القديمة.
3. بعدها: `git pull` ثم `git push` من جهازك إن أردت رفع الكوميتات المحلية، أو `python -m scripts.apply_ci_workflow_fix --apply --push`.

## ملاحظات معروفة (لم تُصلَح بعد، موثّقة)
- `scripts/export_xml_lib/xml_generator.py`: مُعرّفات `uuid4` تجعل ناتج XML غير حتمي البايتات.
- `utils.get_video_dims` docstring قديم (يوثّق 3 قيم ويعيد 4).
- audio clipitem في XML يحمل مُعرّفاً لا يطابق أي `<file>` (يتسامح معه Premiere غالباً).
- الطوابع الزمنية السالبة في `utils.py` تُنسَّق بغرابة.
- قرار الإشو #27 (فتح المستودع أو إبقاؤه خاصاً) يبقى قرارك — قناة الـ manifest الموقَّعة تعمل والمستودع خاص.
