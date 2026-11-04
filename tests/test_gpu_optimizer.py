from scripts import gpu_optimizer
from scripts.gpu_optimizer import build_profile


def test_rtx_3060_12gb_profile_uses_turbo_and_nvenc():
    profile = build_profile(
        {"name": "NVIDIA GeForce RTX 3060", "total_vram_mb": 12288, "cuda_available": True},
        nvenc=True,
    )
    assert profile["is_rtx_3060"] is True
    assert profile["transcription"]["device"] == "cuda"
    assert profile["transcription"]["model"] == "large-v3-turbo"
    assert profile["transcription"]["recommended_batch_size"] == 8
    assert profile["video_encoding"]["preferred_encoder"] == "h264_nvenc"


def test_six_gb_profile_is_conservative():
    profile = build_profile(
        {"name": "NVIDIA GeForce RTX 3060 Laptop GPU", "total_vram_mb": 6144,
         "cuda_available": True},
        nvenc=False,
    )
    assert profile["vram_tier"] == "6gb"
    assert profile["transcription"]["recommended_batch_size"] == 4
    assert profile["video_encoding"]["preferred_encoder"] == "libx264"


def test_no_gpu_profile_does_not_claim_cuda():
    profile = build_profile(None, nvenc=False)
    assert profile["gpu_detected"] is False
    assert profile["transcription"]["device"] == "auto"
    assert profile["transcription"]["compute_type"] == "float32"
    assert profile["video_encoding"]["preferred_encoder"] == "libx264"


def test_nvidia_smi_only_does_not_claim_pytorch_cuda_ready():
    profile = build_profile(
        {"name": "NVIDIA GeForce RTX 3060", "total_vram_mb": 12288,
         "cuda_available": False},
        nvenc=True,
    )
    assert profile["gpu_detected"] is True
    assert profile["cuda_ready"] is False
    assert profile["transcription"]["device"] == "auto"
    assert profile["transcription"]["compute_type"] == "float32"


def test_h264_nvenc_is_detected_without_hevc(monkeypatch):
    monkeypatch.setattr(gpu_optimizer.shutil, "which", lambda _name: "/usr/bin/ffmpeg")
    monkeypatch.setattr(gpu_optimizer, "_run", lambda *_args, **_kwargs: "h264_nvenc")
    assert gpu_optimizer._ffmpeg_nvenc()
