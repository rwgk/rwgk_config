# Copies Microsoft.PowerShell_profile.ps1 and PSReadLine-History-Config.ps1
# from this folder into the user's current-host PowerShell profile directory,
# clobbering existing files. Errors if $PROFILE or its directory don't exist.

# This may be needed to enable running this script:
#     Set-ExecutionPolicy -Scope Process Bypass -Force

# If this script errors because the profile file doesnâ€™t exist yet,
# create it once and rerun:
#     ni -Force -ItemType File $PROFILE

$ErrorActionPreference = 'Stop'

# Destination: must already exist
$destProfile = $PROFILE
if (-not (Test-Path -LiteralPath $destProfile -PathType Leaf)) {
    throw "Profile file does not exist: `"$destProfile`". Create it once (e.g., `ni -Force -ItemType File $PROFILE`) then rerun."
}
$destDir = Split-Path -Path $destProfile -Parent
if (-not (Test-Path -LiteralPath $destDir -PathType Container)) {
    throw "Profile directory does not exist: `"$destDir`"."
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
