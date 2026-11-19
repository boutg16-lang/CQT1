"""تشخيص وإصلاح مكوّنات التفريغ في OUSSAMA Cutter.

هذه الوحدة خفيفة ولا تستورد Torch أو WhisperX مباشرة أثناء الإقلاع. تفحصهما
بشكل آمن، وتوفر إصلاحاً صريحاً يختاره المستخدم بدلاً من تنزيل حزم ثقيلة
تلقائياً داخل WebUI.
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from importlib import metadata
from typing import Any, Dict, Optional

try:
    from scripts.model_paths import configure_model_cache as _configure_model_cache
except Exception:
    _configure_model_cache = None
if _configure_model_cache is not None:
    _configure_model_cache()


def prepare_ctranslate2_windows_import() -> None:
    """Load WhisperX's Windows backend without requiring removed pkg_resources."""
    if sys.platform != "win32":
        return
    try:
        importlib.import_module("pkg_resources")
        return
    except ModuleNotFoundError as exc:
        if exc.name != "pkg_resources":
            raise
    from importlib.resources import files
    from types import ModuleType

    missing = object()
    previous = sys.modules.get("pkg_resources", missing)
    compat = ModuleType("pkg_resources")

    def resource_filename(package: Any, resource: str) -> str:
        package_name = getattr(package, "module_name", package)
        return os.fspath(files(package_name).joinpath(resource))

    compat.resource_filename = resource_filename
    sys.modules["pkg_resources"] = compat
    try:
        importlib.import_module("ctranslate2")
    finally:
        if previous is missing:
            sys.modules.pop("pkg_resources", None)
        else:
            sys.modules["pkg_resources"] = previous


class TranscriptionUnavailableError(ImportError):
    """خطأ قابل للتمييز حتى لا يعيد التطبيق تشغيل العملية بلا فائدة."""

    dependency_error = True


APP_NAME = "OUSSAMA Cutter"
APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIN_FREE_MB = 4096
SUPPORTED_TRANSCRIPTION_VERSIONS = {
    "ctranslate2": ("4.4.0", "4.5.0"),
    "whisperx": ("3.3.1", "3.3.2"),
    "transformers": ("5.14.1", "5.16.0"),
    "huggingface_hub": ("1.16.0", "2.0.0"),
    "tokenizers": ("0.22.2", "0.23.1"),
    "setuptools": ("84.1.0", "85.0.0"),
}


def _version_supported(installed: str, minimum: str, maximum: str) -> bool:
    from packaging.version import InvalidVersion, Version

    try:
        current = Version(installed)
        return Version(minimum) <= current < Version(maximum)
    except InvalidVersion:
        return False


def _probe(module_name: str) -> Dict[str, Any]:
    try:
        if module_name in {"ctranslate2", "whisperx", "faster_whisper"}:
            prepare_ctranslate2_windows_import()
        module = importlib.import_module(module_name)
        version = getattr(module, "__version__", None)
        if version is None:
            try:
                version = metadata.version(module_name.replace("_", "-"))
            except metadata.PackageNotFoundError:
                version = "installed"
        return {
            "module": module_name,
            "ok": True,
            "version": str(version),
            "error": "",
        }
    except Exception as exc:  # optional stack: report, never crash diagnostics
        return {
            "module": module_name,
            "ok": False,
            "version": "",
            "error": str(exc),
        }


def _torch_runtime() -> Dict[str, Any]:
    """Probe the actual Torch backend without failing diagnostics."""
    try:
        torch = importlib.import_module("torch")
        cuda_version = str(getattr(getattr(torch, "version", None), "cuda", "") or "")
        cuda_available = bool(torch.cuda.is_available())
        device_name = ""
        if cuda_available:
            try:
                device_name = str(torch.cuda.get_device_name(0))
            except Exception:
                device_name = "NVIDIA GPU"
        return {
            "torch_version": str(getattr(torch, "__version__", "installed")),
            "cuda_version": cuda_version,
            "cuda_available": cuda_available,
            "device_name": device_name,
            "error": "",
        }
    except Exception as exc:
        return {
            "torch_version": "",
            "cuda_version": "",
            "cuda_available": False,
            "device_name": "",
            "error": str(exc),
        }


