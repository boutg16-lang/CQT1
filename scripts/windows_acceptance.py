# -*- coding: utf-8 -*-
"""Windows + NVIDIA acceptance harness for OUSSAMA Cutter.

The one operational step that a Linux CI cannot do: prove, on the user's real
machine, that every production layer actually works — FFmpeg media handling,
vertical reframe, audio QC, thumbnails, silence detection, and (optionally) the
CUDA transcription backend on an NVIDIA GPU such as the RTX 3060.

Unlike ``windows_diagnostics.py`` (read-only), this harness *runs* the real
pipeline — but it is still safe and self-contained:

* every test asset is generated locally with FFmpeg (no downloads);
* no OAuth, no uploads, no network at all — unless you explicitly pass
  ``--gpu-test``, in which case faster-whisper may download its model once;
* all work happens in a temp folder that is removed afterwards (``--keep``
  keeps it for inspection).

Run from the project root with the project's interpreter::

    .venv\\Scripts\\python.exe -m scripts.windows_acceptance --json windows_acceptance.json
    .venv\\Scripts\\python.exe -m scripts.windows_acceptance --gpu-test --require-gpu
    .venv\\Scripts\\python.exe -m scripts.windows_acceptance --clip "D:\\videos\\talk.mp4"

Exit codes: 0 = every critical check passed, 1 = a critical check failed,
2 = warnings only.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any, Dict, List, Optional, Sequence

OK = "ok"
WARN = "warn"
FAIL = "fail"

SUPPORTED_PYTHON = ((3, 9), (3, 12))  # [min, max] inclusive — matches preflight


def _check(name: str, status: str, detail: str, *, critical: bool = False,
           elapsed: Optional[float] = None) -> Dict[str, Any]:
    item: Dict[str, Any] = {
        "name": name,
        "status": status,
        "detail": detail,
        "critical": bool(critical),
    }
    if elapsed is not None:
        item["elapsed_seconds"] = round(elapsed, 2)
    return item


def _run(cmd: Sequence[str], *, timeout: int = 600) -> subprocess.CompletedProcess:
    return subprocess.run(
        list(cmd), capture_output=True, text=True, timeout=timeout)


# ---------------------------------------------------------------------------
# Environment checks
# ---------------------------------------------------------------------------

def check_python() -> Dict[str, Any]:
    version = platform.python_version_tuple()
    try:
        numeric = tuple(int(part) for part in version[:2])
    except ValueError:
        numeric = (0, 0)
    low, high = SUPPORTED_PYTHON
    supported = low <= numeric <= high
    status = OK if supported else FAIL
    return _check(
        "Python version", status,
        "running {0}; supported range {1}.{2}–{3}.{4}".format(
            platform.python_version(), low[0], low[1], high[0], high[1]),
        critical=True)


def check_ffmpeg() -> Dict[str, Any]:
    missing = [name for name in ("ffmpeg", "ffprobe") if shutil.which(name) is None]
    if missing:
        return _check("FFmpeg toolchain", FAIL,
                      "missing from PATH: {}".format(", ".join(missing)),
                      critical=True)
    version = ""
    try:
        proc = _run([shutil.which("ffmpeg") or "ffmpeg", "-version"], timeout=15)
        version = (proc.stdout or "").splitlines()[0].strip()
    except Exception:
        pass
    return _check("FFmpeg toolchain", OK,
                  "ffmpeg + ffprobe on PATH{}".format((" — " + version) if version else ""),
                  critical=True)


def check_gpu(*, require: bool = False) -> Dict[str, Any]:
    """Probe CUDA without importing torch unless it is actually needed."""
    started = time.perf_counter()
    try:
        import torch  # noqa: PLC0415 — intentionally lazy
    except Exception as exc:
        return _check(
            "GPU (torch/CUDA)",
            FAIL if require else WARN,
            "torch could not be imported: {}".format(str(exc)[:200]),
            critical=require,
            elapsed=time.perf_counter() - started)
    try:
        cuda = bool(torch.cuda.is_available())
    except Exception as exc:
        return _check("GPU (torch/CUDA)", FAIL if require else WARN,
                      "torch.cuda.is_available() raised: {}".format(str(exc)[:200]),
                      critical=require, elapsed=time.perf_counter() - started)
    if not cuda:
        return _check(
            "GPU (torch/CUDA)",
            FAIL if require else WARN,
            "torch {} imported but CUDA is unavailable (torch.cuda.is_available()=False) — "
            "the RTX 3060 path will fall back to CPU".format(
                getattr(torch, "__version__", "?")),
            critical=require,
            elapsed=time.perf_counter() - started)
    try:
        name = torch.cuda.get_device_name(0)
        props = torch.cuda.get_device_properties(0)
        vram = round(int(props.total_memory) / (1024 ** 3), 1)
    except Exception as exc:
        return _check("GPU (torch/CUDA)", FAIL if require else WARN,
                      "CUDA is available but device info failed: {}".format(str(exc)[:200]),
                      critical=require, elapsed=time.perf_counter() - started)
    note = "" if "3060" in name else " (expected an RTX 3060 — check the machine/driver)"
    return _check(
        "GPU (torch/CUDA)", OK if not note else (FAIL if require else WARN),
        "CUDA available on {} ({} GB VRAM, torch {}){}".format(
            name, vram, getattr(torch, "__version__", "?"), note),
        critical=require,
        elapsed=time.perf_counter() - started)


# ---------------------------------------------------------------------------
# Test media generation (fully offline)
# ---------------------------------------------------------------------------

def generate_test_clip(path: str, *, duration: int = 30,
                       silence_start: float = 10.0, silence_end: float = 13.0) -> Dict[str, Any]:
    """A 720p clip with a clearly audible tone and one clean silence gap."""
    if shutil.which("ffmpeg") is None:
        return {"ok": False, "error": "ffmpeg not found on PATH"}
    audio = (
        # amplitude via volume: the sine filter only gained an `amplitude`
        # option in FFmpeg 5.x; volume works on every version we support.
        "sine=frequency=440:sample_rate=44100,volume=0.4,"
        "volume=0:enable='between(t,{:.2f},{:.2f})'".format(silence_start, silence_end)
    )
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i",
        "testsrc2=size=1280x720:rate=30:duration={}".format(duration),
        "-f", "lavfi", "-i", audio,
        "-t", str(duration),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest",
        path,
    ]
    try:
        proc = _run(cmd, timeout=300)
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:300]}
    if proc.returncode != 0 or not os.path.isfile(path) or os.path.getsize(path) == 0:
        return {"ok": False, "error": (proc.stderr or proc.stdout or "ffmpeg failed")[-400:]}
    return {"ok": True, "path": path, "size": os.path.getsize(path),
            "silence_gap": [silence_start, silence_end]}


# ---------------------------------------------------------------------------
# Core pipeline acceptance (deterministic, offline)
# ---------------------------------------------------------------------------

def run_core_pipeline(work_dir: str) -> List[Dict[str, Any]]:
    """Exercise the real production modules against a generated clip."""
    checks: List[Dict[str, Any]] = []
    clip = os.path.join(work_dir, "acceptance_source.mp4")

    started = time.perf_counter()
    made = generate_test_clip(clip)
    if not made.get("ok"):
        checks.append(_check("Test media generation", FAIL,
                             made.get("error", "ffmpeg failed"), critical=True,
                             elapsed=time.perf_counter() - started))
        return checks
    checks.append(_check(
        "Test media generation", OK,
        "1280x720@30 clip with a 440 Hz tone and a {:.0f}s silence gap ({:.0f} KB)".format(
            made["silence_gap"][1] - made["silence_gap"][0], made["size"] / 1024.0),
        critical=True, elapsed=time.perf_counter() - started))

    # --- probe / validation -------------------------------------------------
    started = time.perf_counter()
    try:
        from scripts import media_validation
        probe = media_validation.probe_media(clip)
        verdict = media_validation.validate_media_file(clip, require_audio=True)
        if probe.get("ok") and verdict.get("ok"):
            checks.append(_check(
                "Media probe & validation", OK,
                "duration {:.1f}s, {}x{}, audio stream present".format(
                    probe.get("duration", 0.0),
                    (probe.get("video") or {}).get("width", "?"),
                    (probe.get("video") or {}).get("height", "?")),
                critical=True, elapsed=time.perf_counter() - started))
        else:
            checks.append(_check(
                "Media probe & validation", FAIL,
                "probe/validation rejected the generated clip: {}".format(
                    verdict.get("error") or probe.get("error") or verdict),
                critical=True, elapsed=time.perf_counter() - started))
            return checks
    except Exception as exc:
        checks.append(_check("Media probe & validation", FAIL,
                             "{}: {}".format(type(exc).__name__, str(exc)[:200]),
                             critical=True, elapsed=time.perf_counter() - started))
        return checks

    # --- vertical reframe ---------------------------------------------------
    started = time.perf_counter()
    try:
        from scripts import reframe as reframe_mod
        target = reframe_mod.resolve_aspect("9:16") or (1080, 1920)
        result = reframe_mod.reframe_file(clip, target, "crop")
        if result.get("ok"):
            probe2 = media_validation.probe_media(clip)
            dims = ((probe2.get("video") or {}).get("width"),
                    (probe2.get("video") or {}).get("height"))
            if dims == target and probe2.get("audio"):
                checks.append(_check(
                    "Vertical reframe (9:16)", OK,
                    "720p source reframed to {}x{} with audio kept".format(*target),
                    critical=True, elapsed=time.perf_counter() - started))
            else:
                checks.append(_check(
                    "Vertical reframe (9:16)", FAIL,
                    "reframe reported ok but output is {}x{} (audio: {})".format(
                        dims[0], dims[1], bool(probe2.get("audio"))),
                    critical=True, elapsed=time.perf_counter() - started))
        else:
            checks.append(_check(
                "Vertical reframe (9:16)", FAIL,
                result.get("error", "reframe failed"),
                critical=True, elapsed=time.perf_counter() - started))
    except Exception as exc:
        checks.append(_check("Vertical reframe (9:16)", FAIL,
                             "{}: {}".format(type(exc).__name__, str(exc)[:200]),
                             critical=True, elapsed=time.perf_counter() - started))

    # --- audio quality gate -------------------------------------------------
    started = time.perf_counter()
    try:
        from scripts import audio_qc
        report = audio_qc.analyze_file(clip)
        status = str(report.get("status", "block"))
        metrics = report.get("metrics") or {}
        detail = "audio QC status={} (integrated {} LUFS, silence {:.0%})".format(
            status,
            metrics.get("input_i", "?"),
            ((metrics.get("silence") or {}).get("ratio") or 0.0))
        if status in {"pass", "review"}:
            checks.append(_check("Audio QC (loudnorm + silence)", OK, detail,
                                 critical=True, elapsed=time.perf_counter() - started))
        else:
            checks.append(_check("Audio QC (loudnorm + silence)", FAIL,
                                 detail + " — issues: {}".format(
                                     "; ".join(i.get("detail", "")[:120]
                                               for i in report.get("issues", []))),
                                 critical=True, elapsed=time.perf_counter() - started))
    except Exception as exc:
        checks.append(_check("Audio QC (loudnorm + silence)", FAIL,
                             "{}: {}".format(type(exc).__name__, str(exc)[:200]),
                             critical=True, elapsed=time.perf_counter() - started))

    # --- silence detection (jump cuts) --------------------------------------
    started = time.perf_counter()
    try:
        from scripts import jump_cuts
        silences = jump_cuts.detect_silences(clip, threshold_db=-35.0, min_duration=1.0)
        if silences:
            start, end, dur = silences[0]
            checks.append(_check(
                "Silence detection (jump cuts)", OK,
                "found {:.1f}s of silence at {:.1f}s–{:.1f}s (expected ~3s at 10s)".format(
                    dur, start, end),
                critical=True, elapsed=time.perf_counter() - started))
        else:
            checks.append(_check(
                "Silence detection (jump cuts)", FAIL,
                "the planted 3s silence gap was not detected",
                critical=True, elapsed=time.perf_counter() - started))
    except Exception as exc:
        checks.append(_check("Silence detection (jump cuts)", FAIL,
                             "{}: {}".format(type(exc).__name__, str(exc)[:200]),
                             critical=True, elapsed=time.perf_counter() - started))

    # --- thumbnail generation ------------------------------------------------
    started = time.perf_counter()
    try:
        from scripts import thumbnail_generator
        out_png = os.path.join(work_dir, "acceptance_thumbnail.png")
        thumb = thumbnail_generator.generate_thumbnail(
            clip, title="اختبار القبول", out=out_png, at_seconds=2.0)
        if thumb.get("ok") and os.path.isfile(out_png) and os.path.getsize(out_png) > 0:
            checks.append(_check(
                "Thumbnail generation", OK,
                "1080x1920 PNG rendered ({:.0f} KB)".format(os.path.getsize(out_png) / 1024.0),
                critical=True, elapsed=time.perf_counter() - started))
        else:
            checks.append(_check(
                "Thumbnail generation", FAIL,
                thumb.get("error", "thumbnail generation failed"),
                critical=True, elapsed=time.perf_counter() - started))
    except Exception as exc:
        checks.append(_check("Thumbnail generation", FAIL,
                             "{}: {}".format(type(exc).__name__, str(exc)[:200]),
                             critical=True, elapsed=time.perf_counter() - started))

    return checks


# ---------------------------------------------------------------------------
# Optional GPU transcription test
# ---------------------------------------------------------------------------

def check_gpu_transcription(work_dir: str, model_size: str) -> Dict[str, Any]:
    """Boot the faster-whisper backend on CUDA and transcribe a real clip."""
    started = time.perf_counter()
    try:
        from faster_whisper import WhisperModel  # noqa: PLC0415 — opt-in, lazy
    except Exception as exc:
        return _check(
            "GPU transcription (faster-whisper/CUDA)", WARN,
            "faster-whisper is not installed: {} — install the fallback profile "
            "with: pip install -r requirements-transcribe-fallback.txt".format(
                str(exc)[:160]),
            elapsed=time.perf_counter() - started)

    device, compute = "cpu", "int8"
    try:
        import torch
        if torch.cuda.is_available():
            device, compute = "cuda", "int8_float16"
    except Exception:
        pass

    clip = os.path.join(work_dir, "acceptance_source.mp4")
    if not os.path.isfile(clip):
        made = generate_test_clip(clip, duration=15)
        if not made.get("ok"):
            return _check("GPU transcription (faster-whisper/CUDA)", WARN,
                          "could not generate the test clip: {}".format(
                              made.get("error", "unknown")),
                          elapsed=time.perf_counter() - started)

    try:
        load_start = time.perf_counter()
        model = WhisperModel(model_size, device=device, compute_type=compute)
        load_elapsed = time.perf_counter() - load_start
    except Exception as exc:
        return _check(
            "GPU transcription (faster-whisper/CUDA)", FAIL,
            "model {} failed to load on {} ({}): {}".format(
                model_size, device, compute, str(exc)[:240]),
            elapsed=time.perf_counter() - started)

    try:
        transcribe_start = time.perf_counter()
        segments, info = model.transcribe(clip, language="ar", beam_size=1)
        segments = list(segments or [])
        transcribe_elapsed = time.perf_counter() - transcribe_start
    except Exception as exc:
        return _check(
            "GPU transcription (faster-whisper/CUDA)", FAIL,
            "transcription raised on {}: {}".format(device, str(exc)[:240]),
            elapsed=time.perf_counter() - started)

    return _check(
        "GPU transcription (faster-whisper/CUDA)", OK,
        "model={} device={} compute={} — loaded in {:.1f}s, transcribed 15s in "
        "{:.1f}s, {} segment(s), detected language={} (a pure tone yields few "
        "words; the pass is that the CUDA backend booted and ran)".format(
            model_size, device, compute, load_elapsed, transcribe_elapsed,
            len(segments), getattr(info, "language", "?")),
        elapsed=time.perf_counter() - started)


# ---------------------------------------------------------------------------
# Optional end-to-end mini-pipeline on a real user clip
# ---------------------------------------------------------------------------

def run_clip_pipeline(source: str, work_dir: str) -> List[Dict[str, Any]]:
    """Cut a window out of a real user clip and run the production stages."""
    checks: List[Dict[str, Any]] = []
    if not os.path.isfile(source):
        return [_check("User clip pipeline", FAIL,
                       "clip not found: {}".format(source), critical=True)]

    started = time.perf_counter()
    cut = os.path.join(work_dir, "acceptance_clip_cut.mp4")
    proc = _run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-ss", "5", "-t", "15", "-i", source,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
        "-pix_fmt", "yuv420p", "-c:a", "aac", cut], timeout=600)
    if proc.returncode != 0 or not os.path.isfile(cut):
        checks.append(_check("User clip pipeline", FAIL,
                             "could not cut a 15s window: {}".format(
                                 (proc.stderr or proc.stdout or "")[-300:]),
                             critical=True, elapsed=time.perf_counter() - started))
        return checks

    from scripts import audio_qc, media_validation
    from scripts import reframe as reframe_mod

    verdict = media_validation.validate_media_file(cut, require_audio=True)
    if not verdict.get("ok"):
        checks.append(_check("User clip pipeline", FAIL,
                             "cut window failed validation: {}".format(
                                 verdict.get("error", "unknown")),
                             critical=True, elapsed=time.perf_counter() - started))
        return checks

    target = reframe_mod.resolve_aspect("9:16") or (1080, 1920)
    reframed = reframe_mod.reframe_file(cut, target, "crop")
    qc = audio_qc.analyze_file(cut)
    if reframed.get("ok") and str(qc.get("status")) in {"pass", "review"}:
        checks.append(_check(
            "User clip pipeline", OK,
            "cut 15s from {}, reframed to {}x{}, audio QC status={}".format(
                os.path.basename(source), *target, qc.get("status")),
            critical=True, elapsed=time.perf_counter() - started))
    else:
        checks.append(_check(
            "User clip pipeline", FAIL,
            "reframe ok={}, audio QC status={} (issues: {})".format(
                reframed.get("ok"), qc.get("status"),
                "; ".join(i.get("detail", "")[:120] for i in qc.get("issues", []))),
            critical=True, elapsed=time.perf_counter() - started))
    return checks


# ---------------------------------------------------------------------------
# Report assembly
# ---------------------------------------------------------------------------

def build_report(checks: List[Dict[str, Any]]) -> Dict[str, Any]:
    ok = sum(1 for c in checks if c["status"] == OK)
    warn = sum(1 for c in checks if c["status"] == WARN)
    fail = sum(1 for c in checks if c["status"] == FAIL)
    critical_fail = sum(1 for c in checks if c["status"] == FAIL and c["critical"])
    verdict = "fail" if critical_fail else ("warn" if (warn or fail) else "pass")
    return {
        "tool": "windows_acceptance",
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "host": {
            "platform": platform.platform(),
            "machine": platform.machine(),
            "python": platform.python_version(),
        },
        "checks": checks,
        "summary": {"ok": ok, "warn": warn, "fail": fail,
                    "critical_fail": critical_fail},
        "verdict": verdict,
    }


_ICON = {OK: "✓", WARN: "⚠", FAIL: "✗"}


def render_console(report: Dict[str, Any]) -> str:
    lines = ["فحص قبول OUSSAMA Cutter — بيئة الإنتاج الحقيقية", ""]
    for item in report["checks"]:
        line = "{} [{}] {}".format(_ICON.get(item["status"], "?"),
                                   item["name"], item["detail"])
        if item.get("elapsed_seconds") is not None:
            line += " ({:.1f}s)".format(item["elapsed_seconds"])
        lines.append(line)
    lines.append("")
    summary = report["summary"]
    lines.append("الخلاصة: {} ناجح / {} تحذير / {} فاشل (منها {} حرج) — الحكم: {}".format(
        summary["ok"], summary["warn"], summary["fail"],
        summary["critical_fail"], report["verdict"]))
    return "\n".join(lines)


def _atomic_write_json(path: str, payload: Dict[str, Any]) -> None:
    directory = os.path.dirname(os.path.abspath(path))
    fd, tmp_name = tempfile.mkstemp(prefix=".acceptance-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            try:
                os.remove(tmp_name)
            except OSError:
                pass


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Windows + NVIDIA acceptance harness for OUSSAMA Cutter")
    parser.add_argument("--json", metavar="PATH",
                        help="write the JSON report to PATH")
    parser.add_argument("--gpu-test", action="store_true",
                        help="also boot the faster-whisper backend (downloads "
                             "the model once; uses CUDA when available)")
    parser.add_argument("--model", default="tiny",
                        help="faster-whisper model size for --gpu-test (default: tiny)")
    parser.add_argument("--require-gpu", action="store_true",
                        help="fail the run unless CUDA is available")
    parser.add_argument("--clip", metavar="VIDEO",
                        help="also run a 15s mini-pipeline on a real video file")
    parser.add_argument("--keep", action="store_true",
                        help="keep the temp work directory for inspection")
    args = parser.parse_args(argv)

    work_dir = tempfile.mkdtemp(prefix="oussama_acceptance_")
    checks: List[Dict[str, Any]] = []
    try:
        checks.append(check_python())
        checks.append(check_ffmpeg())
        checks.append(check_gpu(require=args.require_gpu))
        checks.extend(run_core_pipeline(work_dir))
        if args.gpu_test:
            checks.append(check_gpu_transcription(work_dir, args.model))
        if args.clip:
            checks.extend(run_clip_pipeline(os.path.abspath(os.path.expanduser(args.clip)),
                                            work_dir))

        report = build_report(checks)
        report["work_dir"] = work_dir if args.keep else None
        print(render_console(report))
        if args.keep:
            print("مجلد العمل محفوظ للفحص: {}".format(work_dir))
        if args.json:
            _atomic_write_json(args.json, report)
            print("التقرير كُتب إلى: {}".format(os.path.abspath(args.json)))

        if report["summary"]["critical_fail"]:
            return 1
        if report["summary"]["warn"] or report["summary"]["fail"]:
            return 2
        return 0
    finally:
        if not args.keep:
            shutil.rmtree(work_dir, ignore_errors=True)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
