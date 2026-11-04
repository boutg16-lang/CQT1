# تقرير التحديث العربي — OUSSAMA Cutter 7.52.0

## ما تم تغييره

- تحسين `scripts/seo_titles.py` عند تمرير `transcript_text`.
- أصبح مسار العناوين المعتمد على نص المقطع يستخرج المرشحين من كلام المقطع نفسه، بدلاً من قوالب تضيف ادعاءات تسويقية أو أرقاماً عشوائية.
- كل عنوان في هذا المسار يخضع للتحقق الحتمي عبر `scripts/title_factual.py` مقابل نص المقطع، وتُستبعد العناوين المرفوضة.
- إضافة بيانات مصدر العنوان `title_source=exact_clip_transcript` ووسم `grounded=True` لتسهيل المراجعة في الواجهة والأنظمة اللاحقة.
- ضبط عدد العناوين: من 0 إلى 20، وإرجاع قائمة فارغة عند طلب صفر أو عند غياب نص المقطع في وضع التوليد المعتمد على النص.
- إضافة اختبارات انحدار للعناوين العربية المعتمدة على نص المقطع.

## اختبارات

نجحت الاختبارات المركزة التالية:

- `tests/test_v722_seo_scheduler.py`
- `tests/test_transcript_title_regressions.py`
- `tests/test_v740_clip_quality.py`
- `tests/test_distinct_clip_selection.py`
- `tests/test_pro_clip_review.py`
- `tests/test_v741_title_validation.py`

اجتازت مجموعة الاختبارات الكاملة `uv run --no-sync pytest -q`: **2,223 passed, 2 skipped**. كما نجح `uv run --no-sync ruff check .` و`uv lock --check` و`uv run --no-sync python -m compileall -q -x '/(\.git|\.venv)/' .`.

## الأمان والتوزيع

- لم يتم تضمين `api_config.json` أو أي ملف `.env` أو مخازن أسرار محلية في حزمة التوزيع.
- استخدم `api_config.example.json` كقالب، ثم أضف مفاتيحك محلياً على جهازك.
- لم يتم تغيير إصدارات Python أو PyTorch أو CUDA أو WhisperX.

## حدود هذا التحديث

هذا تحديث فعلي لمسار توليد العناوين عند توفر نص المقطع، وليس إعادة كتابة شاملة لمحرك اختيار المقاطع أو واجهة Gradio. محرك تقييم المقاطع الموجود في `scripts/clip_scoring.py` بقي دون تغيير لتجنب تغيير سلوك الاختيار قبل تدقيق تكامله مع جميع مسارات الإنتاج.
