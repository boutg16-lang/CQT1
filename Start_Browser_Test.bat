@echo off
setlocal
cd /d "%~dp0"
title OUSSAMA Cutter - Local Browser Test

set "PYTHON=%~dp0.venv\Scripts\python.exe"
if not exist "%PYTHON%" (
    echo Run install_dependencies.bat first to create the project environment.
    pause
    exit /b 1
)

if not defined CAT_BROWSER_BRIDGE_URL set "CAT_BROWSER_BRIDGE_URL=https://cat-browser-bridge-2nnhh.zocomputer.io"
set "UV=uv"
where uv >nul 2>&1
if errorlevel 1 (
    if exist "%USERPROFILE%\.local\bin\uv.exe" (
        set "UV=%USERPROFILE%\.local\bin\uv.exe"
    ) else (
        echo uv was not found. Run install_dependencies.bat first.
        pause
        exit /b 1
    )
)

"%UV%" pip install --python "%PYTHON%" -r "%~dp0requirements-browser-bridge.txt"
if errorlevel 1 goto FAILED

"%PYTHON%" -m playwright install chromium
if errorlevel 1 goto FAILED

"%PYTHON%" -m tools.browser_bridge.client --relay-url "%CAT_BROWSER_BRIDGE_URL%"
if errorlevel 1 goto FAILED
exit /b 0

:FAILED
echo Browser test bridge failed. Read the message above; no project files were changed by the bridge installer.
pause
exit /b 1
