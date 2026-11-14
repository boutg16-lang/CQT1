# OUSSAMA Cutter 8.6.2-ultimate — تبعيات VAD المفقودة

_التاريخ: 2026-11-14 · النطاق: WhisperX + pyannote على Windows_

## 1. العَرَض

التفريغ يبدأ، ثم يسقط عند تحميل VAD:

```
File "...\whisperx\asr.py", line 387, in load_model
  vad_model = load_vad_model(...)
File "...\pyannote\audio\models\segmentation\PyanNet.py", line 34
  from pyannote.audio.models.blocks.sincnet import SincNet
File "...\pyannote\audio\models\blocks\sincnet.py", line 31
  from asteroid_filterbanks import Encoder, ParamSincFB
ModuleNotFoundError: No module named 'asteroid_filterbanks'
```

## 2. السبب

- `whisperx==3.3.1` يعتمد على `pyannote.audio==3.3.2`.
- `pyannote.audio==3.3.2` يعتمد على `asteroid-filterbanks>=0.4` و`einops`
  و`omegaconf` و`soundfile` وغيرها.
- في بيئتك وُجد `pyannote.audio` مثبتاً **بلا تبعياته** (تثبيت سابق ناقص
  أو `--no-deps`)، فمرّ `import whisperx` بينما VAD يحتاج pyannote فعلياً.
- الفحص القديم كان يتحقق من `import whisperx` فقط، لا من مسار VAD.

## 3. الإصلاح

### أ. تبعيات صريحة
`requirements-transcribe.txt` يضم الآن:

```
pyannote.audio==3.3.2
asteroid-filterbanks>=0.4
einops>=0.6.0
omegaconf>=2.1,<3.0
pyannote.core>=5.0.0
pyannote.database>=5.0.1
pyannote.metrics>=3.2
pyannote.pipeline>=3.0.1
pytorch-metric-learning>=2.1.0
rich>=12.0.0
semver>=3.0.0
soundfile>=0.12.1
speechbrain>=1.0.0
tensorboardX>=2.6
torch-audiomentations>=0.11.0
torchmetrics>=0.11.0
```

`install_dependencies.bat` يثبّت أهمها صراحةً، ثم **يتحقق من VAD الحقيقي**:

```python
import asteroid_filterbanks
from pyannote.audio.models.segmentation import PyanNet
```

إن فشل، يطبع الأمر العلاجي ويعتبر التفريغ غير مكتمل — لا يتركك تكتشف
العطل بعد دقائق من المعالجة.

### ب. فحص preflight
`check_pyannote_vad()` يظهر كـ`pyannote VAD`:
- إن لم يكن WhisperX مثبتاً: OK (حزمة اختيارية).
- إن كان مثبتاً وVAD ناقص: WARN مع اسم الحزمة المفقودة والأمر العلاجي.

## 4. الإصلاح الفوري (بدون إعادة تثبيت كامل)

```powershell
cd D:\OS
uv pip install --python ".venv\Scripts\python.exe" `
  "asteroid-filterbanks>=0.4" "einops>=0.6.0" `
  "omegaconf>=2.1,<3.0" "soundfile>=0.12.1"
```

ثم تحقق:

```powershell
.\.venv\Scripts\python.exe -c "import asteroid_filterbanks; from pyannote.audio.models.segmentation import PyanNet; print('VAD OK')"
```

إذا ظهرت وحدة أخرى مفقودة، ثبّت مجموعة pyannote كاملة:

```powershell
uv pip install --python ".venv\Scripts\python.exe" `
  "pyannote.core>=5.0.0" "pyannote.database>=5.0.1" `
  "pyannote.metrics>=3.2" "pyannote.pipeline>=3.0.1" `
  "pytorch-metric-learning>=2.1.0" "rich>=12.0.0" "semver>=3.0.0" `
  "speechbrain>=1.0.0" "tensorboardX>=2.6" `
  "torch-audiomentations>=0.11.0" "torchmetrics>=0.11.0"
```

هذه الحزم لا ترفع Torch 2.8.0 المثبت؛ إن اقترح uv تغييره، أوقف واذكر
الرسالة.

## 5. التحقق

- `tests/test_preflight_metadata.py` — 8 اختبارات، منها:
  - `asteroid_filterbanks` مفقود → WARN مع الأمر.
  - VAD سليم → OK.
  - WhisperX غير مثبت → OK بلا استيراد.
- المجموعة كاملة خضراء، `ruff` نظيف.

## 6. حدود

- هذا لا يحل نماذج pyannote المحمية بـHugging Face token؛ إن طلب النموذج
  توثيقاً فالمشكلة صلاحيات، لا تبعيات.
- `pyannote.audio` ثقيل؛ التثبيت الكامل يحتاج مساحة ووقتاً. البديل
  `faster-whisper` ما زال متاحاً عند تعطّل WhisperX.
