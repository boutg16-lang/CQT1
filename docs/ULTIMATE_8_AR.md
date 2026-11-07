# OUSSAMA CUTTER 8.0 ULTIMATE — ملاحظات الإصدار

## المكونات
- PRODUCTION STUDIO: تبويب إنتاج يوجّه إلى أدوات الإنتاج والتحرير القائمة في Create New؛ لم تُحذف الأدوات القديمة أو تُستبدل.
- YOUTUBE CONTENT PROTECTOR: رفع فيديو مستقل، تحليل تفريغ الكلام والسياق، فحص OCR لعينات الإطارات، عرض تقارير JSON، وحقل تعديلات يبدأ فارغاً ويتطلب إدخالاً ومراجعة بشرية قبل المعالجة.
- SMART SANITIZER: mute / bleep / cut مع حفظ الملف الأصلي، تحقق FFprobe، بصمات SHA-256، وتقرير تدقيق.
- SRT: إخفاء نصوص الفترات المكتومة/المصفرة وإعادة توقيت الترجمات حول القص.
- POST-RENDER REVIEW: يعاد فحص الكلام وOCR بعد المعالجة. تبقى الحالة HOLD إذا تعذر أي فحص أو أظهر خطراً؛ وعند النجاح تكون HUMAN_APPROVAL_REQUIRED وليس نشرًا آلياً.

## متطلبات التشغيل
- FFmpeg وFFprobe في PATH.
- Tesseract مع حزم اللغة المطلوبة لفحص النصوص الظاهرة.
- إعداد API لمزود المراجعة السياقية (Gemini أو OpenAI Moderation) وإعداد Whisper/WhisperX وفق ملفات requirements الخاصة بالمشروع.
- يجب التحقق من توافق إصدارات Gradio/Python مع البيئة المحلية.

## حدود معروفة
- OCR يفحص عينات من الإطارات وليس كل إطار؛ النصوص الصغيرة أو المتحركة أو المائلة قد تفلت.
- تحليل الكلام يعتمد على دقة التفريغ واللغة/اللهجة ومزود السياق.
- جدول المعالجة يعتمد توقيتات يراجعها المستخدم؛ لا يتم حذف كلام تلقائياً بناءً على تصنيف آلي.
- يجب إعادة فحص الملف الناتج ومراجعته بصرياً وسمعياً.
- لا توجد أداة تضمن الامتثال أو نتيجة قرار YouTube؛ السياق والقرار النهائي للمنصة.

## الاختبارات
توجد اختبارات تكامل FFmpeg لكتم الصوت والصفارة والقص، سلامة الأصل، تقرير التدقيق، والتحقق من SRT. نجاح الاختبارات المحلية لا يعادل اختبار قبول على جهاز Windows/GPU أو فيديوهات المستخدم الفعلية.


## Hardening update — 2026-11-06
- Editor project validation rejects NaN/Infinity/boolean numeric values, invalid coordinates, rotation/zoom ranges, malformed clip collections and unsupported project versions. JSON saving uses strict finite-number serialization.
- OCR is fail-closed: unavailable OCR, invalid duration, or any failed sampled frame requires human review; sample count is capped at 60.
- Doctor reports Tesseract Arabic (`ara`) and French (`fra`) language packs. Windows setup helper targets Python 3.12 and installs the project/transcription/upload dependency groups.
- Focused regression suite and UI import passed in the build environment. Windows/RTX hardware acceptance and full end-to-end production tests remain outstanding.
