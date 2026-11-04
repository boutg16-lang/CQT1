@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title OUSSAMA Cutter - RTX 3060 Mode
set "PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True"
set "CUDA_MODULE_LOADING=LAZY"
set "OMP_NUM_THREADS=8"
if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] Runtime missing. Run setup_rtx3060.bat first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m scripts.gpu_optimizer --write "gpu_profile.json"
if errorlevel 2 echo [WARNING] NVIDIA CUDA was not detected. The application may use CPU fallback.
call run_webui.bat %*
exit /b %ERRORLEVEL%
