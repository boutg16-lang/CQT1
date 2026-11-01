param(
    [Parameter(Mandatory = $true)]
    [string]$PythonPath
)

$ErrorActionPreference = "Stop"

try {
    $targetPath = [System.IO.Path]::GetFullPath($PythonPath)
    $scriptsDirectory = [System.IO.Path]::GetDirectoryName($targetPath)
    $venvRoot = [System.IO.Path]::GetDirectoryName($scriptsDirectory)
    $venvPrefix = $venvRoot.TrimEnd([System.IO.Path]::DirectorySeparatorChar) + [System.IO.Path]::DirectorySeparatorChar
    $runningProcesses = @(
        Get-CimInstance -ClassName Win32_Process -ErrorAction Stop | Where-Object {
            if (-not $_.ExecutablePath) {
                return $false
            }
            $processPath = [System.IO.Path]::GetFullPath($_.ExecutablePath)
            $processPath.Equals($targetPath, [System.StringComparison]::OrdinalIgnoreCase)
        }
    )
    $pythonProcesses = @(Get-Process -Name python,pythonw -ErrorAction SilentlyContinue)
    $moduleUsers = @()
    $moduleInspectionFailed = $false
    foreach ($process in $pythonProcesses) {
        try {
            $usesVenv = @($process.Modules | Where-Object {
                $_.FileName -and [System.IO.Path]::GetFullPath($_.FileName).StartsWith(
                    $venvPrefix,
                    [System.StringComparison]::OrdinalIgnoreCase
                )
            }).Count -gt 0
            if ($usesVenv) {
                $moduleUsers += $process.Id
            }
        } catch {
            $moduleInspectionFailed = $true
        }
    }
} catch {
    Write-Host "[ERROR] Could not check whether this project's Python environment is in use."
    Write-Host "[INFO] Close OUSSAMA Cutter and retry setup."
    exit 3
}

if ($moduleInspectionFailed) {
    Write-Host "[ERROR] Could not inspect every Python process for modules loaded from this project's .venv."
    Write-Host "[INFO] Close all Python and OUSSAMA Cutter processes, then retry setup."
    exit 3
}

if ($runningProcesses.Count -gt 0 -or $moduleUsers.Count -gt 0) {
    Write-Host "[ERROR] A process is using modules from this project's Python environment."
    Write-Host "[INFO] Close OUSSAMA Cutter, WebUI, and Python tasks using this project's .venv, then retry setup."
    exit 2
}

exit 0
