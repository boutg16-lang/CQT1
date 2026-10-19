from scripts.transcription_diagnostics import (
    TranscriptionUnavailableError,
    build_error_message,
    diagnose,
)


def test_diagnostics_is_json_serializable():
    report = diagnose(".")
    assert report["app"] == "OUSSAMA Cutter"
    assert isinstance(report["packages"], dict)
    assert set(report["packages"]) == {
        "torch", "torchaudio", "whisperx", "transformers",
        "tokenizers", "faster_whisper",
    }
    assert report["backend"] in {"none", "whisperx", "faster-whisper"}


def test_tokenizers_conflict_message_is_actionable():
    message = build_error_message(
        "ImportError: tokenizers>=0.22.0,<=0.23.0 is required ... but found "
        "tokenizers==0.23.1",
        base_dir=".",
    )
    assert "تعارض إصدارات tokenizers" in message
    assert "tokenizers==0.22.2" not in message  # guidance uses the range form
    assert "tokenizers>=0.22.0,<0.23.1" in message


def test_missing_stack_message_is_actionable():
    message = build_error_message(
        "No module named whisperx",
        "No module named torch",
        base_dir=".",
    )
    assert "OUSSAMA Cutter" in message
    assert "setup_on_d.ps1 -Mode Full -Transcription cpu" in message
    assert "--repair cpu" in message
    assert "لا تستخدم الترجمة الوهمية" in message


def test_dependency_error_is_marked_for_no_retry():
    error = TranscriptionUnavailableError("missing stack")
    assert error.dependency_error is True


def test_huggingface_conflict_message_is_actionable():
    message = build_error_message(
        "cannot import name 'is_offline_mode' from 'huggingface_hub'",
        base_dir=".",
    )
    assert "Transformers 5.14–5.15" in message
    assert "transformers>=5.14.1,<5.16" in message
    assert "huggingface-hub>=1.16.0,<2.0" in message
    assert "Gradio 6.22" in message
    assert "tokenizers>=0.22.2,<0.23.1" in message
    assert "لا تغيّر إصدار Torch CUDA" in message
    assert "uv pip install --python ./.venv/Scripts/python.exe" in message
    assert "'huggingface-hub>=1.16.0,<2.0'" in message


def test_repair_installs_transformers_5_compatibility_range(monkeypatch):
    import scripts.transcription_diagnostics as diagnostics

    reports = [
        {"free_disk_mb": 5000},
        {"ready": True, "gpu_ready": False},
    ]
    commands = []
    monkeypatch.setattr(diagnostics, "diagnose", lambda base_dir=None: reports.pop(0))
    monkeypatch.setattr(
        diagnostics,
        "_run_install",
        lambda command, timeout=1800: commands.append(command) or {"ok": True},
    )

    result = diagnostics.repair("cpu")

    assert result["ok"] is True
    assert any(
        "transformers>=5.14.1,<5.16" in command
        and "huggingface-hub>=1.16.0,<2.0" in command
        and "tokenizers>=0.22.2,<0.23.1" in command
        and "numpy<2" in command
        for command in commands
    )


def test_gpu_repair_preserves_working_cuda_torch(monkeypatch):
    import scripts.transcription_diagnostics as diagnostics

    reports = [
        {
            "free_disk_mb": 5000,
            "packages": {"torch": {"ok": True}, "torchaudio": {"ok": True}},
            "torch_runtime": {"cuda_available": True},
        },
        {"ready": True, "gpu_ready": True},
    ]
    commands = []
    monkeypatch.setattr(diagnostics, "diagnose", lambda base_dir=None: reports.pop(0))
    monkeypatch.setattr(
        diagnostics,
        "_run_install",
        lambda command, timeout=1800: commands.append(command) or {"ok": True},
    )

    monkeypatch.setattr("importlib.metadata.version", lambda name: "3.2.0")
    result = diagnostics.repair("gpu")

    assert result["ok"] is True
    assert len(commands) == 1
    assert "torch==2.6.0" not in commands[0]
    assert "huggingface-hub>=1.16.0,<2.0" in commands[0]
    assert "whisperx" not in commands[0]


def test_transcription_install_manifests_use_the_locked_hub_one_x_stack():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    manifests = (
        root / "requirements-transcribe.txt",
        root / "install_dependencies.bat",
        root / "pyproject.toml",
    )
    for manifest in manifests:
        text = manifest.read_text(encoding="utf-8")
        assert "transformers>=5.14.1,<5.16" in text
        assert "huggingface-hub>=1.16.0,<2.0" in text
