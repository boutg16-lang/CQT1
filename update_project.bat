@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title OUSSAMA Cutter - Update

set "PYTHON=%CD%\.venv\Scripts\python.exe"
if not exist "%PYTHON%" (
    echo [ERROR] Python environment not found.
    echo [INFO] Run install_dependencies.bat first.
    if "%VIRALCUTTER_NO_PAUSE%"=="1" exit /b 1
    pause
    exit /b 1
)

if exist "bin\ffmpeg.exe" set "PATH=%CD%\bin;%PATH%"

set "MODE=%*"
if "%MODE%"=="" set "MODE=--apply"

echo [update] %MODE%
"%PYTHON%" -m scripts.source_updater %MODE%
set "UPDATE_EXIT=%ERRORLEVEL%"

echo.
if "%UPDATE_EXIT%"=="0" (
    echo [update] Done. Start the app with: run_webui.bat
) else if "%UPDATE_EXIT%"=="2" (
    echo [update] Nothing applied. Read the message above.
) else if "%UPDATE_EXIT%"=="3" (
    echo [update] An update is available. Run: update_project.bat --apply
) else (
    echo [update] Update failed with code %UPDATE_EXIT%.
)

if "%VIRALCUTTER_NO_PAUSE%"=="1" exit /b %UPDATE_EXIT%
pause
exit /b %UPDATE_EXIT%
