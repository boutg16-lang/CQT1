"""Detect NVIDIA hardware and generate a conservative OUSSAMA Cutter GPU profile.

Usage: python -m scripts.gpu_optimizer [--json] [--write PATH]
The module never changes driver/CUDA installations; it only inspects hardware and
writes an explicit, reproducible profile when requested.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any


def _run(command: list[str], timeout: int = 8) -> str:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
        return result.stdout.strip() if result.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def _torch_gpu() -> dict[str, Any] | None:
    try:
        import torch
        if not torch.cuda.is_available():
            return None
        props = torch.cuda.get_device_properties(0)
        return {
            "name": str(props.name),
            "total_vram_mb": int(props.total_memory // (1024 * 1024)),
            "cuda_runtime": str(getattr(torch.version, "cuda", None) or "unknown"),
            "source": "torch",
            "cuda_available": True,
        }
    except Exception:
        return None


def _nvidia_smi_gpu() -> dict[str, Any] | None:
    binary = shutil.which("nvidia-smi")
    if not binary:
        return None
    output = _run([binary, "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader,nounits"])
    if not output:
        return None
    fields = [part.strip() for part in output.splitlines()[0].split(",")]
    if len(fields) < 3:
        return None
    try:
        memory_mb = int(float(fields[1]))
    except ValueError:
        memory_mb = 0
    return {"name": fields[0], "total_vram_mb": memory_mb, "driver": fields[2], "source": "nvidia-smi", "cuda_available": False}


def _ffmpeg_nvenc() -> bool:
    binary = shutil.which("ffmpeg")
    if not binary:
        return False
    output = _run([binary, "-hide_banner", "-encoders"], timeout=12)
    return "h264_nvenc" in output


def build_profile(gpu: dict[str, Any] | None = None, nvenc: bool | None = None) -> dict[str, Any]:
    gpu = gpu if gpu is not None else (_torch_gpu() or _nvidia_smi_gpu())
    if nvenc is None:
        nvenc = _ffmpeg_nvenc()
    vram = int((gpu or {}).get("total_vram_mb") or 0)
    if vram >= 10_000:
        tier, model, batch, note = "12gb", "large-v3-turbo", 8, "Best quality/performance profile for RTX 3060 12GB-class cards."
    elif vram >= 6_000:
        tier, model, batch, note = "6gb", "large-v3-turbo", 4, "Conservative profile for 6GB cards; use smaller batch if CUDA OOM occurs."
    elif vram >= 4_000:
        tier, model, batch, note = "4gb", "medium", 2, "Reduced model profile for limited VRAM."
    elif gpu:
        tier, model, batch, note = "low-vram", "small", 1, "Low-VRAM profile; CPU fallback may be more reliable."
    else:
        tier, model, batch, note = "cpu", "medium", 1, "No CUDA GPU confirmed; transcription will use CPU unless repaired."
    name = str((gpu or {}).get("name") or "No NVIDIA GPU detected")
    is_3060 = "3060" in name.lower()
    cuda_ready = bool((gpu or {}).get("cuda_available", False))
    use_nvenc = bool(gpu and nvenc)
    return {
        "schema_version": 1,
        "product": "OUSSAMA Cutter",
        "gpu_detected": bool(gpu),
        "cuda_ready": cuda_ready,
        "gpu": gpu or {"name": name, "total_vram_mb": 0},
        "is_rtx_3060": is_3060,
        "vram_tier": tier,
        "transcription": {"device": "cuda" if cuda_ready else "auto", "model": model, "recommended_batch_size": batch, "compute_type": "float16" if cuda_ready else "float32"},
        "video_encoding": {"preferred_encoder": "h264_nvenc" if use_nvenc else "libx264", "nvenc_available": bool(nvenc), "target": "1080x1920", "preset": "p4" if nvenc else "medium"},
        "runtime_environment": {
            "PYTORCH_CUDA_ALLOC_CONF": "expandable_segments:True",
            "CUDA_MODULE_LOADING": "LAZY",
            "OMP_NUM_THREADS": str(max(2, min(8, (os.cpu_count() or 4) // 2))),
        },
        "recommendation": note,
        "limitations": ["GPU detection is hardware/runtime evidence, not a benchmark.", "The profile does not install or update NVIDIA drivers.", "YouTube policy review remains an editorial risk assessment, not a guarantee of platform approval."],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Detect NVIDIA GPU and build an OUSSAMA Cutter performance profile")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    parser.add_argument("--write", metavar="PATH", help="Write profile JSON to PATH")
    args = parser.parse_args()
    profile = build_profile()
    if args.write:
        path = Path(args.write).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_text(json.dumps(profile, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temp, path)
    if args.json:
        print(json.dumps(profile, ensure_ascii=False, indent=2))
    else:
        gpu = profile["gpu"]
        print("=== OUSSAMA Cutter GPU Optimizer ===")
        print("GPU: {}".format(gpu.get("name", "unknown")))
        print("VRAM: {} MB".format(gpu.get("total_vram_mb", 0)))
        print("RTX 3060: {}".format("yes" if profile["is_rtx_3060"] else "not confirmed"))
        print("PyTorch CUDA: {}".format("ready" if profile["cuda_ready"] else "not verified"))
        print("Transcription: {} / {} / batch {}".format(profile["transcription"]["device"], profile["transcription"]["model"], profile["transcription"]["recommended_batch_size"]))
        print("Video encoder: {}".format(profile["video_encoding"]["preferred_encoder"]))
        print(profile["recommendation"])
        if args.write:
            print("Profile written: {}".format(path))
    return 0 if profile["gpu_detected"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
