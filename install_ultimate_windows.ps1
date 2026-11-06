param(
    [ValidateSet('cpu', 'gpu')]
    [string]$Transcription = 'cpu'
)

$ErrorActionPreference = 'Stop'
Write-Host 'OUSSAMA CUTTER 8.0 ULTIMATE — Windows setup' -ForegroundColor Cyan

if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
    throw 'Install Python 3.12 x64 first.'
}
& py -3.12 --version
if ($LASTEXITCODE -ne 0) {
    throw 'Python 3.12 is required (supported project range: 3.10–3.12).'
}

if (-not (Test-Path '.venv')) {
    & py -3.12 -m venv .venv
}
$python = Join-Path (Get-Location) '.venv\Scripts\python.exe'
& $python -m pip install --upgrade pip setuptools wheel

if ($Transcription -eq 'gpu') {
    Write-Host 'Installing PyTorch CUDA 12.6 runtime for RTX/NVIDIA.' -ForegroundColor Cyan
    & $python -m pip install torch==2.8.0 torchvision==0.23.0 torchaudio==2.8.0 --index-url https://download.pytorch.org/whl/cu126
} else {
    Write-Host 'Installing CPU PyTorch runtime.' -ForegroundColor Yellow
    & $python -m pip install torch==2.8.0 torchvision==0.23.0 torchaudio==2.8.0 --index-url https://download.pytorch.org/whl/cpu
}

& $python -m pip install -e '.[dev]'
& $python -m pip install -r requirements-transcribe.txt
& $python -m pip install -r requirements-upload.txt

Write-Host 'Install FFmpeg/FFprobe and Tesseract (ara/fra language packs) separately.' -ForegroundColor Yellow
Write-Host "Transcription mode: $Transcription"
& $python scripts\doctor.py
if ($LASTEXITCODE -ne 0) {
    throw 'Doctor checks reported a critical failure.'
}
