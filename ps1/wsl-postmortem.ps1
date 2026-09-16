# wsl-postmortem.ps1
#
# Collect Windows-side diagnostic information about a broken WSL state
# WITHOUT invoking wsl.exe.
#
# Best run immediately after noticing that WSL sessions have died,
# before:
#   - starting another WSL session
#   - running `wsl --shutdown`
#   - rebooting Windows
#
# Running PowerShell as Administrator is preferable, but not required.

$timestamp = Get-Date -Format "yyyy-MM-dd_HH-mm-ss"
$outdir = Join-Path $env:USERPROFILE "Desktop\wsl-postmortem-$timestamp"

New-Item -ItemType Directory -Force -Path $outdir | Out-Null

function Capture-Text {
    param(
        [string]$Name,
        [scriptblock]$Command
    )

    $path = Join-Path $outdir $Name

    try {
        & $Command 2>&1 |
            Out-String -Width 300 |
            Set-Content -Encoding UTF8 $path
    }
    catch {
        "ERROR: $_" | Set-Content -Encoding UTF8 $path
    }
}

"WSL postmortem collected: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss K')" |
    Set-Content -Encoding UTF8 (Join-Path $outdir "README.txt")


# ----------------------------------------------------------------------
# Basic Windows state
# ----------------------------------------------------------------------

Capture-Text "windows.txt" {
    Get-CimInstance Win32_OperatingSystem |
        Format-List Caption, Version, BuildNumber, LastBootUpTime,
        LocalDateTime, OSArchitecture
}

Capture-Text "uptime.txt" {
    $os = Get-CimInstance Win32_OperatingSystem
    $uptime = (Get-Date) - $os.LastBootUpTime

    [pscustomobject]@{
        Now         = Get-Date
        LastBoot    = $os.LastBootUpTime
        Uptime      = $uptime
        UptimeHours = [math]::Round($uptime.TotalHours, 2)
    } | Format-List
}

Capture-Text "powercfg-a.txt" {
    powercfg.exe /a
}

Capture-Text "powercfg-lastwake.txt" {
    powercfg.exe /lastwake
}

Capture-Text "powercfg-waketimers.txt" {
    powercfg.exe /waketimers
}


# ----------------------------------------------------------------------
# Relevant processes
# ----------------------------------------------------------------------

Capture-Text "processes-wsl-hyperv.txt" {
    Get-Process |
        Where-Object {
            $_.ProcessName -match
            '^(wsl|wslservice|wslrelay|wslhost|vmmem|vmmemWSL|vmcompute|vmwp|hns)$'
        } |
        Sort-Object ProcessName, Id |
        Format-Table ProcessName, Id, StartTime, CPU,
        @{n = 'WorkingSetMB'; e = { [math]::Round($_.WorkingSet64 / 1MB, 1) } },
        Path -AutoSize
}

