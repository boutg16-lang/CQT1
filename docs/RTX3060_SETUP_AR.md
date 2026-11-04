# إعداد OUSSAMA Cutter لبطاقة NVIDIA RTX 3060

## التثبيت التلقائي على Windows

1. ثبّت تعريف NVIDIA الرسمي الحديث وتأكد أن `nvidia-smi` يعمل.
2. فك ضغط المشروع في مجلد ثابت، ويفضل مساراً قصيراً مثل `D:\OUSSAMA-Cutter`.
3. شغّل `setup_rtx3060.bat` بصلاحية المستخدم العادي. لا يحتاج البرنامج إلى صلاحية Administrator إلا إذا طلب مثبت تعريف NVIDIA ذلك.
4. بعد اكتمال التثبيت شغّل `run_rtx3060.bat`.
5. راجع `gpu_profile.json` و`.windows-diagnostics.json` للتأكد من ظهور CUDA واسم البطاقة.

## ماذا يضبط الإعداد؟

- يثبت PyTorch CUDA 12.6 وفق إصدارات المشروع المقفلة، ثم WhisperX.
- يثبت faster-whisper كمسار استرداد عند تعطل WhisperX أو نفاد الذاكرة.
- يكتشف VRAM ويكتب ملف تعريف دون تغيير تعريف البطاقة.
- يختار `large-v3-turbo` للبطاقات ذات 6GB أو أكثر، ويخفض دفعة المعالجة حسب الذاكرة.
- يتحقق من توفر NVENC في FFmpeg؛ إذا لم يكن موجوداً يستخدم `libx264`.
- يفعّل إعدادات تخصيص ذاكرة CUDA وتحميل الوحدات عند بدء التشغيل.

## توصية حسب RTX 3060

- RTX 3060 Desktop 12GB: ابدأ بـ `large-v3-turbo` وCUDA وNVENC.
- RTX 3060 Laptop 6GB: ابدأ بـ `large-v3-turbo` مع معالجة محافظة؛ إذا ظهر CUDA OOM استخدم `medium` أو مسار faster-whisper.
- لا تعتمد على اسم البطاقة وحده؛ ملف `gpu_profile.json` يسجل الذاكرة المكتشفة فعلياً.

## فحص سريع

```powershell
.\.venv\Scripts\python.exe -m scripts.gpu_optimizer --json
.\.venv\Scripts\python.exe -m scripts.windows_diagnostics --root .
.\.venv\Scripts\python.exe -c "import torch; print(torch.cuda.is_available()); print(torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'CUDA unavailable')"
```

## التشغيل الآلي وحدوده

المعالجة المحلية، التفريغ الصوتي، استخراج المقاطع، الفحص والتحضير للتصدير تعمل من داخل التطبيق بحسب الوحدات المثبتة والإعدادات. مراجعة السياسات تصنف المخاطر ولا تضمن قرار YouTube؛ العبارات الملتبسة تحتاج مراجعة بشرية. النشر المباشر إلى YouTube يحتاج إعداد OAuth منفصل وموافقة صريحة على الحساب والقناة.
