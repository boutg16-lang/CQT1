param(
    [Parameter(Mandatory = $true)]
    [string]$PythonPath
)

$ErrorActionPreference = "Stop"

try {
    $targetPath = [System.IO.Path]::GetFullPath($PythonPath)
    $runningProcesses = @(
        Get-CimInstance -ClassName Win32_Process -ErrorAction Stop | Where-Object {
            if (-not $_.ExecutablePath) {
                return $false
            }
            $processPath = [System.IO.Path]::GetFullPath($_.ExecutablePath)
            $processPath.Equals($targetPath, [System.StringComparison]::OrdinalIgnoreCase)
        }
    )
} catch {
    Write-Host "[ERROR] Could not check whether this project's Python environment is in use."
    Write-Host "[INFO] Close OUSSAMA Cutter and retry setup."
    exit 3
}

if ($runningProcesses.Count -gt 0) {
    Write-Host "[ERROR] This project's Python environment is currently in use."
    Write-Host "[INFO] Close OUSSAMA Cutter, its WebUI, and Python tasks started from this project, then retry setup."
    exit 2
}

exit 0
