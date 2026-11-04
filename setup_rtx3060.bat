@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title OUSSAMA Cutter - RTX 3060 Setup

echo ======================================================
echo OUSSAMA Cutter - Automatic NVIDIA GPU installation
echo ======================================================
echo This installs the CUDA-enabled PyTorch stack, WhisperX,
echo and the faster-whisper recovery backend.
echo.
where nvidia-smi >nul 2>&1
if errorlevel 1 (
  echo [WARNING] nvidia-smi was not found. Install/update the NVIDIA driver first.
  echo Setup will continue, but CUDA readiness must be verified afterwards.
) else (
  nvidia-smi
)
echo.
call install_dependencies.bat gpu full fallback
if errorlevel 1 (
  echo.
  echo [ERROR] Installation failed. Run diagnostics:
  echo .venv\Scripts\python.exe -m scripts.windows_diagnostics --root "%CD%"
  pause
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" exit /b 1
".venv\Scripts\python.exe" -m scripts.gpu_optimizer --write "gpu_profile.json"
set "GPU_PROFILE_EXIT=%ERRORLEVEL%"
".venv\Scripts\python.exe" -m scripts.windows_diagnostics --root "%CD%" --output ".windows-diagnostics.json"
if errorlevel 1 echo [WARNING] Some optional or critical diagnostics need attention. See .windows-diagnostics.json
if not "%GPU_PROFILE_EXIT%"=="0" echo [WARNING] CUDA was not confirmed. Check NVIDIA driver and diagnostics.
echo.
echo Setup finished. Start with run_rtx3060.bat
pause
exit /b 0