def diagnose(base_dir: Optional[str] = None) -> Dict[str, Any]:
    """Return a JSON-serialisable health report for the transcription stack."""
    base_dir = os.path.abspath(base_dir or os.getcwd())
    try:
        free_mb = shutil.disk_usage(base_dir).free // (1024 * 1024)
    except Exception:
        free_mb = None
    probes = {
        name: _probe(name)
        for name in (
            "torch",
            "torchaudio",
            "ctranslate2",
            "whisperx",
            "transformers",
            "huggingface_hub",
            "tokenizers",
            "setuptools",
            "faster_whisper",
            "pyannote.audio",
            "asteroid_filterbanks",
        )
    }
    incompatible = [
        name
        for name, (minimum, maximum) in SUPPORTED_TRANSCRIPTION_VERSIONS.items()
        if probes[name]["ok"]
        and not _version_supported(probes[name]["version"], minimum, maximum)
    ]
    primary_ready = bool(
        all(
            probes[name]["ok"]
            for name in ("torch", "torchaudio", *SUPPORTED_TRANSCRIPTION_VERSIONS)
        )
        and not incompatible
    )
    fallback_ready = bool(probes["faster_whisper"]["ok"])
    missing = [name for name, result in probes.items() if not result["ok"]]
    torch_runtime = _torch_runtime()
    return {
        "app": APP_NAME,
        "python": sys.version.split()[0],
        "python_executable": sys.executable,
        "platform": sys.platform,
        "base_dir": base_dir,
        "free_disk_mb": free_mb,
        "disk_ok_for_repair": free_mb is None or free_mb >= MIN_FREE_MB,
        "ready": bool(primary_ready or fallback_ready),
        "primary_ready": primary_ready,
        "fallback_ready": fallback_ready,
        "backend": "whisperx"
        if primary_ready
        else ("faster-whisper" if fallback_ready else "none"),
        "gpu_ready": bool(
            (primary_ready or fallback_ready)
            and torch_runtime["cuda_available"]
            and torch_runtime["cuda_version"]
        ),
        "missing": missing,
        "incompatible": incompatible,
        "packages": probes,
        "torch_runtime": torch_runtime,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


def probe_vad(device: str = "cpu", import_module=None) -> Dict[str, Any]:
    """Actually load WhisperX's bundled VAD model.

    `import whisperx` can pass while the VAD stack is broken (pyannote,
    asteroid_filterbanks, tokenizers). This probe catches the failure before
    the user waits for a model download / transcription.
    """
    import_module = import_module or importlib.import_module
    try:
        prepare_ctranslate2_windows_import()
        torch = import_module("torch")
        whisperx = import_module("whisperx")
        load_vad_model = getattr(whisperx, "load_vad_model", None)
        if load_vad_model is None:
            load_vad_model = import_module("whisperx.vad").load_vad_model
        load_vad_model(torch.device(device))
        return {"ok": True, "error": "", "device": device}
    except Exception as exc:
        return {
            "ok": False,
            "error": "{}: {}".format(type(exc).__name__, exc),
            "device": device,
        }


READY_CACHE_NAME = ".transcription_ready.json"


def _ready_cache_path() -> str:
    return os.path.join(APP_ROOT, READY_CACHE_NAME)


def _stack_fingerprint(report: Dict[str, Any]) -> str:
    """Versions that invalidate a previous successful VAD probe."""
    parts = [sys.executable]
    for name in (
        "torch",
        "whisperx",
        "tokenizers",
        "transformers",
        "huggingface_hub",
        "pyannote.audio",
        "asteroid_filterbanks",
        "ctranslate2",
    ):
        item = (report.get("packages") or {}).get(name) or {}
        parts.append("{}={}".format(name, item.get("version") or "?"))
    return "|".join(parts)


def _read_ready_cache() -> Dict[str, Any]:
    try:
        with open(_ready_cache_path(), "r", encoding="utf-8") as handle:
            value = json.load(handle)
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _write_ready_cache(fingerprint: str) -> None:
    try:
        path = _ready_cache_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "fingerprint": fingerprint,
                    "checked_at": datetime.now(timezone.utc).isoformat(),
                },
                handle,
                ensure_ascii=False,
            )
    except OSError:
        pass


def _publish_health(backend: str, force_cpu: bool, reason: str) -> None:
    try:
        from scripts import transcription_health

        transcription_health.write_profile(
            backend=backend,
            force_cpu=force_cpu,
            chunk_seconds=transcription_health.recommended_chunk_seconds(),
            reason=reason,
        )
    except Exception:
        pass


