# تقرير التدقيق والتقوية — 2026-10-04

**الفرع:** `ai/comprehensive-audit-and-hardening`
**النقطة المرجعية:** `main` = `e2e8904`
**البيئة:** Python 3.10.12، بيئة معزولة عبر `uv` + `requirements-dev.txt`

## 1) خط الأساس (Baseline)

| الفحص | النتيجة |
|---|---|
| `ruff check .` | ✅ نظيف |
| `python -m compileall -q .` | ✅ نظيف |
| `pytest` | ✅ **1903 ناجح، 1 متخطّى** (قبل التغييرات) |
| Python | 3.10.12 محلياً؛ CI على 3.10/3.11/3.12 |
| ملفات المصدر | 280 ملف `.py`؛ 139 ملف اختبار |

لا توجد أسرار مضمّنة في المستودع (النتائج الوحيدة لأنماط مفاتيح كانت قيَم اختبار وهمية في `tests/`).

## 2) النتائج والإصلاحات

### أمنية — تم إصلاحها

| # | الموضع | المشكلة | الخطورة | الإصلاح |
|---|---|---|---|---|
| 1 | `scripts/content_guard.py` (`filter_segments`) | فشل استيراد طبقة السلامة الدلالية كان يُترجم إلى `analyze_text=None` فيُتخطى فحص السياسة بصمت (**fail-open**) | عالية | `_load_semantic_tools()` يبلّغ عن الخطأ، وحينها **يُمنع** كل مرشّح بسبب `semantic_safety_unavailable` (fail-closed) |
| 2 | `scripts/upload_gate.py` (`check_clip`) | `except Exception: pass` حول فحص السلامة الدلالي لبيانات النشر → تمرير بيانات غير مُدقّقة | عالية | صار يسجّل سبباً بخطورة عالية ويُمنع الرفع الآلي (fail-closed) |
| 3 | `scripts/upload_gate.py` (`_save_token`) | كتابة توكن OAuth بصلاحيات افتراضية (قد تكون مقروءة للمجموعة) عبر اسم مؤقت متوقّع | متوسطة | إنشاء الملف المؤقت بصلاحية `0600` من البداية + `fsync` + استبدال ذرّي |
| 4 | `scripts/risk_scorecard.py` | فشل كتابة/حذف `publish_blocklist.json` كان صامتاً (بوابة الرفع تقرأ هذا الملف) | متوسطة | صار يُطبع خطأ واضح |
| 5 | `scripts/safety_filter.py` | فشل تحميل معجم السياسات كان يُسقط مصطلحات إضافية بصمت | منخفضة | صار يُطبع تحذير |
| 6 | `.gitignore` | لا يغطّي `token.json`, `client_secrets*.json`, `*.pem`, `.viralcutter/` | منخفضة | أُضيفت |

### CI/CD — تمت تقويتها

| # | الموضع | التغيير |
|---|---|---|
| 7 | `.github/workflows/build-exe.yml` | `contents: write` انتقل من مستوى الـ workflow إلى **مستوى الوظيفة فقط**، مع افتراضي `contents: read` |
| 8 | `.github/workflows/build-exe.yml` | تثبيت الـ action الخارجي `softprops/action-gh-release` على **SHA** (`efb3536…`) لأنه يعمل بصلاحية كتابة |
| 9 | كل ملفات workflows | إضافة `concurrency` (إلغاء التشغيلات المكررة) و`timeout-minutes` لكل وظيفة |

> **ملاحظة مقصودة:** لم أفصل البناء عن الإصدار في وظائف منفصلة (وهو التقويس الأقصى للصلاحيات) لأن الوظيفتين في نفس المهمة تحمي إصلاح **Issue #26** (فشل حصة الـ artifacts ما كان يمنع الإصدار). الفصل سيُعيد تلك العلّة.

### الجودة والاتساق — تم إصلاحها

| # | الموضع | المشكلة | الإصلاح |
|---|---|---|---|
| 10 | `pyproject.toml` | `target-version = "py39"` يناقض `requires-python >=3.10` | صار `py310`، ومعها أُصلحت 18 موضع `zip()` بلا `strict=` (إضافة `strict=False` محافظة على السلوك) |
| 11 | `pyproject.toml` | تعليق قديم «the UI displays 7.32.4-pro» | صار `7.51.0-pro` |
| 12 | `.github/CODEOWNERS` | يشير إلى `/ruff.toml` غير موجود | صار `/pyproject.toml` |
| 13 | `CONTRIBUTING_QUICKSTART.md` | يصف `ruff.toml` وطول سطر 100 (غير صحيح) | وُصف الإعداد الفعلي في `pyproject.toml` |
| 14 | `README_ar.md` | أمر `export_blocklist_pack --version 3` وقديم، وعدد المصطلحات «296 (v4)» | صار `--version 6` و«336 مصطلحاً (v6)» |
| 15 | `README*.md` | شارة الاختبارات تقول 1238 | صارت 1903 |

### اختبارات جديدة
`tests/test_security_hardening_v752.py` — 5 اختبارات تغطي: fail-closed في `content_guard`، وسلوك المسار السليم، وfail-closed في `upload_gate`، وصلاحيات ملف التوكن `0600`، وتغطية `.gitignore`.

## 3) بنود موصى بها للمالك (لم تُنفَّذ — تتطلب قرار المالك)

1. **`CODEOWNERS` (أولوية عالية):** المالك الافتراضي `@mostafabonnif-beep` يختلف عن مالك المستودع `boutg16-lang`. إن لم يكن هذا الحساب متعاوناً بصلاحية كتابة، فلن تستطيع قاعدة «code owner review» أن تُفعّل مراجعاً وقد تمنع الدمج. يجب التحقق/التصحيح من إعدادات المالك.
2. **تثبيت كل الـ actions على SHA:** ثبّتُّ الطرف الثالث فقط؛ تثبيت `actions/*` أيضاً خيار (Dependabot يدير `github-actions`).
3. **تحقق المجموع الاختباري لـ ffmpeg/fpcalc** في `build-exe.yml` (تنزيلها حالياً بلا تحقق).
4. **gitleaks في CI:** غير مُفعّل server-side (يحتاج allowlist لأن قيم الاختبار تُشبّه المفاتيح).
5. **وثائق قديمة تحتاج إعادة كتابة:** `docs/DEVELOPER_HANDOVER.md` و`docs/REMAINING_AFTER_V6_9.md` (متجمّدة عند v4–v6.13، ومراجع commits/tags لم تعد موجودة).

## 4) التحقق النهائي

| الفحص | النتيجة |
|---|---|
| `ruff check .` | ✅ نظيف |
| `compileall -q .` | ✅ نظيف |
| `pytest` | ✅ **1908 ناجح، 1 متخطّى** |
| YAML لملفات workflows | ✅ صالح |

> راجع `AGENTS.md`: كل تغييرات `.github/workflows/` وملفات السلامة موثّقة هنا وتحتاج مراجعة Code Owner قبل الدمج.
