# تحليل فجوات OUSSAMA Cutter — 22 سبتمبر 2026

> **تحديث 29 سبتمبر 2026 (v7.47.0-pro):** بند «corpus عربي مرخّص للمعايرة»
> أُغلق أدواتياً: `scripts/audio_qc_corpus.py` يبني corpus بإثبات ترخيص
> (تسجيلات خاصة / Common Voice CC0 / ترخيص مخصص) ويطبع الملفات إلى صيغة معيارية،
> و`calibrate` ينتج العتبات عبر `audio_qc_calibrate` — فشلاً مغلقاً بلا ترخيص.
> يتبقى فقط اختيار المستخدم لمصدره (تسجيلاته أو تنزيل CV بنفسه) وتشغيل الأمرين —
> انظر `docs/AUDIO_QC_CORPUS_AR.md`.

> **تحديث 29 سبتمبر 2026 (v7.46.0-pro):** بند «اختبار Windows + NVIDIA الواقعي»
> أصبح له أداة تنفيذية جاهزة ومختبَرة: `scripts/windows_acceptance.py` يثبت CUDA
> والتفريغ ومسار الوسائط كاملاً بأمر واحد على الجهاز الحقيقي
> (`docs/WINDOWS_RTX3060_ACCEPTANCE_AR.md`). يبقى منه فقط تشغيله على جهاز المستخدم —
> وهو بطبيعته خطوة محلية لا يمكن تنفيذها من خادم.

