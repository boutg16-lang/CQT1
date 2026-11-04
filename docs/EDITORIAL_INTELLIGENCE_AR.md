# محرك الذكاء التحريري العربي

## الهدف
يجمع `scripts/editorial_intelligence.py` بين تحليل مؤشرات الحوار العربي/المغاربي وتجميع حزمة مقاطع متنوعة. هذه طبقة مساعدة قابلة للتفسير، وليست نموذجاً يتنبأ بالانتشار أو يحكم على صحة المحتوى.

## التشغيل
من جذر المشروع:

```powershell
.\.venv\Scripts\python.exe -m scripts.editorial_intelligence input_segments.json --output editorial_package.json --limit 5 --max-per-topic 2
```

يدعم الإدخال قائمة JSON مباشرة أو كائناً يحتوي على `segments`. يعتمد النص على `transcript_text` ثم `text`. وتُستخدم الحقول الاختيارية `selection_score` أو `score`، و`topic` أو `theme`، و`source_id` أو `source`، و`start` و`end`.

## ما الذي ينتجه؟
- `dialogue_intelligence`: مؤشرات السؤال والاعتراض والرد وكشف المعلومة والخلاصة والدعوة للتفاعل، مع الأدلة النصية.
- `dialogue_cue_bonus`: مكافأة محدودة وشفافة (حدها الأقصى 7 نقاط) للمؤشرات الحوارية المحددة.
- `editorial_review_score`: درجة ترتيب مساعدة مبنية على الدرجة السابقة والمكافأة المحدودة.
- `editorial_review_flags`: تنبيهات للمراجعة البشرية، ومنها قصر التفريغ وعدم وضوح إشارة النهاية.
- `selected` و`excluded`: المقاطع المختارة وأسباب استبعاد غير المختار وفق حدود التنوع والتكرار.

## حدود مهمة
مطابقة العبارات تقريبية وتغطية اللهجات غير مكتملة. اكتمال النهاية مؤشر لغوي لا يثبت اكتمال المعنى. لا يستنتج المحرك المصداقية أو نية المتحدث أو الانتشار أو مخالفة سياسات المنصة. يجب مراجعة الصوت والصورة والسياق قبل النشر.

## الاختبارات
```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_editorial_intelligence.py tests/test_arabic_dialogue_intelligence.py tests/test_clip_package.py -q
```