Capture-Text "all-processes.txt" {
    Get-Process |
        Sort-Object ProcessName, Id |
        Format-Table ProcessName, Id, StartTime, CPU,
        @{n = 'WorkingSetMB'; e = { [math]::Round($_.WorkingSet64 / 1MB, 1) } } `
            -AutoSize
}


# ----------------------------------------------------------------------
# Services
# ----------------------------------------------------------------------

Capture-Text "services-wsl-hyperv.txt" {
    Get-Service |
        Where-Object {
            $_.Name -match 'wsl|lxss|vmcompute|hns|hvhost|vmms'
            -or
            $_.DisplayName -match 'WSL|Linux|Hyper-V|Host Network'
        } |
        Sort-Object Name |
        Format-Table Status, StartType, Name, DisplayName -AutoSize
}


# ----------------------------------------------------------------------
# Networking
# ----------------------------------------------------------------------

Capture-Text "network-adapters.txt" {
    Get-NetAdapter -IncludeHidden |
        Sort-Object Name |
        Format-Table Name, InterfaceDescription, Status,
        MacAddress, LinkSpeed, ifIndex -AutoSize
}

Capture-Text "network-ipconfig.txt" {
    ipconfig.exe /all
}

Capture-Text "network-routes.txt" {
    Get-NetRoute |
        Sort-Object InterfaceIndex, DestinationPrefix |
        Format-Table InterfaceIndex, DestinationPrefix,
        NextHop, RouteMetric, State -AutoSize
}


# ----------------------------------------------------------------------
# Recent Windows event logs
#
# Collect the last 3 days.  We intentionally discover matching logs rather
# than assuming that every Windows/WSL version exposes exactly the same set.
# ----------------------------------------------------------------------

$since = (Get-Date).AddDays(-3)

$interestingLogPatterns = @(
    '*WSL*',
    '*Lxss*',
    '*Hyper-V*',
    '*Host-Network-Service*',
    '*HNS*'
)

$logs = Get-WinEvent -ListLog * -ErrorAction SilentlyContinue |
    Where-Object {
        $name = $_.LogName
        ($interestingLogPatterns | Where-Object { $name -like $_ }).Count -gt 0
    } |
    Sort-Object LogName -Unique

$logs |
    Select-Object LogName, RecordCount, IsEnabled, LogMode, MaximumSizeInBytes |
    Format-Table -AutoSize |
    Out-String -Width 300 |
    Set-Content -Encoding UTF8 (Join-Path $outdir "eventlogs-found.txt")

foreach ($log in $logs) {

    $safeName = $log.LogName -replace '[\\/:*?"<>|]', '_'
    $path = Join-Path $outdir "events-$safeName.txt"

    try {
        Get-WinEvent -FilterHashtable @{
            LogName   = $log.LogName
            StartTime = $since
        } -ErrorAction Stop |
            Select-Object TimeCreated, Id, LevelDisplayName,
            ProviderName, ProcessId, ThreadId, Message |
            Format-List |
            Out-String -Width 300 |
            Set-Content -Encoding UTF8 $path
    }
    catch {
        "Could not read log $($log.LogName): $_" |
            Set-Content -Encoding UTF8 $path
    }
}


# ----------------------------------------------------------------------
# Power / sleep / resume events from the System log
# ----------------------------------------------------------------------

Capture-Text "events-power.txt" {
    Get-WinEvent -FilterHashtable @{
        LogName   = 'System'
        StartTime = $since
    } -ErrorAction SilentlyContinue |
        Where-Object {
            $_.ProviderName -match
            'Kernel-Power|Power-Troubleshooter|Kernel-General'
        } |
        Select-Object TimeCreated, Id, LevelDisplayName,
        ProviderName, Message |
        Format-List
}


# ----------------------------------------------------------------------
# Hyper-V / WSL-related errors in System log
# ----------------------------------------------------------------------

Capture-Text "events-system-errors.txt" {
    Get-WinEvent -FilterHashtable @{
        LogName   = 'System'
        StartTime = $since
    } -ErrorAction SilentlyContinue |
        Where-Object {
            ($_.LevelDisplayName -in @('Critical', 'Error', 'Warning')) -and
            (
                ($_.ProviderName -match 'Hyper-V|HvHost|HNS|Host.Network|Lxss|WSL|Virtual') -or
                ($_.Message -match 'WSL|Linux|Hyper-V|virtual machine|hvsocket|VMBus')
            )
        } |
        Select-Object TimeCreated, Id, LevelDisplayName,
        ProviderName, Message |
        Format-List
}


# ----------------------------------------------------------------------
# Optional crash artifacts already present on Windows
# ----------------------------------------------------------------------

$crashDir = Join-Path $env:TEMP "wsl-crashes"

Capture-Text "wsl-crashes-directory.txt" {
    if (Test-Path $crashDir) {
        Get-ChildItem -Force -Recurse $crashDir |
            Select-Object FullName, Length, CreationTime, LastWriteTime |
            Format-Table -AutoSize
    }
    else {
        "No $crashDir directory exists."
    }
}


# ----------------------------------------------------------------------
# Bundle it up
# ----------------------------------------------------------------------

$zip = "$outdir.zip"

Compress-Archive -Path "$outdir\*" -DestinationPath $zip -Force

Write-Host
Write-Host "WSL postmortem complete." -ForegroundColor Green
Write-Host
Write-Host "Directory:"
Write-Host "  $outdir"
Write-Host
Write-Host "ZIP:"
Write-Host "  $zip"
Write-Host
Write-Host "No wsl.exe command was executed by this script."
