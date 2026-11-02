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
                $moduleUsers += [PSCustomObject]@{
                    ProcessId = $process.Id
                    ProcessName = $process.ProcessName
                }
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

$environmentUsers = @()
$reportedProcessIds = @{}
foreach ($process in $runningProcesses) {
    $processId = [string]$process.ProcessId
    if (-not $reportedProcessIds.ContainsKey($processId)) {
        $environmentUsers += [PSCustomObject]@{
            ProcessId = $process.ProcessId
            ProcessName = $process.Name
            Reason = "running this project's Python interpreter"
        }
        $reportedProcessIds[$processId] = $true
    }
}
foreach ($process in $moduleUsers) {
    $processId = [string]$process.ProcessId
    if (-not $reportedProcessIds.ContainsKey($processId)) {
        $environmentUsers += [PSCustomObject]@{
            ProcessId = $process.ProcessId
            ProcessName = $process.ProcessName
            Reason = "using modules from this project's .venv"
        }
        $reportedProcessIds[$processId] = $true
    }
}

if ($environmentUsers.Count -gt 0) {
    Write-Host "[ERROR] A process is using this project's Python environment."
    foreach ($process in $environmentUsers) {
        Write-Host ("[INFO] PID {0}: {1} is {2}." -f $process.ProcessId, $process.ProcessName, $process.Reason)
    }
    if ($moduleInspectionFailed) {
        Write-Host "[WARNING] Some other Python processes could not be inspected; the listed PIDs are confirmed."
    }
    Write-Host "[INFO] Close the listed app or Python process in Task Manager, then retry setup."
    exit 2
}

if ($moduleInspectionFailed) {
    Write-Host "[ERROR] Could not inspect every Python process for modules loaded from this project's .venv."
    Write-Host "[INFO] Close OUSSAMA Cutter and Python processes, then retry setup."
    exit 3
}

exit 0