def ensure_ready(
    auto_repair: bool = True, base_dir: Optional[str] = None, force: bool = False
) -> Dict[str, Any]:
    """Probe the real VAD; repair the missing piece once; cache success.

    Returns a JSON-serialisable result. It never raises: a failed repair is a
    reported state, and the pipeline can still use faster-whisper.
    """
    report = diagnose(base_dir=base_dir)
    packages = report.get("packages") or {}
    if not packages.get("whisperx", {}).get("ok"):
        _publish_health("faster-whisper", False, "whisperx is not installed")
        return {
            "ok": bool(report.get("fallback_ready")),
            "skipped": True,
            "reason": "whisperx is not installed; nothing to repair",
            "diagnostic": report,
            "steps": [],
        }
    fingerprint = _stack_fingerprint(report)
    if not force:
        cache = _read_ready_cache()
        if cache.get("fingerprint") == fingerprint:
            _publish_health(
                "whisperx", False, "VAD already verified for this exact stack"
            )
            return {
                "ok": True,
                "cached": True,
                "reason": "VAD already verified for this exact stack",
                "diagnostic": report,
                "steps": [],
            }

    vad = probe_vad()
    if vad.get("ok"):
        _write_ready_cache(fingerprint)
        _publish_health("whisperx", False, "VAD probe succeeded")
        return {
            "ok": True,
            "cached": False,
            "vad": vad,
            "diagnostic": report,
            "steps": [],
        }

    steps = []
    if auto_repair:
        error = str(vad.get("error") or "").lower()
        targeted = None
        if "asteroid_filterbanks" in error or "pyannote" in error:
            targeted = [
                "asteroid-filterbanks>=0.4",
                "einops>=0.6.0",
                "omegaconf>=2.1,<3.0",
                "soundfile>=0.12.1",
                "pyannote.core>=5.0.0",
                "pyannote.database>=5.0.1",
                "pyannote.metrics>=3.2",
                "pyannote.pipeline>=3.0.1",
            ]
        elif "tokenizers" in error:
            targeted = ["tokenizers==0.22.2"]
            steps.append(
                _run_install(
                    _pip_command(
                        ["--force-reinstall", "--no-deps", "tokenizers==0.22.2"]
                    )
                )
            )
        if targeted and not steps:
            print(
                "[doctor] repairing VAD dependencies: {}".format(
                    ", ".join(targeted[:4]) + (" ..." if len(targeted) > 4 else "")
                ),
                flush=True,
            )
            steps.append(_run_install(_pip_command(["--upgrade", *targeted])))
        if steps:
            vad = probe_vad()
        if not vad.get("ok"):
            mode = (
                "gpu"
                if report.get("torch_runtime", {}).get("cuda_available")
                else "cpu"
            )
            print(
                "[doctor] targeted repair did not fix VAD; repairing the "
                "full transcription stack ({})...".format(mode),
                flush=True,
            )
            repair_result = repair(mode, base_dir=base_dir)
            steps.append(repair_result)
            vad = probe_vad()
            if vad.get("ok"):
                report = diagnose(base_dir=base_dir)
                fingerprint = _stack_fingerprint(report)
    if vad.get("ok"):
        _write_ready_cache(fingerprint)
        _publish_health("whisperx", False, "VAD repaired successfully")
    else:
        _publish_health(
            "faster-whisper", True, str(vad.get("error") or "VAD probe failed")
        )
    return {
        "ok": bool(vad.get("ok")),
        "cached": False,
        "vad": vad,
        "auto_repaired": bool(steps),
        "steps": steps,
        "diagnostic": report if vad.get("ok") else diagnose(base_dir=base_dir),
    }


