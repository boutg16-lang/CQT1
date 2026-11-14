# Remove half-written *.dist-info folders that have no METADATA file.
#
# A partial uv/pip install can leave e.g.
#   .venv\Lib\site-packages\charset_normalizer-3.5.2.dist-info\
# without METADATA. uv then aborts with "Failed to read metadata" before it
# can reinstall anything. Deleting that folder makes the package look
# uninstalled, so the next install rewrites it cleanly.
#
# Usage:
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\repair_venv_metadata.ps1 -PythonPath .venv\Scripts\python.exe
#   ... -DryRun    # report only

param(
    [Parameter(Mandatory = $true)][string]$PythonPath,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $PythonPath)) {
    Write-Host "[repair] Python not found: $PythonPath"
    exit 2
}

try {
    $SitePackages = & $PythonPath -c "import sysconfig; print(sysconfig.get_paths()['purelib'])"
} catch {
    Write-Host "[repair] Could not resolve site-packages: $($_.Exception.Message)"
    exit 2
}

if (-not $SitePackages -or -not (Test-Path -LiteralPath $SitePackages)) {
    Write-Host "[repair] site-packages not found: $SitePackages"
    exit 2
}

$Removed = 0
$Failed = 0
Get-ChildItem -LiteralPath $SitePackages -Directory -Filter "*.dist-info" -ErrorAction SilentlyContinue |
    ForEach-Object {
        $Metadata = Join-Path $_.FullName "METADATA"
        $Broken = $true
        if (Test-Path -LiteralPath $Metadata) {
            try {
                $Broken = ((Get-Item -LiteralPath $Metadata).Length -le 0)
            } catch {
                $Broken = $true
            }
        }
        if (-not $Broken) { return }

        if ($DryRun) {
            Write-Host "[repair] would remove incomplete $($_.Name)"
            $Removed++
            return
        }
        try {
            Remove-Item -LiteralPath $_.FullName -Recurse -Force
            Write-Host "[repair] removed incomplete $($_.Name)"
            $Removed++
        } catch {
            Write-Host "[repair] could not remove $($_.Name): $($_.Exception.Message)"
            $Failed++
        }
    }

if ($Removed -eq 0 -and $Failed -eq 0) {
    Write-Host "[repair] venv metadata OK."
} else {
    Write-Host "[repair] incomplete metadata folders: $Removed removed, $Failed failed."
}
exit 0
