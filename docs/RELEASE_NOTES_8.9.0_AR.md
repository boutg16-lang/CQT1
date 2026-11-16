# OUSSAMA Cutter 8.9.0-ultimate — النماذج على قرص المشروع

_التاريخ: 2026-11-14 · النطاق: كاش Hugging Face وTorch_

## 1. المشكلة

Hugging Face يخزّن النماذج افتراضياً في:

```
C:\Users\PC\.cache\huggingface
```

وTorch في `C:\Users\PC\.cache\torch`. على جهاز يعمل من `D:\OS`، هذا يملأ
قرص النظام بجيجابايتات (large-v3 وحده ~3 GB) بينما D: واسع.

## 2. الحل

### أ. مسار افتراضي على قرص المشروع
`scripts/model_paths.py` يضبط **قبل** استيراد torch/transformers/gradio:

| المتغير | القيمة |
|---|---|
| `HF_HOME` | `<project>\.models\huggingface` |
| `HUGGINGFACE_HUB_CACHE` | `<project>\.models\huggingface\hub` |
| `HF_HUB_CACHE` | نفس المسار |
| `TRANSFORMERS_CACHE` | نفس المسار (توافق قديم) |
| `HF_DATASETS_CACHE` | `<project>\.models\huggingface\datasets` |
| `TORCH_HOME` | `<project>\.models\torch` |

- `<project>` = `D:\OS` عند تشغيل المشروع من هناك.
- المجلدات تُنشأ تلقائياً.
- **المسار الصريح للمستخدم يُحترم**: إذا ضبطت `HF_HOME` إلى مكان
  مخصص، لا نغيّره. نغيّر فقط مسار Hugging Face الافتراضي أو غير المضبوط.
- لتجاوز كل شيء: `VIRALCUTTER_MODELS_DIR=D:\Models`.

### ب. قبل الاستيراد الثقيل
يُستدعى في:
- `main_improved.py`
- `webui/app.py` قبل `import gradio`
- `scripts/transcribe_video.py` قبل `import torch`
- `scripts/transcription_fallback.py`
- `scripts/transcription_diagnostics.py`
- `scripts/preflight.py`

كما تضبطه سكربتات التشغيل `run_webui.bat`, `run.bat`,
`run_webui_on_d.ps1` قبل Python.

### ج. ظهور المسار في preflight
```
✅ Model cache   D:\OS\.models\huggingface (129.5 GB free)
```

### د. نقل الكاش القديم
```powershell
# نسخ فقط (يبقي القديم كاحتياط)
.\.venv\Scripts\python.exe -m scripts.model_paths --migrate

# نسخ ثم حذف القديم بعد النجاح
.\.venv\Scripts\python.exe -m scripts.model_paths --migrate --move
```

- المصدر الافتراضي: `C:\Users\<user>\.cache\huggingface`.
- يحاول نسخ symlinks أولاً؛ إن رفض Windows، ينسخ الملفات.
- `--from "C:\path"` لتحديد مصدر آخر.

أو يدوياً بعد إغلاق التطبيق:

```powershell
robocopy "C:\Users\PC\.cache\huggingface" "D:\OS\.models\huggingface" /E /MOVE
```

## 3. التحقق

```powershell
.\.venv\Scripts\python.exe -m scripts.model_paths --show
```

يجب أن تظهر المسارات تحت `D:\OS\.models`.

## 4. الاختبارات

- `tests/test_model_paths.py` — 7 اختبارات:
  - كل متغيرات الكاش تشير للمشروع.
  - المسار المخصص الصريح لا يُلمس.
  - مسار C: الافتراضي يُنقل.
  - `VIRALCUTTER_MODELS_DIR` يتفوق.
  - المجلدات تُنشأ.
  - النقل copy/move يعملان.
- المجموعة كاملة خضراء، `ruff` نظيف.

## 5. حدود

- الكاش القديم لا يُنقل تلقائياً؛ الأمر أعلاه يفعل ذلك. بدونه سيُعاد
  تنزيل النموذج مرة واحدة في المسار الجديد.
- `VIRALCUTTER_MODELS_DIR` يتفوق على كل المسارات؛ استخدمه لوضع النماذج
  على قرص آخر.
- إذا كان مجلد المشروع للقراءة فقط (تثبيت في Program Files)، يُستخدم
  `%LOCALAPPDATA%\OUSSAMA Cutter\models` كاحتياط.
