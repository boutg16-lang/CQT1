"""Tests for the system check script (scripts/doctor.py)."""

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import doctor


def test_run_checks_structure():
    checks = doctor.run_checks()
    assert len(checks) > 5
    for c in checks:
        assert set(c.keys()) == {"name", "status", "detail"}
        assert c["status"] in (doctor.OK, doctor.WARN, doctor.FAIL)


def test_check_python_matches_declared_runtime_support():
    result = doctor.check_python()
    version = (sys.version_info.major, sys.version_info.minor)
    supported = doctor.MIN_PYTHON <= version <= doctor.MAX_PYTHON
    assert result["status"] == (doctor.OK if supported else doctor.FAIL)
    assert result["name"] == "Python"
    assert result["detail"]


def test_dependency_checks_do_not_duplicate_modules():
    modules = [module for module, _critical in doctor.DEPENDENCIES]
    assert len(modules) == len(set(modules))


def test_check_binary_found(monkeypatch):
    monkeypatch.setattr(doctor.shutil, "which", lambda name: f"/usr/bin/{name}")
    result = doctor.check_binary("ffmpeg")
    assert result["status"] == doctor.OK
    assert "ffmpeg" in result["detail"]


def test_check_binary_missing_critical(monkeypatch):
    monkeypatch.setattr(doctor.shutil, "which", lambda name: None)
    result = doctor.check_binary("ffmpeg", critical=True)
    assert result["status"] == doctor.FAIL


def test_check_binary_missing_optional(monkeypatch):
    monkeypatch.setattr(doctor.shutil, "which", lambda name: None)
    result = doctor.check_binary("someoptional", critical=False)
    assert result["status"] == doctor.WARN


def test_check_dependency_missing():
    result = doctor.check_dependency("module_that_does_not_exist_xyz", critical=False)
    assert result["status"] == doctor.WARN
    result = doctor.check_dependency("module_that_does_not_exist_xyz", critical=True)
    assert result["status"] == doctor.FAIL


def test_check_dependency_present():
    result = doctor.check_dependency("json", critical=True)
    assert result["status"] == doctor.OK


def test_cuda_runtime_warns_when_cuda_is_unavailable(monkeypatch):
    fake_torch = SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: False))
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    result = doctor.check_cuda_runtime()
    assert result["status"] == doctor.WARN
    assert "CPU fallback" in result["detail"]


def test_cuda_runtime_reports_cudnn_version(monkeypatch):
    fake_torch = SimpleNamespace(
        cuda=SimpleNamespace(is_available=lambda: True),
        version=SimpleNamespace(cuda="12.6"),
        backends=SimpleNamespace(cudnn=SimpleNamespace(version=lambda: 9100)),
    )
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    result = doctor.check_cuda_runtime()
    assert result == {
        "name": "CUDA/cuDNN runtime",
        "status": doctor.OK,
        "detail": "CUDA 12.6, cuDNN 9100",
    }


def test_check_writable(tmp_path):
    result = doctor.check_writable(str(tmp_path))
    assert result["status"] == doctor.OK


def test_main_reports_and_exit_code(capsys):
    # In the sandbox some optional deps are missing but criticals (python,
    # ffmpeg) may vary — assert the report shape, not the final verdict.
    code = doctor.main()
    out = capsys.readouterr().out
    assert "OUSSAMA Cutter Doctor" in out
    assert code in (0, 1)
    if code == 0:
        assert "System ready" in out
    else:
        assert "critical" in out
