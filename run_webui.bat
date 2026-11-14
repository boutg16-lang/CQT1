@echo off
setlocal EnableExtensions

title OUSSAMA Cutter WebUI
cd /d "%~dp0"

set "PYTHON=%CD%\.venv\Scripts\python.exe"
if not exist "%PYTHON%" goto NO_PYTHON

set "RUNTIME_TMP=%CD%\.runtime-tmp"
if not exist "%RUNTIME_TMP%" mkdir "%RUNTIME_TMP%" >nul 2>&1
set "TEMP=%RUNTIME_TMP%"
set "TMP=%RUNTIME_TMP%"
set "UV_CACHE_DIR=%CD%\.uv-cache"
set "UV_NO_CACHE=1"

if exist "bin\ffmpeg.exe" set "PATH=%CD%\bin;%PATH%"

if exist "scripts\repair_venv_metadata.ps1" powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\repair_venv_metadata.ps1" -PythonPath "%PYTHON%"

echo.
echo [preflight] Checking WebSockets compatibility...
"%PYTHON%" scripts/ensure_websockets.py --check
if not errorlevel 1 goto WEBSOCKETS_READY
powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\windows_installer_preflight.ps1" -PythonPath "%PYTHON%"
if errorlevel 3 goto WEBSOCKETS_PROCESS_UNKNOWN
if errorlevel 2 goto WEBSOCKETS_PROCESS_IN_USE
if errorlevel 1 goto WEBSOCKETS_PROCESS_UNKNOWN
echo [preflight] Repairing WebSockets without changing other packages...
"%PYTHON%" scripts/ensure_websockets.py --repair
if errorlevel 1 goto WEBSOCKETS_REPAIR_FAILED
goto WEBSOCKETS_READY

:WEBSOCKETS_PROCESS_IN_USE
echo [ERROR] A process is using modules from this project's Python environment.
echo [INFO] Close the PID listed above in Task Manager, then retry setup.
goto WEBSOCKETS_FAILED

:WEBSOCKETS_PROCESS_UNKNOWN
echo [ERROR] Could not inspect whether this project's Python environment is idle.
echo [INFO] Close OUSSAMA Cutter and Python processes from this project, then retry.
goto WEBSOCKETS_FAILED

:WEBSOCKETS_REPAIR_FAILED
echo [ERROR] Could not repair the WebSockets compatibility dependency.
echo [INFO] Close the listed process and retry; restart Windows if it remains locked.
goto WEBSOCKETS_FAILED

:WEBSOCKETS_FAILED
if "%VIRALCUTTER_NO_PAUSE%"=="1" exit /b 1
pause
exit /b 1

:WEBSOCKETS_READY
echo [preflight] Checking environment...
"%PYTHON%" -m scripts.preflight --auto-fix --ensure-upload
set "PREFLIGHT_EXIT=%ERRORLEVEL%"

if "%PREFLIGHT_EXIT%"=="0" goto START_WEBUI
if "%PREFLIGHT_EXIT%"=="2" goto START_WEBUI_WARNINGS

echo.
echo [ERROR] Preflight failed. Fix the items above and try again.
echo [INFO] Full transcription: setup_on_d.ps1 -Mode Full -Transcription cpu
echo [INFO] NVIDIA transcription: setup_on_d.ps1 -Mode Full -Transcription gpu
echo [INFO] YouTube OAuth repair: install_dependencies.bat upload

if "%VIRALCUTTER_NO_PAUSE%"=="1" exit /b 1
pause
exit /b 1

:START_WEBUI_WARNINGS
echo [WARNING] Optional checks reported warnings. Starting WebUI.

:START_WEBUI
echo [webui] Starting OUSSAMA Cutter...
"%PYTHON%" webui\app.py %*
set "WEBUI_EXIT=%ERRORLEVEL%"

if "%VIRALCUTTER_NO_PAUSE%"=="1" exit /b %WEBUI_EXIT%
if not "%WEBUI_EXIT%"=="0" (
    echo.
    echo [webui] Exited with code %WEBUI_EXIT%.
    pause
)
exit /b %WEBUI_EXIT%

:NO_PYTHON
echo [ERROR] Python environment not found.
echo [INFO] Run install_dependencies.bat in this folder first.
echo [INFO] Expected: %CD%\.venv\Scripts\python.exe
if "%VIRALCUTTER_NO_PAUSE%"=="1" exit /b 1
pause
exit /b 1
