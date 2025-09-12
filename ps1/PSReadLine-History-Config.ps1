<#
PSReadLine persistent history + timestamped sidecar (OneDrive-friendly)
============================================================================

What this file does
-------------------
1) Centralizes PowerShell command history for this workstation in a single file
   (`$HistoryFile`) under OneDrive → Documents → WindowsPowerShell → History.
   - PSReadLine is configured with:
       * -HistorySavePath      → the central file
       * -HistorySaveStyle     SaveIncrementally  (append on every Enter)
       * -HistoryNoDuplicates  $true (skip consecutive duplicates in PSReadLine’s file)
   - New shells automatically preload from this file, so up-arrow / Ctrl+R see past
     commands from earlier sessions. (Note: Get-History still shows *session-only*.)

2) Writes a *separate* timestamped sidecar log (`$HistoryTsLog`) **only** for commands
   you actually execute. This is implemented by rebinding Enter to:
       a) append “ISO-8601 timestamp :: command” to `$HistoryTsLog`
       b) then execute the line

3) Keeps obvious secrets out of both histories using a single helper regex
   (see `Test-PSRLSafeLine`). Adjust the pattern here if you want to broaden or
   relax the filter.

4) Chooses a OneDrive path when available, otherwise falls back to your local
   Documents folder. The History directory/file are created if missing, but are
   never truncated on shell startup.

OneDrive + “Always keep on this device”
---------------------------------------
OneDrive keeps your profile and history backed up. For fast and reliable appends,
ensure the History folder is stored locally (not cloud-only):

    File Explorer → navigate to:
      OneDrive - <Your Org>\Documents\WindowsPowerShell\
    Right-click the “History” folder → OneDrive → **Always keep on this device**

This prevents cloud placeholders from causing latency or write hiccups.

How to include this file from your profile
------------------------------------------
Place this file next to your Windows PowerShell 5.1 profile and dot-source it:

    $PROFILE  →  C:\Users\<you>\OneDrive - <Your Org>\Documents\WindowsPowerShell\Microsoft.PowerShell_profile.ps1

Add this line to your profile (once):

    . "$PSScriptRoot\PSReadLine-History-Config.ps1"

(If you prefer, use an absolute path. PowerShell 5.1 loads PSReadLine by default.)

Everyday behavior & tips
------------------------
- Up-arrow: walk through preloaded history.
- Ctrl+R: reverse-search the whole preloaded history (Ctrl+R again = older match).
- Get-History: shows *current session only* (by PowerShell design).
- The timestamped sidecar grows only when you press Enter in that shell.
- To allow duplicates in PSReadLine’s file, change:
      Set-PSReadLineOption -HistoryNoDuplicates:$false

Troubleshooting
---------------
- Nothing appears in the timestamp log:
    * Confirm the Enter binding is active:
        Get-PSReadLineKeyHandler | ? Key -eq Enter
      It should report your custom “AcceptLineAndLog” handler.
    * Verify $HistoryTsLog points to a writable file.
    * Ensure the History folder is marked “Always keep on this device”.
- Preload works but Get-History looks empty:
    * Expected. Get-History is session-scoped; use Ctrl+R or tail the file.

Portability
-----------
- Works on Windows PowerShell 5.1. Also works on PowerShell 7; if you want a single,
  shared history across both, point both profiles to the same `$HistoryFile` and
  reuse this file.

Security note
-------------
The secret filter is only a heuristic. Avoid running commands that embed secrets in
plain text whenever possible (use secure prompts / environment variables).

#>

if ($script:__PSRLHistoryInit) { return }
$script:__PSRLHistoryInit = $true

if (-not (Get-Module PSReadLine)) { try { Import-Module PSReadLine -ErrorAction Stop } catch { } }

$oneDriveRoot = $env:OneDriveCommercial
if (-not $oneDriveRoot) { $oneDriveRoot = $env:OneDrive }

if ($oneDriveRoot) {
    $HistoryDir = Join-Path $oneDriveRoot 'Documents\WindowsPowerShell\History'
}
else {
    $docs = [Environment]::GetFolderPath('MyDocuments')
    $HistoryDir = Join-Path $docs 'WindowsPowerShell\History'
}

$HistoryFile = Join-Path $HistoryDir "psrl_history.txt"
$HistoryTsLog = Join-Path $HistoryDir "history_tslog.txt"

