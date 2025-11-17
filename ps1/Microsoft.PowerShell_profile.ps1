# copy Microsoft.PowerShell_profile.ps1 "$PROFILE"
# also copy PSReadLine-History-Config.ps1 to the same directory

if (-not (Test-Path "U:\")) {
    cmd /c "subst U: \\wsl$\Ubuntu\home\$env:USERNAME"
}
$wintermsettings = "$env:LOCALAPPDATA\Packages\Microsoft.WindowsTerminal_8wekyb3d8bbwe\LocalState\settings.json"
$env:Path += ";C:\Program Files\Vim\vim91"
function gitbash { & "C:\Program Files\Git\bin\bash.exe" -l }
function mf3path { & "$env:USERPROFILE\AppData\Local\miniforge3\shell\condabin\conda-hook.ps1" }

$profileDir = Split-Path -Path $PROFILE -Parent
$psrlConfig = Join-Path $profileDir 'PSReadLine-History-Config.ps1'
if (Test-Path $psrlConfig) { . $psrlConfig }

function Run-Bypass {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Script,
        [string[]]$Args
    )

    & powershell -ExecutionPolicy Bypass -File $Script @Args
}

function ps1fmt {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory, Position = 0)]
        [ValidateNotNullOrEmpty()]
        [string]$Path,

        [switch]$Backup  # save file.ps1.bak before writing
    )

    if (-not (Get-Command Invoke-Formatter -ErrorAction SilentlyContinue)) {
        try { Import-Module PSScriptAnalyzer -ErrorAction Stop }
        catch { throw "Invoke-Formatter not found. Install PSScriptAnalyzer: Install-Module PSScriptAnalyzer -Scope CurrentUser" }
    }

    $full = Resolve-Path -LiteralPath $Path -ErrorAction Stop | Select-Object -ExpandProperty Path
    $src = Get-Content -LiteralPath $full -Raw -ErrorAction Stop

    try { $fmt = Invoke-Formatter -ScriptDefinition $src -ErrorAction Stop }
    catch { throw "Formatting failed for '$full': $($_.Exception.Message)" }

    # No change? Do nothing.
    if ($src -ceq $fmt) { return }

    if ($Backup) { Copy-Item -LiteralPath $full -Destination ($full + '.bak') -Force }

    # PowerShell 5.1: make encoding explicit
    Set-Content -LiteralPath $full -Value $fmt -Encoding UTF8
}

function Lock-Workstation {
    <#
    .SYNOPSIS
        Locks the current Windows session.

    .DESCRIPTION
        Invokes the Windows API call `LockWorkStation()` via rundll32.
        Useful in remote sessions (including RDP from macOS) where
        standard keyboard shortcuts like Win+L may not be passed through.
    #>

    rundll32.exe user32.dll, LockWorkStation
}

function Enable-Automatic-Driver-Updates {
    <#
    .SYNOPSIS
        Re-enables Windows automatic driver installation.

    .DESCRIPTION
        Restores the default Windows behavior for device driver updates by
        setting:
            HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\DriverSearching
                SearchOrderConfig = 1

        This command reverses the setting used to disable automatic driver
        installation when performing manual NVIDIA driver cleanup.
        After updating the registry, the function reads back the value and
        displays it for verification.

    .EXAMPLE
        Enable-Automatic-Driver-Updates

        Re-enables automatic driver updates immediately.
    #>

    $regPath = "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\DriverSearching"
    $name = "SearchOrderConfig"
    $value = 1

    try {
        Set-ItemProperty -Path $regPath -Name $name -Value $value -Force
        Write-Host "Automatic driver updates have been re-enabled." -ForegroundColor Green

        # Read back the current value
        $current = Get-ItemProperty -Path $regPath -Name $name | Select-Object -ExpandProperty $name
        Write-Host "Current value of SearchOrderConfig: $current" -ForegroundColor Cyan
    }
    catch {
        Write-Host "Failed to update driver search configuration:" -ForegroundColor Red
        Write-Host $_.Exception.Message
    }
}

function fresh_venv {
    param(
        [Parameter(Mandatory = $true)]
        [string]$VenvName
    )

    # Check if directory already exists
    if (Test-Path $VenvName) {
        Write-Error "fresh_venv: ERROR: directory '$VenvName' already exists. Please remove it first."
        return
    }

    # Create virtual environment
    try {
        Write-Host "Creating virtual environment: $VenvName"
        python -m venv $VenvName
        if ($LASTEXITCODE -ne 0) {
            throw "Python venv creation failed"
        }
    }
    catch {
        Write-Error "fresh_venv: ERROR: failed to create virtual environment."
        return
    }

    # Activate virtual environment
    $activateScript = Join-Path $VenvName "Scripts\Activate.ps1"
    if (Test-Path $activateScript) {
        & $activateScript
        Write-Host "Virtual environment activated: $VenvName" -ForegroundColor Green
    }
    else {
        Write-Error "fresh_venv: ERROR: activation script not found at $activateScript"
        return
    }

    # Upgrade pip
    Write-Host "Upgrading pip..."
    python -m pip install --upgrade pip

    # Install requirements if requirements.txt exists
    if (Test-Path "requirements.txt") {
        Write-Host "Installing dependencies from requirements.txt..."
        pip install -r requirements.txt
    }
    else {
        Write-Host "fresh_venv: NOTE: no requirements.txt found, skipping dependency installation." -ForegroundColor Yellow
    }
}

if (-not $env:CUDA_LOC) {
    $env:CUDA_LOC = "C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA"
}

function set_cuda_env {
    param(
        [string]$Version
    )

    # Check if CUDA directory exists
    if (-not (Test-Path $env:CUDA_LOC)) {
        Write-Error "CUDA installation not found at: $env:CUDA_LOC"
        return
    }

    # Get all version directories (starting with 'v')
    $versionDirs = Get-ChildItem -Path $env:CUDA_LOC -Directory | Where-Object { $_.Name -match '^v\d+\.\d+$' }

    if ($versionDirs.Count -eq 0) {
        Write-Error "No CUDA versions found in: $env:CUDA_LOC"
        return
    }

    # If version not specified, try to auto-detect
    if (-not $Version) {
        if ($versionDirs.Count -eq 1) {
            $Version = $versionDirs[0].Name
            Write-Host "Auto-detected CUDA version: $Version" -ForegroundColor Green
        }
        else {
            Write-Host "Multiple CUDA versions found:" -ForegroundColor Yellow
            $versionDirs | ForEach-Object { Write-Host "  $($_.Name)" }
            Write-Error "Please specify a version using -Version parameter"
            return
        }
    }

    # Validate the specified version exists
    $targetPath = Join-Path $env:CUDA_LOC $Version
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

function set_all_must_work {
    $env:CUDA_PATHFINDER_TEST_LOAD_NVIDIA_DYNAMIC_LIB_STRICTNESS = "all_must_work"
    Write-Host "  CUDA_PATHFINDER_TEST_LOAD_NVIDIA_DYNAMIC_LIB_STRICTNESS=all_must_work"
    $env:CUDA_PATHFINDER_TEST_FIND_NVIDIA_HEADERS_STRICTNESS = "all_must_work"
    Write-Host "  CUDA_PATHFINDER_TEST_FIND_NVIDIA_HEADERS_STRICTNESS=all_must_work"
}
