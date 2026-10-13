# خطة دمج مشاريع مفتوحة المصدر في OUSSAMA Cutter

## الهدف

الهدف ليس جمع أكبر عدد من المشاريع، بل إزالة نقاط الفشل التي ظهرت في العمل الحقيقي على Windows مع الحفاظ على الاستقرار والأمان ووضوح الترخيص. ستبقى كل الإضافات اختيارية أو خلف بوابات واضحة، ولن يسمح أي backend جديد بتجاوز `content_guard` أو `upload_gate` أو موافقة النشر.

## ترتيب التنفيذ

| المرحلة | الحل | ما يحلّه | المفاضلة | شرط القبول |
|---|---|---|---|---|
| 1 | واجهة backend موحّدة + faster-whisper fallback | يمنع توقف التفريغ الكامل عند تعطل Torch/WhisperX أو تعارض Hugging Face | يحتاج CTranslate2 وcuBLAS/cuDNN على GPU؛ CPU fallback أبطأ | ينجح استخراج SRT/TSV/JSON مع timestamps من fake backend، وتبقى WhisperX أولوية عندما تكون جاهزة |
| 2 | Audio QC محلي عبر FFmpeg | يكشف silence غير المقصود، فرق loudness، clipping، ومسار صوت مفقود قبل polish/النشر | يحتاج معايرة target ولا يثبت قبول YouTube | تقرير JSON قابل للقراءة، لا يمنع المسار إلا عند غياب الصوت أو فشل probe الصريح |
| 3 | OpenTimelineIO كـoptional export | يوسّع Premiere XML الحالي إلى timeline قابل للتبادل ويشير إلى الوسائط بدلاً من تضمينها | اعتماد اختياري وصيغة لا تحمل الفيديو | export صحيح عند تثبيت OTIO، ورسالة واضحة عند عدم تثبيته، مع اختبار مسارات آمنة |
| 4 | Silero VAD لتحسين jump cuts | يقلل أخطاء `silencedetect` في الضوضاء واللهجات | توجد jump cuts حالياً؛ يلزم benchmark عربي قبل اعتماده | لا يغيّر القرار التلقائي قبل مقارنة precision/recall على fixtures عربية |
| 5 | pyannote.audio أو face/voice association | يحسن Active Speaker من heuristic إلى ربط صوتي/زمني | نماذج منفصلة الشروط، اعتماد ثقيل، telemetry اختيارية يجب تعطيلها | benchmark multi-speaker، تقرير backend، fallback تلقائي، وعدم إرسال صوت للخارج |
| 6 | whisper.cpp كخطة إنقاذ محمولة | يعمل خارج بيئة Python/Torch عند انهيارها | binary ونماذج وإدارة Windows إضافية | spike مستقل لا يدخل release قبل اختبار binary/model/signature على Windows |
| 7 | Demucs | فصل الموسيقى والكلام | المستودع الرسمي مؤرشف، والنماذج ثقيلة، والموسيقى موجودة أصلاً | يبقى خارج core إلى أن يثبت فائدة في حالات صوتية حقيقية وترخيص النماذج |
| 8 | دبلجة متعددة اللغات (فكرة من MoneyPrinterTurbo، MIT) | المقطع المترجم يبقى بصوتك الأصلي فلا يُنشر لجمهور آخر | TTS سحابي يرسل النص للخارج؛ سياسة يوتيوب تطلب إفصاحاً للصوت الاصطناعي | opt-in صريح + تنبيه إرسال النص، Edge TTS افتراضي (مجاني بلا مفتاح)، الترجمة من `translate_json` الموجودة، والتوقيت من TTS لا من تفريغ ثانٍ؛ المقطع المدبلج يمر بـ`originality`/`provenance` كتحويل جوهري **وبنفس بوابات النشر** |
| 9 | قناة توزيع بديلة عن GitHub Releases (فكرة من MoneyPrinterTurbo) | #27: المحدّث لا يقرأ إصدارات مستودع خاص، و#25/#26 يمنعان نشر Releases | صيانة إضافية لقناة ثانية | صورة GHCR أو حزمة محمولة + تحديث ذاتي، بلا أسرار وبلا كسر لـ`auto_updater` الموجود |