if (-not (Test-Path $HistoryDir -PathType Container)) { New-Item -ItemType Directory -Path $HistoryDir | Out-Null }
if (-not (Test-Path $HistoryFile -PathType Leaf)) { New-Item -ItemType File -Path $HistoryFile | Out-Null }
if (-not (Test-Path $HistoryTsLog -PathType Leaf)) { New-Item -ItemType File -Path $HistoryTsLog | Out-Null }

Set-PSReadLineOption -HistorySavePath $HistoryFile
Set-PSReadLineOption -HistorySaveStyle SaveIncrementally
Set-PSReadLineOption -MaximumHistoryCount 200000
Set-PSReadLineOption -HistoryNoDuplicates:$true

$script:PSRL_SecretRegex = New-Object System.Text.RegularExpressions.Regex(
    '\b(pass(word)?|secret|token|apikey|sas|connection(-)?string)\b',
    [System.Text.RegularExpressions.RegexOptions]::IgnoreCase
)

function Test-PSRLSafeLine {
    param([string]$Line)
    # For PSReadLine filtering we only exclude obvious secrets;
    # blank lines are fine (PSReadLine can decide whether to store them).
    if ($null -eq $Line) { return $true }
    return -not $script:PSRL_SecretRegex.IsMatch($Line)
}

Set-PSReadLineOption -AddToHistoryHandler {
    param($line)
    Test-PSRLSafeLine $line
}

# Enter = log to sidecar, then execute the line
Set-PSReadLineKeyHandler -Key Enter `
    -BriefDescription "AcceptLineAndLog" `
    -LongDescription  "Append command to timestamped sidecar log, then execute" `
    -ScriptBlock {
    param($key, $arg)

    # Read the current buffer
    $line = $null; $cursor = $null
    [Microsoft.PowerShell.PSConsoleReadLine]::GetBufferState([ref]$line, [ref]$cursor)

    # Log non-empty, non-secret-ish lines
    if (-not [string]::IsNullOrWhiteSpace($line) -and (Test-PSRLSafeLine $line)) {
        try {
            $ts = Get-Date -Format o
            $entry = "{0} :: {1}" -f $ts, $line
            $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
            [System.IO.File]::AppendAllText($global:HistoryTsLog, ($entry + [Environment]::NewLine), $utf8NoBom)
        }
        catch { }
    }

    # Hand the line off to PSReadLine to actually run
    [Microsoft.PowerShell.PSConsoleReadLine]::AcceptLine()
}

function histsync {
    if (-not (Get-Module PSReadLine)) { try { Import-Module PSReadLine -ErrorAction Stop } catch { return } }
    $path = (Get-PSReadLineOption).HistorySavePath
    if (-not $path -or -not (Test-Path $path -PathType Leaf)) { return }
    $import = Get-Command Import-PSReadLineHistory -ErrorAction SilentlyContinue
    if ($import) {
        Import-PSReadLineHistory -Path $path -ErrorAction SilentlyContinue
    }
    else {
        foreach ($l in (Get-Content -Path $path -ErrorAction SilentlyContinue)) {
            if (-not [string]::IsNullOrWhiteSpace($l)) {
                [Microsoft.PowerShell.PSConsoleReadLine]::AddToHistory($l)
            }
        }
    }
}

function histtail {
    param([Alias('n', 'count')][ValidateRange(1, 1000000)][int]$Last = 10)
    $p = (Get-PSReadLineOption).HistorySavePath
    if (-not $p -or -not (Test-Path $p -PathType Leaf)) { Write-Host "No PSReadLine history file." -f Yellow; return }
    Get-Content -Path $p -Tail $Last
}

function histtailts {
    param([Alias('n', 'count')][ValidateRange(1, 1000000)][int]$Last = 10)
    if (-not (Test-Path $HistoryTsLog -PathType Leaf)) { Write-Host "No timestamped log found." -f Yellow; return }
    Get-Content -Path $HistoryTsLog -Tail $Last
}

function histgrep {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Pattern)
    if (-not $Pattern) { Write-Host "Usage: histgrep <pattern>"; return }
    Get-Content (Get-PSReadLineOption).HistorySavePath | Select-String -SimpleMatch ($Pattern -join " ")
}