def build_error_message(
    whisperx_error: str = "",
    torch_error: str = "",
    base_dir: Optional[str] = None,
) -> str:
    """Create a concise Arabic recovery message for the pipeline and CLI."""
    report = diagnose(base_dir=base_dir)
    lines = ["التفريغ الصوتي غير متاح حالياً في OUSSAMA Cutter."]
    if report["missing"]:
        lines.append(
            "المكوّنات الناقصة أو التي فشل استيرادها: {}.".format(
                ", ".join(report["missing"])
            )
        )
    if report.get("incompatible"):
        lines.append(
            "إصدارات خارج النطاق المدعوم: {}.".format(", ".join(report["incompatible"]))
        )
    if torch_error:
        lines.append("تفاصيل Torch: {}".format(torch_error))
    if whisperx_error:
        lines.append("تفاصيل WhisperX: {}".format(whisperx_error))
    combined_error = "{} {}".format(whisperx_error, torch_error).lower()
    if "unable to allocate" in combined_error or "out of memory" in combined_error:
        lines.append(
            "الذاكرة غير كافية للتفريغ: CQT1 يقسم الصوت الطويل إلى نوافذ تلقائياً. "
            "أغلق البرامج الثقيلة أو اختر موديل small/medium؛ لا تحتاج إعادة تثبيت الحزم."
        )
    legacy_pkg_resources_error = "pkg_resources" in combined_error
    ctranslate2_issue = (
        "ctranslate2" in report["missing"]
        or "ctranslate2" in report["incompatible"]
        or (legacy_pkg_resources_error and sys.platform == "win32")
    )
    if ctranslate2_issue:
        if legacy_pkg_resources_error and sys.platform == "win32":
            lines.append(
                "على Windows، CTranslate2 4.4.0 يستعمل pkg_resources القديم لتحميل DLL؛ توافق المشروع يعالجه عبر importlib.resources من دون تخفيض setuptools أو تغيير Torch CUDA."
            )
        lines.append(
            "أبقِ CTranslate2 ضمن >=4.4.0,<4.5.0 لأن WhisperX 3.3.1 يفرض هذا النطاق."
        )
    setuptools_issue = (
        "setuptools" in report["missing"] or "setuptools" in report["incompatible"]
    )
    if legacy_pkg_resources_error:
        repair_mode = (
            "gpu" if report.get("torch_runtime", {}).get("cuda_available") else "cpu"
        )
        lines.extend(
            [
                "خطأ pkg_resources يشير إلى تبعية قديمة غير متوافقة؛ لا تخفّض setuptools إلى 81.x لأن هذا الخط يعاني من ثغرات معروفة.",
                "أعد تثبيت المجموعة المدعومة من requirements-transcribe.txt مع الحفاظ على Torch CUDA إذا كان يعمل:",
                "    .\\.venv\\Scripts\\python.exe -m scripts.transcription_diagnostics --repair {}".format(
                    repair_mode
                ),
            ]
        )
    elif setuptools_issue:
        repair_mode = (
            "gpu" if report.get("torch_runtime", {}).get("cuda_available") else "cpu"
        )
        lines.extend(
            [
                "سبب الفشل: إصدار setuptools خارج النطاق الآمن المدعوم للمشروع.",
                "المجموعة تتطلب setuptools>=84.1.0,<85 ولا تعتمد على pkg_resources. أصلح التفريغ مع الحفاظ على Torch CUDA إذا كان يعمل:",
                "    .\\.venv\\Scripts\\python.exe -m scripts.transcription_diagnostics --repair {}".format(
                    repair_mode
                ),
            ]
        )
    hub_version = str(
        (report.get("packages") or {}).get("huggingface_hub", {}).get("version") or ""
    )
    hub_supported = bool(hub_version) and _version_supported(
        hub_version, "1.16.0", "2.0.0"
    )
    if (
        "huggingface-hub" in combined_error or "huggingface_hub" in combined_error
    ) and not hub_supported:
        lines.extend(
            [
                "إصدار Hugging Face Hub خارج النطاق المدعوم ({}؛ المطلوب 1.16.0–2.0).".format(
                    hub_version or "غير معروف"
                ),
                "مجموعة whisperx==3.3.1 تعمل مع Transformers 5.14–5.15 وHub 1.16+.",
                "لا تبدأ الاختبار باستيراد is_offline_mode يدوياً؛ اختبر WhisperX مباشرة.",
                "لا تغيّر إصدار Torch CUDA إذا كان يعمل.",
                "نفّذ: uv pip install --python ./.venv/Scripts/python.exe -r requirements-transcribe.txt",
            ]
        )
    tokenizers_version = str(
        (report.get("packages") or {}).get("tokenizers", {}).get("version") or ""
    )
    tokenizers_supported = bool(tokenizers_version) and _version_supported(
        tokenizers_version, "0.22.2", "0.23.1"
    )
    if "tokenizers" in combined_error and not tokenizers_supported:
        lines.extend(
            [
                "تعارض إصدارات tokenizers: المثبت {}، والمطلوب 0.22.2 (0.23.0 لم يصدر أصلاً).".format(
                    tokenizers_version or "غير معروف"
                ),
                'نفّذ: uv pip install --python ./.venv/Scripts/python.exe --upgrade "tokenizers>=0.22.0,<0.23.1"',
            ]
        )
    elif "tokenizers" in combined_error:
        lines.extend(
            [
                "فشل tokenizers أثناء التشغيل رغم أن الإصدار {} داخل النطاق المدعوم — الخطأ ليس تعارض إصدارات.".format(
                    tokenizers_version or "غير معروف"
                ),
                'أعد تثبيت الامتداد الأصلي: uv pip install --python ./.venv/Scripts/python.exe --force-reinstall "tokenizers==0.22.2"',
                "إن استمر، أرسل آخر سطر من التتبّع؛ قد يكون ملف tokenizer النموذج تالفاً.",
            ]
        )
    if report.get("fallback_ready"):
        lines.append(
            "يتوفر faster-whisper كمسار احتياطي مستقل؛ شغّل OUSSAMA مجدداً بعد تثبيته لاستخدامه تلقائياً."
        )
    else:
        lines.extend(
            [
                "لتجنب توقف التفريغ عند تعطل WhisperX، ثبّت المسار الاختياري faster-whisper من requirements-transcribe-fallback.txt.",
                "    .\\.venv\\Scripts\\python.exe -m scripts.transcription_diagnostics --repair-fallback",
            ]
        )
    lines.extend(
        [
            "هذا لا يعني أن المشروع تالف؛ وضع المونتاج والأمان يعملان بدون حزمة التفريغ.",
            "لإصلاح المسار الكامل على Windows من مجلد المشروع:",
            "    .\\setup_on_d.ps1 -Mode Full -Transcription cpu",
            "أو مباشرة:",
            "    .\\.venv\\Scripts\\python.exe -m scripts.transcription_diagnostics --repair cpu",
            "للجهاز الذي يملك NVIDIA استخدم: --repair gpu أو -Transcription gpu.",
            "بعد التثبيت أعد تشغيل OUSSAMA Cutter. لا تستخدم الترجمة الوهمية في إنتاج حقيقي.",
            "تم إيقاف العملية بأمان بدلاً من إعادة المحاولة تلقائياً بلا فائدة.",
        ]
    )
    return "\n".join(lines)


