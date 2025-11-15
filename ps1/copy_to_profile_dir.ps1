# **********************************************
# REMINDER: Run-Bypass .\copy_to_profile_dir.ps1
# **********************************************

# Copies Microsoft.PowerShell_profile.ps1 and PSReadLine-History-Config.ps1
# from this folder into the user's current-host PowerShell profile directory,
# clobbering existing files. Automatically creates the profile directory and
# profile file if they do not exist.

$ErrorActionPreference = 'Stop'

# Destination: ensure profile path and directory exist
$destProfile = $PROFILE
if (-not $destProfile) {
    throw "`$PROFILE is not set. Cannot determine destination profile path."
}

$destDir = Split-Path -Path $destProfile -Parent
if (-not (Test-Path -LiteralPath $destDir -PathType Container)) {
    Write-Host "Creating PowerShell profile directory: $destDir"
    New-Item -ItemType Directory -Path $destDir -Force | Out-Null
}

if (-not (Test-Path -LiteralPath $destProfile -PathType Leaf)) {
    Write-Host "Creating empty profile file: $destProfile"
    New-Item -ItemType File -Path $destProfile -Force | Out-Null
}

# Source directory: where this script lives
$srcDir = $PSScriptRoot
$files = @('Microsoft.PowerShell_profile.ps1', 'PSReadLine-History-Config.ps1')

# Verify sources exist
foreach ($name in $files) {
    $src = Join-Path $srcDir $name
    if (-not (Test-Path -LiteralPath $src -PathType Leaf)) {
        throw "Source file not found: `"$src`""
    }
}

# Copy (clobber)
foreach ($name in $files) {
    $src = Join-Path $srcDir $name
    Copy-Item -LiteralPath $src -Destination $destDir -Force
}

Write-Host "Copied: $($files -join ', ')" -ForegroundColor Green
Write-Host "Destination: $destDir" -ForegroundColor Green