> **تحديث 29 سبتمبر 2026 (v7.50.0-pro):** نُفِّذت **المرحلة 9** (`scripts/update_manifest.py`
> — قناة تحديث موقَّعة Ed25519 تعمل بلا GitHub Releases، وتُغلق مسار #27) و**المرحلة 8**
> (`scripts/tts_providers.py` + `scripts/dubbing.py` — دبلجة متعددة اللغات: Edge TTS اختياري
> صريح لأنه يرسل النص للخارج، أو ملفات صوت مسجَّلة، مع ملاءمة زمنية بـ`atempo`، وفشل مفتوح
> لكل سطر، ولا تجاوز لبوابات النشر). الأدلة: `docs/UPDATE_CHANNEL_AR.md` و`docs/DUBBING_AR.md`.

## تقييم مرشّح خارج الترتيب (29 سبتمبر 2026)

روجع **MoneyPrinterTurbo** (MIT، ~127k نجمة) — وهو **منتج مختلف الاتجاه** (توليد من
موضوع بمواد stock/AI) لا منافس لجوهر OUSSAMA؛ تقييمه الكامل وسبب أخذ/رفض كل بند في
`docs/MONEYPRINTERTURBO_ASSESSMENT_AR.md`. أُخذت منه أربع أفكار هندسية (أعلاه: مراحل
8 و9 + أسبقية الإعدادات + وثيقة skill للوكلاء) ورُفض جوهره (زراعة محتوى، مادة غير
موثّقة الحقوق، text-to-video).

## القرار التنفيذي

تبدأ النسخة التالية بالمرحلتين 1 و2، لأنهما تعالجان أكثر مشكلتين إيلاماً للمستخدم: توقف التفريغ وجودة المخرج غير المقاسة. تضاف المرحلة 3 في نفس الدفعة إن بقيت optional ولا تغيّر المسار الافتراضي. لن يضاف pyannote أو Demucs أو YOLO إلى الاعتماد الأساسي قبل benchmark وترخيص النماذج.

## ضوابط الأمان

لا تُنقل الفيديوهات أو الصوت إلى خدمة خارجية بسبب هذه الإضافات. تُستبعد النماذج وملفات cache وtokens من ZIP، ويُحفظ كل تقرير داخل المشروع. يفشل fallback مغلقاً إذا لم يستطع استخراج timestamps صالحة، ولا يسمح placeholder بالمرور في إنتاج حقيقي. أي telemetry غير ضرورية تُعطّل افتراضياً، وأي نموذج له ترخيص مستقل يُراجع قبل التوزيع.

## المصادر الرسمية

المعلومات المتعلقة بالمشاريع مأخوذة من مستودعاتها الرسمية: faster-whisper يعلن MIT ويدعم CPU/GPU عبر CTranslate2 مع متطلبات CUDA الحديثة [1]، Silero VAD يعلن MIT ويدعم ONNX Runtime [2]، pyannote.audio يعلن MIT للمكتبة ويوثق telemetry الاختيارية [3]، OpenTimelineIO يعلن Apache-2.0 ويدعم Python 3.9–3.12 [4]، whisper.cpp يعلن MIT ودعم Windows وCUDA/CPU [5]، وDemucs يعلن MIT لكنه مؤرشف للقراءة فقط [6].

## المراجع

[1]: https://github.com/SYSTRAN/faster-whisper "SYSTRAN/faster-whisper"

[2]: https://github.com/snakers4/silero-vad "snakers4/silero-vad"

[3]: https://github.com/pyannote/pyannote-audio "pyannote/pyannote-audio"

[4]: https://github.com/AcademySoftwareFoundation/OpenTimelineIO "AcademySoftwareFoundation/OpenTimelineIO"

[5]: https://github.com/ggml-org/whisper.cpp "ggml-org/whisper.cpp"

[6]: https://github.com/facebookresearch/demucs "facebookresearch/demucs"
