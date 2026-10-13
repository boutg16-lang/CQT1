# فحص القبول على Windows + RTX 3060 — التشغيل الحقيقي

> **لماذا هذا الملف؟** كل ما يمكن اختباره من خادم Linux اختُبر (1613 اختباراً
> خضراء في CI ومحلياً)، لكن **ثلاثة أشياء لا تُثبت إلا على جهازك الحقيقي**:
> أن CUDA تعمل فعلاً على الـ RTX 3060، وأن backend التفريغ يقلع على البطاقة،
> وأن مسار الوسائط كاملاً (FFmpeg → إعادة إطار → QC → مصغّرة) ينتج ملفات صالحة
> على Windows. `scripts/windows_acceptance.py` يقوم بذلك في أمر واحد.

---

## 1) المتطلبات

- المشروع مثبَّت على `D:` كما في `docs/WINDOWS_SETUP_FIX_AR.md`
  (المشروع و`TEMP` وcache الخاصة بـ uv على قرص بمساحة كافية).
- تشغيل من PowerShell داخل مجلد المشروع، بـ Python البيئة الافتراضية نفسها
  التي تُشغّل WebUI.

```powershell
Set-ExecutionPolicy -Scope Process Bypass
cd D:\SS
```

## 2) الفحص الكامل (موصى به)

```powershell
.\.venv\Scripts\python.exe -m scripts.windows_acceptance --gpu-test --json windows_acceptance.json
```

ماذا يحدث:

| الفحص | ماذا يثبت |
|---|---|
| Python / FFmpeg | الإصدار ضمن النطاق المدعوم والأدوات على PATH |
| GPU (torch/CUDA) | `torch.cuda.is_available()=True` واسم البطاقة وحجم VRAM — يُتوقّع `RTX 3060` |
| توليد وسائط اختبار | FFmpeg يبني مقطع 720p بنغمة مسموعة وفجوة صمت 3 ثوانٍ — بلا أي تنزيل |
| فحص الوسائط | `probe_media` و`validate_media_file` يقبلان المقطع |
| إعادة الإطار 9:16 | تحويل حقيقي إلى 1080×1920 مع بقاء الصوت |
| Audio QC | `loudnorm` + `silencedetect` حقيقيان على الملف |
| كشف الصمت | الفجوة المزروعة تُكتشف (أساس jump cuts) |
| الصورة المصغّرة | PNG حقيقية 1080×1920 |
| **تفريغ GPU** (`--gpu-test`) | تحميل نموذج faster-whisper على **CUDA** وتفريغ مقطع حقيقي، مع قياس الزمن |

> ملاحظة `--gpu-test`: يستخدم نموذج `tiny` افتراضياً ويُنزَّل **مرة واحدة** من
> Hugging Face (اتصال إنترنت مطلوب للمرة الأولى فقط). المقطع المولَّد نغمة صافية
> لا كلام — النجاح هو أن الـ backend قلع على البطاقة واشتغل، لا جودة النص.
> لنموذج أقرب للإنتاج: `--gpu-test --model small`.

أكواد الخروج: `0` = كل الفحوص الحرجة ناجحة · `1` = فحص حرج فاشل ·
`2` = تحذيرات فقط (مثلاً لا GPU — CPU fallback يظل صالحاً للعمل).

## 3) فحص إجباري للـ GPU

```powershell
.\.venv\Scripts\python.exe -m scripts.windows_acceptance --gpu-test --require-gpu
```

يفشل الفحص (خروج 1) ما لم تكن CUDA متاحة — مفيد للتأكد من أن التثبيت GPU حقاً
وليس CPU صامتاً. التشخيص المرجعي: `torch.cuda.is_available()` يجب أن يكون
`True` واسم الجهاز `NVIDIA GeForce RTX 3060`.

## 4) فحص مقطع حقيقي من إنتاجك

```powershell
.\.venv\Scripts\python.exe -m scripts.windows_acceptance --clip "D:\videos\my-talk.mp4" --keep
```

يقصّ نافذة 15 ثانية من فيديو حقيقي، يعيد إطارها 9:16، ويقيس Audio QC عليها —
أقرب محاكاة ممكنة لمسار الإنتاج دون بدء مشروع كامل. `--keep` يحفظ مجلد العمل
للفحص اليدوي.

## 5) التشغيل اليومي بعد القبول

بعد نجاح الفحص، المسار الكامل يعمل من الواجهة:

```powershell
.\run_webui.bat
```

أو CLI:

```powershell
.\.venv\Scripts\python.exe main_improved.py --youtube-link "https://..." --num-clips 5
```

## 6) إذا فشل فحص

| العَرَض | السبب المحتمل | الحل |
|---|---|---|
| `torch.cuda.is_available()=False` | torch بلا CUDA مثبَّت | `.\setup_on_d.ps1 -Mode Full -Transcription gpu` ثم شغّل `scripts.windows_diagnostics` |
| `faster-whisper is not installed` | ملف التعريف الاحتياطي غير مثبَّت | `pip install -r requirements-transcribe-fallback.txt` |
| نموذج يفشل بالتحميل على CUDA | ذاكرة VRAM غير كافية أو سائق قديم | جرّب `--model tiny`، حدّث تعريف NVIDIA، أو استخدم CPU كاحتياط |
| `missing from PATH: ffmpeg` | FFmpeg غير مثبَّت | `choco install ffmpeg` أو أعد تشغيل المثبّت |
| فشل `Audio QC` على مقطعك | الملف صامت/مشوَّش فعلاً | هذا صحّة الفحص: راجع `issues` في التقرير |

## 7) ماذا يعني نجاح الفحص

- بيئة الإنتاج على جهازك تطابق ما يتوقعه الكود (نفس وحدات `scripts/` التي
  يستعملها المسار الحقيقي — لا محاكاة).
- تقرير `windows_acceptance.json` قابل للإرسال عند أي دعم، ولا يحتوي أي أسرار
  ولا روابط شخصية — فقط نتائج الفحوص وأزمنتها.

> هذا الفحص لا يُنزّل فيديو من YouTube ولا يفتح OAuth ولا يرفع أي شيء.
> هو اختبار قبول محلي صِرف.
