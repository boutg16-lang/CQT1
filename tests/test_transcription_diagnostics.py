from scripts.transcription_diagnostics import (
    TranscriptionUnavailableError,
    _version_supported,
    build_error_message,
    diagnose,
)


def test_diagnostics_is_json_serializable():
    report = diagnose(".")
    assert report["app"] == "OUSSAMA Cutter"
    assert isinstance(report["packages"], dict)
    assert set(report["packages"]) == {
        "torch", "torchaudio", "ctranslate2", "whisperx", "transformers",
        "huggingface_hub", "tokenizers", "setuptools", "faster_whisper",
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


def test_supported_transcription_versions_reject_user_reported_mix():
    assert not _version_supported("3.2.0", "3.3.1", "3.3.2")
    assert not _version_supported("4.57.6", "5.14.1", "5.16.0")
    assert not _version_supported("0.36.2", "1.16.0", "2.0.0")
    assert _version_supported("3.3.1", "3.3.1", "3.3.2")
    assert not _version_supported("4.3.0", "4.4.0", "4.5.0")
    assert _version_supported("4.4.0", "4.4.0", "4.5.0")


def test_diagnostics_rejects_mixed_stack_but_keeps_fallback(monkeypatch):
    import scripts.transcription_diagnostics as diagnostics

    versions = {
        "torch": "2.8.0+cu126",
        "torchaudio": "2.8.0+cu126",
        "ctranslate2": "4.4.0",
        "whisperx": "3.2.0",
        "transformers": "4.57.6",
        "huggingface_hub": "0.36.2",
        "tokenizers": "0.22.2",
        "setuptools": "84.2.1",
        "faster_whisper": "1.2.1",
    }
    monkeypatch.setattr(
        diagnostics,
        "_probe",
        lambda name: {"module": name, "ok": True, "version": versions[name], "error": ""},
    )
    monkeypatch.setattr(
        diagnostics,
        "_torch_runtime",
        lambda: {"cuda_available": True, "cuda_version": "12.6"},
    )

    report = diagnostics.diagnose(".")

    assert report["primary_ready"] is False
    assert report["fallback_ready"] is True
    assert report["ready"] is True
    assert report["backend"] == "faster-whisper"
    assert report["incompatible"] == ["whisperx", "transformers", "huggingface_hub"]


def test_huggingface_conflict_message_is_actionable():
    message = build_error_message(
        "cannot import name 'is_offline_mode' from 'huggingface_hub'",
        base_dir=".",
    )
    assert "Transformers 5.14–5.15" in message
    assert "whisperx==3.3.1" in message
    assert "لا تبدأ الاختبار باستيراد is_offline_mode يدوياً" in message
    assert "لا تغيّر إصدار Torch CUDA" in message
    assert "uv pip install --python ./.venv/Scripts/python.exe" in message
    assert "-r requirements-transcribe.txt" in message


def test_ctranslate2_windows_import_uses_local_resource_shim(monkeypatch, tmp_path):
    import sys
    from importlib import resources
    from types import ModuleType

    import scripts.transcription_diagnostics as diagnostics

    monkeypatch.setattr(resources, "files", lambda package: tmp_path)
    imported = []

    def fake_import(module_name):
        if module_name == "pkg_resources":
            raise ModuleNotFoundError("No module named 'pkg_resources'", name="pkg_resources")
        if module_name == "ctranslate2":
            shim = sys.modules["pkg_resources"]
            assert shim.resource_filename(module_name, "") == str(tmp_path)
            imported.append(module_name)
            return ModuleType(module_name)
        raise AssertionError(module_name)

    monkeypatch.setattr(diagnostics.sys, "platform", "win32")
    monkeypatch.setattr(diagnostics.importlib, "import_module", fake_import)
    diagnostics.prepare_ctranslate2_windows_import()

    assert imported == ["ctranslate2"]
    assert "pkg_resources" not in sys.modules


def test_pkg_resources_is_not_required_for_whisperx_backend(monkeypatch):
    import scripts.transcription_diagnostics as diagnostics

    versions = {
        "torch": "2.8.0+cu126",
        "torchaudio": "2.8.0+cu126",
        "ctranslate2": "4.4.0",
        "whisperx": "3.3.1",
        "transformers": "5.14.1",
        "huggingface_hub": "1.16.0",
        "tokenizers": "0.22.2",
        "setuptools": "84.2.1",
        "faster_whisper": "1.2.1",
    }

    monkeypatch.setattr(
        diagnostics,
        "_probe",
        lambda name: {"module": name, "ok": True, "version": versions[name], "error": ""},
    )
    monkeypatch.setattr(
        diagnostics,
        "_torch_runtime",
        lambda: {"cuda_available": True, "cuda_version": "12.6"},
    )

    report = diagnostics.diagnose(".")

    assert "pkg_resources" not in report["packages"]
    assert report["primary_ready"] is True
    assert report["backend"] == "whisperx"


def test_pkg_resources_error_never_recommends_vulnerable_setuptools(monkeypatch):
    import scripts.transcription_diagnostics as diagnostics

    monkeypatch.setattr(diagnostics.sys, "platform", "win32")

    monkeypatch.setattr(
        diagnostics,
        "diagnose",
        lambda base_dir=None: {
            "missing": ["ctranslate2"],
            "incompatible": [],
            "fallback_ready": False,
            "torch_runtime": {"cuda_available": True},
        },
    )
    message = build_error_message("No module named 'pkg_resources'", base_dir=".")
    assert "لا تخفّض setuptools إلى 81.x" in message
    assert "setuptools>=81.0.0,<82" not in message
    assert "--repair gpu" in message
    assert "CTranslate2" in message
    assert "importlib.resources" in message


def test_repair_installs_transformers_5_compatibility_range(monkeypatch):
    import scripts.transcription_diagnostics as diagnostics

    reports = [
        {"free_disk_mb": 5000},
        {"ready": True, "primary_ready": True, "gpu_ready": False},
    ]
    commands = []
    monkeypatch.setattr(diagnostics, "diagnose", lambda base_dir=None: reports.pop(0))
    monkeypatch.setattr(
        diagnostics,
        "_run_install",
        lambda command, timeout=1800: commands.append(command) or {"ok": True},
    )

    result = diagnostics.repair("cpu")

    assert "torch==2.8.0" in commands[0]
    assert "torchvision==0.23.0" in commands[0]
    assert "torchaudio==2.8.0" in commands[0]
    assert commands[0][commands[0].index("--index-url") + 1].endswith("/cpu")

    assert result["ok"] is True
    assert "-r" in commands[-1]
    assert commands[-1][-1].endswith("requirements-transcribe.txt")
    assert "--upgrade" not in commands[-1]
    assert "torch==2.8.0" not in commands[-1]


def test_gpu_repair_preserves_working_cuda_torch(monkeypatch):
    import scripts.transcription_diagnostics as diagnostics

    reports = [
        {
            "free_disk_mb": 5000,
            "packages": {"torch": {"ok": True}, "torchaudio": {"ok": True}},
            "torch_runtime": {"cuda_available": True},
        },
        {"ready": True, "primary_ready": True, "gpu_ready": True},
    ]
    commands = []
    monkeypatch.setattr(diagnostics, "diagnose", lambda base_dir=None: reports.pop(0))
    monkeypatch.setattr(
        diagnostics,
        "_run_install",
        lambda command, timeout=1800: commands.append(command) or {"ok": True},
    )

    result = diagnostics.repair("gpu")

    assert result["ok"] is True
    assert len(commands) == 1
    assert "torch==2.8.0" not in commands[0]
    assert "-r" in commands[0]
    assert commands[0][-1].endswith("requirements-transcribe.txt")
    assert "--upgrade" not in commands[0]


def test_gpu_repair_uses_supported_cuda_torch_pair_when_needed(monkeypatch):
    import scripts.transcription_diagnostics as diagnostics

    reports = [
        {
            "free_disk_mb": 5000,
            "packages": {"torch": {"ok": False}, "torchaudio": {"ok": False}},
            "torch_runtime": {"cuda_available": False},
        },
        {"ready": True, "primary_ready": True, "gpu_ready": True},
    ]
    commands = []
    monkeypatch.setattr(diagnostics, "diagnose", lambda base_dir=None: reports.pop(0))
    monkeypatch.setattr(
        diagnostics,
        "_run_install",
        lambda command, timeout=1800: commands.append(command) or {"ok": True},
    )

    result = diagnostics.repair("gpu")

    assert result["ok"] is True
    assert len(commands) == 2
    assert "torch==2.8.0" in commands[0]
    assert "torchvision==0.23.0" in commands[0]
    assert "torchaudio==2.8.0" in commands[0]
    assert commands[0][commands[0].index("--index-url") + 1].endswith("/cu126")
    assert commands[1][-1].endswith("requirements-transcribe.txt")


def test_repair_does_not_report_fallback_as_a_whisperx_repair(monkeypatch):
    import scripts.transcription_diagnostics as diagnostics

    reports = [
        {
            "free_disk_mb": 5000,
            "packages": {"torch": {"ok": True}, "torchaudio": {"ok": True}},
            "torch_runtime": {"cuda_available": False},
        },
        {"ready": True, "primary_ready": False, "fallback_ready": True, "gpu_ready": False},
    ]
    monkeypatch.setattr(diagnostics, "diagnose", lambda base_dir=None: reports.pop(0))
    monkeypatch.setattr(diagnostics, "_run_install", lambda *args, **kwargs: {"ok": True})
    monkeypatch.setattr(diagnostics, "build_error_message", lambda base_dir=None: "WhisperX stack still incompatible")

    result = diagnostics.repair("cpu")

    assert result["ok"] is False
    assert result["error"] == "WhisperX stack still incompatible"


def test_transcription_install_manifests_pin_the_locked_stack():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    manifests = (
        root / "requirements-transcribe.txt",
        root / "install_dependencies.bat",
        root / "pyproject.toml",
    )
    for manifest in manifests:
        text = manifest.read_text(encoding="utf-8")
        assert "whisperx==3.3.1" in text
        assert "ctranslate2>=4.4.0,<4.5.0" in text
        assert "transformers>=5.14.1,<5.16" in text
        assert "huggingface-hub>=1.16.0,<2.0" in text
        assert "tokenizers>=0.22.2,<0.23.1" in text
        assert "setuptools>=84.1.0,<85" in text
        if manifest.name == "install_dependencies.bat":
            install_line = next(line for line in text.splitlines() if '"whisperx==3.3.1"' in line)
            assert '"setuptools>=84.1.0,<85"' in install_line
            assert '"ctranslate2>=4.4.0,<4.5.0"' in install_line
            assert "import pkg_resources" not in text
            assert "Version('84.1.0')" in text and "Version('85.0.0')" in text
            assert "--upgrade" not in install_line
