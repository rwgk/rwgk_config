# copy Microsoft.PowerShell_profile.ps1 "$PROFILE"
$uh = "\\wsl$\Ubuntu\home\$env:USERNAME"
$wintermsettings = "$env:LOCALAPPDATA\Packages\Microsoft.WindowsTerminal_8wekyb3d8bbwe\LocalState\settings.json"
$env:Path += ";C:\Program Files\Vim\vim91"
function gitbash { & "C:\Program Files\Git\bin\bash.exe" -l }
function mf3path { & "$env:USERPROFILE\AppData\Local\miniforge3\shell\condabin\conda-hook.ps1" }

function Set-CudaEnv {
    param(
        [string]$Version
    )

    $cudaBasePath = "C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA"

    # Check if CUDA directory exists
    if (-not (Test-Path $cudaBasePath)) {
        Write-Error "CUDA installation not found at: $cudaBasePath"
        return
    }

    # Get all version directories (starting with 'v')
    $versionDirs = Get-ChildItem -Path $cudaBasePath -Directory | Where-Object { $_.Name -match '^v\d+\.\d+$' }

    if ($versionDirs.Count -eq 0) {
        Write-Error "No CUDA versions found in: $cudaBasePath"
        return
    }

    # If version not specified, try to auto-detect
    if (-not $Version) {
        if ($versionDirs.Count -eq 1) {
            $Version = $versionDirs[0].Name
            Write-Host "Auto-detected CUDA version: $Version" -ForegroundColor Green
        } else {
            Write-Host "Multiple CUDA versions found:" -ForegroundColor Yellow
            $versionDirs | ForEach-Object { Write-Host "  $($_.Name)" }
            Write-Error "Please specify a version using -Version parameter"
            return
        }
    }

    # Validate the specified version exists
    $targetPath = Join-Path $cudaBasePath $Version
    if (-not (Test-Path $targetPath)) {
        Write-Host "Available versions:" -ForegroundColor Yellow
        $versionDirs | ForEach-Object { Write-Host "  $($_.Name)" }
        Write-Error "CUDA version '$Version' not found"
        return
    }

    # Set environment variables
    $env:CUDA_HOME = $targetPath
    $env:LIB = "$env:CUDA_HOME\lib\x64;$env:LIB"

    Write-Host "CUDA environment set:" -ForegroundColor Green
    Write-Host "  CUDA_HOME = $env:CUDA_HOME"
    Write-Host "  LIB = $env:LIB"
}