def write_report(
    project_folder: Optional[str],
    error: Optional[BaseException] = None,
) -> Optional[str]:
    """Persist a local diagnostic report beside the project when possible."""
    target_dir = os.path.abspath(project_folder or os.getcwd())
    try:
        os.makedirs(target_dir, exist_ok=True)
        report = diagnose(base_dir=target_dir)
        if error is not None:
            report["pipeline_error"] = str(error)
        path = os.path.join(target_dir, "transcription_diagnostic.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2)
        return path
    except Exception:
        return None


def _pip_command(args: list[str]) -> list[str]:
    """Build a pip install command, falling back to uv for pip-less venvs."""
    if importlib.util.find_spec("pip") is not None:
        return [sys.executable, "-m", "pip", "install"] + args
    uv = shutil.which("uv")
    if uv:
        return [uv, "pip", "install", "--python", sys.executable] + args
    return [sys.executable, "-m", "pip", "install"] + args


def _run_install(command: list[str], timeout: int = 1800) -> Dict[str, Any]:
    try:
        proc = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    except Exception as exc:
        return {"ok": False, "command": command, "error": str(exc), "output": ""}
    output = (proc.stdout or "") + "\n" + (proc.stderr or "")
    return {
        "ok": proc.returncode == 0,
        "command": command,
        "returncode": proc.returncode,
        "error": "" if proc.returncode == 0 else output[-3000:],
        "output": output[-3000:],
    }


def repair_fallback(base_dir: Optional[str] = None) -> Dict[str, Any]:
    """Install only the independent faster-whisper recovery backend."""
    requirement_file = os.path.join(APP_ROOT, "requirements-transcribe-fallback.txt")
    if not os.path.exists(requirement_file):
        return {
            "ok": False,
            "mode": "fallback",
            "steps": [],
            "error": "requirements-transcribe-fallback.txt is missing",
        }
    steps = [_run_install(_pip_command(["--upgrade", "-r", requirement_file]))]
    final = diagnose(base_dir=base_dir)
    ok = bool(final.get("fallback_ready"))
    return {
        "ok": ok,
        "mode": "fallback",
        "diagnostic": final,
        "steps": steps,
        "error": "" if ok else "faster-whisper was not importable after installation",
    }


def repair(mode: str = "cpu", base_dir: Optional[str] = None) -> Dict[str, Any]:
    """Install the transcription stack explicitly in CPU or NVIDIA CUDA mode."""
    mode = str(mode or "cpu").lower().strip()
    if mode not in {"cpu", "gpu"}:
        raise ValueError("mode must be cpu or gpu")
    report = diagnose(base_dir=base_dir)
    if report["free_disk_mb"] is not None and report["free_disk_mb"] < MIN_FREE_MB:
        return {
            "ok": False,
            "mode": mode,
            "error": "مساحة القرص غير كافية؛ يلزم 4GB على الأقل لإصلاح مكوّنات التفريغ.",
            "diagnostic": report,
            "steps": [],
        }
    requirement_file = os.path.join(APP_ROOT, "requirements-transcribe.txt")
    if not os.path.isfile(requirement_file):
        return {
            "ok": False,
            "mode": mode,
            "error": "requirements-transcribe.txt is missing",
            "diagnostic": report,
            "steps": [],
        }
    index_url = (
        "https://download.pytorch.org/whl/cu126"
        if mode == "gpu"
        else "https://download.pytorch.org/whl/cpu"
    )
    steps = []
    torch_ready = bool(
        report.get("packages", {}).get("torch", {}).get("ok")
        and report.get("packages", {}).get("torchaudio", {}).get("ok")
    )
    cuda_ready = bool(report.get("torch_runtime", {}).get("cuda_available"))
    if not torch_ready or (mode == "gpu" and not cuda_ready):
        steps.append(
            _run_install(
                _pip_command(
                    [
                        "--upgrade",
                        "torch==2.8.0",
                        "torchvision==0.23.0",
                        "torchaudio==2.8.0",
                        "--index-url",
                        index_url,
                    ]
                )
            )
        )
    else:
        steps.append(
            {
                "ok": True,
                "command": [],
                "returncode": 0,
                "error": "",
                "output": "Existing Torch installation preserved.",
            }
        )
    steps.append(_run_install(_pip_command(["-r", requirement_file])))
    final = diagnose(base_dir=base_dir)
    primary_ready = bool(final.get("primary_ready"))
    gpu_ready = bool(final.get("gpu_ready"))
    ok = primary_ready and (mode != "gpu" or gpu_ready)
    if ok:
        error = ""
    elif primary_ready and mode == "gpu":
        error = "Torch CUDA أو تعريف NVIDIA غير جاهز؛ تحقق من torch.version.cuda وtorch.cuda.is_available()."
    else:
        error = build_error_message(base_dir=base_dir)
    return {
        "ok": ok,
        "mode": mode,
        "diagnostic": final,
        "steps": steps,
        "error": error,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="OUSSAMA Cutter transcription diagnostics"
    )
    parser.add_argument(
        "--repair",
        choices=["cpu", "gpu"],
        help="install the WhisperX transcription stack",
    )
    parser.add_argument(
        "--repair-fallback",
        action="store_true",
        help="install only the faster-whisper fallback",
    )
    parser.add_argument(
        "--ensure",
        action="store_true",
        help="load the real VAD stack and repair it once if needed",
    )
    parser.add_argument(
        "--no-repair",
        action="store_true",
        help="with --ensure: probe only, do not install anything",
    )
    parser.add_argument("--report", help="write JSON report to this folder")
    parser.add_argument(
        "--json", action="store_true", help="print machine-readable output"
    )
    args = parser.parse_args(argv)
    if args.repair_fallback:
        result = repair_fallback()
    elif args.repair:
        result = repair(args.repair)
    elif args.ensure:
        result = ensure_ready(auto_repair=not args.no_repair)
    else:
        diagnostic = diagnose()
        result = {"ok": diagnostic["ready"], "diagnostic": diagnostic}
    if args.report:
        path = write_report(args.report)
        result["report_path"] = path
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        diagnostic = result.get("diagnostic", {})
        print("=== {} — فحص التفريغ ===".format(APP_NAME))
        for name, item in diagnostic.get("packages", {}).items():
            print(
                "{} {}: {}".format(
                    "✅" if item.get("ok") else "❌",
                    name,
                    item.get("version") or item.get("error"),
                )
            )
        if args.ensure:
            vad = result.get("vad") or {}
            print(
                "{} VAD: {}".format(
                    "✅" if vad.get("ok") else "❌", vad.get("error") or "loaded"
                )
            )
            if result.get("cached"):
                print("ℹ️ تم التحقق سابقاً لنفس الإصدارات.")
            elif result.get("auto_repaired"):
                print("🔧 أُصلحت حزمة التفريغ تلقائياً.")
        if result.get("ok"):
            print("✅ مكوّنات التفريغ جاهزة.")
        elif result.get("error"):
            print(result["error"])
        elif args.ensure:
            print("⚠️ VAD غير جاهز؛ سيعمل التطبيق عبر faster-whisper إن توفر.")
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
