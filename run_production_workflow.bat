@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] Python virtual environment not found: .venv\Scripts\python.exe
  echo Create the project environment first, then install project dependencies.
  exit /b 2
)
if "%~1"=="" (
  echo Usage: run_production_workflow.bat segments.json [additional options]
  echo Example: run_production_workflow.bat segments.json --output VIRALS\production_report.json --limit 8
  exit /b 2
)
.venv\Scripts\python.exe -m scripts.production_workflow %*
exit /b %errorlevel%
