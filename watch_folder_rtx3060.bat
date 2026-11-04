@echo off
setlocal EnableExtensions
cd /d "%~dp0"
if not exist "INBOX" mkdir "INBOX"
if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] Run setup_rtx3060.bat first.
  pause
  exit /b 1
)
set "PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True"
set "CUDA_MODULE_LOADING=LAZY"
set "OMP_NUM_THREADS=8"
".venv\Scripts\python.exe" -m scripts.gpu_optimizer --write "gpu_profile.json"
".venv\Scripts\python.exe" -m scripts.watch_folder --input "INBOX" --state-dir ".watch-folder" --app "main_improved.py" --device auto --model auto --gpu-profile "gpu_profile.json" --platform yt_shorts --poll-seconds 5 --settle-seconds 20
pause