> **تحديث 29 سبتمبر 2026 (v7.45.0-pro):** جولة مراجعة مستقلة عدائية على الطبقات
> الأربع الحرجة أثبتت 24 خللاً حقيقياً بإعادة الإنتاج — أخطرها ست فتحات fail-open
> في بوابة الرفع (الأمان الدلالي، خريطة الكتم القديمة، تعطُّل content_guard، حكم
> review السياقي، OCR بلا دليل، تجاوز `YT_PRIVACY`)، إضافة إلى جدولة مستحيلة
> واقعياً، رفع دفعي مكرر، وإفساد حالة/إعدادات. أُغلقت كلها في v7.45.0-pro مع
> +98 اختبار انحدار. التفاصيل: `docs/RELEASE_NOTES_7.45.0_AR.md`. يبقى من هذا
> التحليل: اختبار Windows + NVIDIA الواقعي، وcorpus عربي مرخّص للمعايرة،
> وصلاحية Workflows لتطبيق `docs/ISSUE_25_workflow_fix.patch` (الذي يصلح #25 و#26)،
> وحصة تخزين artifacts ممتلئة على مستوى الحساب (#26) تمنع حالياً إشارة CI ونشر الإصدارات.

> **تحديث 28 سبتمبر 2026:** تقدّم بندا «عتبات المعايرة» و«توقيع artifacts» —
> أُضيف `scripts/audio_qc_calibrate.py` (يقيس corpus مقاطع مرجعية معتمدة ويستخرج
> عتبات LUFS/true-peak/صمت مبنية على المئينات في `audio_qc_thresholds.json`،
> وتقرأه `python -m scripts.audio_qc --thresholds-file` مع أعلام `--target-*`
> للتجاوز اليدوي)، وأُضيف `scripts/sign_artifacts.py` (توقيع Ed25519 دون اتصال:
> `init`/`sign`/`verify` ينتج `SHA256SUMS` + توقيعًا منفصلًا — نفس صيغة manifest
> التي يتحقق منها `auto_updater`). يبقى: corpus عربي مرخّص فعلي لتشغيل المعايرة
> عليه، ربط التوقيع ببناء CI (ينتظر صلاحية Workflows، انظر Issue #25)، واختبار
> Windows + NVIDIA الواقعي. كذلك أُصلح مسار بناء Windows EXE في CI بـshim مؤقت
> في `packaging/viralcutter.spec` وصدر Release ‏v7.42.0 بنجاح.
>
> **تحديث 27 سبتمبر 2026:** الفجوتان الأعلى أولوية أُغلقتا بعد كتابة هذا
> التحليل — fallback التفريغ المستقل موجود الآن (`scripts/transcription_fallback.py`
> + `requirements-transcribe-fallback.txt`، faster-whisper اختياري لا يستورد
> Torch)، وربط الصوت بالوجه موجود منذ v7.35 (`scripts/voice_face_link.py`،
> opt-in من الواجهة منذ v7.36). كذلك صدرت حزمة الأمان v6 وأُضيفت أدوات الجودة
> (pre-commit، CODEOWNERS، Makefile، .env.example). يبقى من هذا التحليل:
> اختبار Windows + NVIDIA الواقعي، وcorpus عربي مرخّص للمعايرة، وتوقيع artifacts.

> **حالة البنود 3–7:** أضيف تصدير OpenTimelineIO اختياري، وشُدّد mux الصوت
> باستخدام CFR و`aresample`، وأضيف benchmark فيديو/صوت ثابت، كما أصبحت CI تبني
> الحزمة وتنتج SBOM وتفحص تراخيص الاعتمادات. يبقى اختبار Windows + NVIDIA
> ومجموعة فيديوهات عربية حقيقية خارج CI.

## الخلاصة

المشروع الحالي قوي في pipeline الأساسي: اختيار المقاطع، منع تكرار النوافذ، حارس السلامة، القص، Professional Polish، الموسيقى وauto-duck، B-roll الاختياري، الصور المصغرة، كشف المشاهد، رفع YouTube الآمن، الطابور الدائم، والتقارير. لذلك لا ينبغي دمج مشاريع جديدة تكرر هذه الأجزاء قبل تحسين نقاط الاختناق التي ظهرت في التشغيل الواقعي على Windows.

| الفجوة | الدليل من المصدر الحالي | الأولوية | المسار المقترح |
|---|---|---:|---|
| توقف التفريغ عند تعطل WhisperX أو Torch | `transcribe_video.py` يوقف المسار إذا كان `whisperx` أو `torch` غير قابل للاستيراد، والبديل الحالي placeholder للاختبار فقط | عالية جداً | fallback اختياري مستقل مثل faster-whisper، ثم whisper.cpp كخطة إنقاذ محمولة |
| تتبع المتحدث | `ActiveSpeakerSelector` يختار الوجه من `activity_score` وMAR مع hysteresis، لكنه لا يربط الصوت بالوجه ولا يستخدم diarization | عالية | backend اختياري لـpyannote أو face/voice association، مع fallback الحالي وتقرير صريح |
| كشف الصمت | `jump_cuts.py` يستعمل FFmpeg `silencedetect` وtranscript filler logic؛ الميزة موجودة وليست ناقصة | متوسطة | معايرة VAD اختيارية مثل Silero فقط إذا أثبتت benchmark العربية تحسناً |
| جودة الصوت | Audio QC يقيس LUFS/true peak والصمت، مع benchmark ثابت في CI | متوسطة | إضافة corpus عربي مرخّص وعتبات معايرة |
| تصدير timeline | Premiere XML + OTIO اختياري | منخفضة | توسيع محولات OTIO عند الحاجة |
| B-roll والموسيقى والبصمة | `broll_engine.py` و`background_music.py` و`music_fingerprint.py` موجودة | منخفضة حالياً | لا ندمج مشروعاً مكرراً؛ نركز على QC وحقوق الاستخدام |
| الاختبار الواقعي | benchmark صغير في CI؛ لا يستبدل اختبار Windows RTX 3060 أو خدمات حقيقية | عالية | إضافة fixtures عربية مرخّصة واختبار قبول Windows يدوي موثق، دون أسرار |
| التوزيع والاعتمادات | WhisperX/Torch اختيارية؛ CI تنتج wheel وsdist وSBOM وتقرير تراخيص | متوسطة | توقيع artifacts وضم التقارير إلى GitHub Release |

## قرار مبدئي

أفضل قيمة مباشرة هي فصل طبقة التفريغ عن WhisperX بإضافة fallback اختياري، ثم إضافة audio QC، ثم OTIO. لا يُنصح حالياً بجعل pyannote أو Demucs أو YOLO اعتماداً أساسياً؛ الأولى تحتاج نماذج وسياسة telemetry/ترخيص لكل نموذج، والثانية أرشيفية وثقيلة، والثالثة قد تفرض تعقيدات وترخيصاً غير مناسباً. أي backend جديد يجب أن يكون opt-in، قابلاً للإزالة، ولا يسمح بتخطي safety gate أو upload gate.

## ما لم يتم فعله بعد

لم يتم تثبيت أو نسخ أي مشروع خارجي بناءً على هذا التحليل، ولم يتم تنزيل نماذج أو تعديل مسار الإنتاج. ستُراجع صفحات المشاريع الرسمية والتراخيص والإصدارات قبل تنفيذ أي دمج، ثم ستضاف اختبارات deterministic وfixtures صغيرة قبل رفع commit جديد.
