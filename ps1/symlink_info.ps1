# 1) Read Developer Mode registry (both hives)
$devMode = $false
$devReg = @()
$devKeys = @(
    'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\AppModelUnlock',
    'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\AppModelUnlock'
)
foreach ($k in $devKeys) {
    if (Test-Path $k) {
        try {
            $val = Get-ItemProperty -Path $k -ErrorAction Stop
            $devReg += @{ Path = $k; AllowDevelopmentWithoutDevLicense = $val.AllowDevelopmentWithoutDevLicense; AllowAllTrustedApps = $val.AllowAllTrustedApps }
            if ($val.AllowDevelopmentWithoutDevLicense -eq 1) { $devMode = $true }
        }
        catch {}
    }
    else {
        $devReg += @{ Path = $k; Missing = $true }
    }
}

# 2) Try to export local security policy to see who has SeCreateSymbolicLinkPrivilege
$principals = @()
$secpolPath = Join-Path $env:TEMP "secpol.cfg"
$secpolOk = $false
try {
    secedit /export /cfg $secpolPath | Out-Null
    if (Test-Path $secpolPath) {
        $line = Select-String -Path $secpolPath -Pattern '^SeCreateSymbolicLinkPrivilege' -ErrorAction SilentlyContinue
        if ($line) {
            $sids = ($line -split '=', 2)[1].Trim() -split ','
            foreach ($sid in $sids) {
                $s = $sid.Trim().Trim('*')
                try {
                    $principals += ([System.Security.Principal.SecurityIdentifier]$s).Translate([System.Security.Principal.NTAccount]).Value
                }
                catch {
                    $principals += $s
                }
            }
        }
        $secpolOk = $true
    }
}
catch {}

# 3) Check if current user belongs to any of those principals
$user = [System.Security.Principal.WindowsIdentity]::GetCurrent()
$inPrivGroup = $false
if ($principals.Count -gt 0) {
    $userGroups = @()
    if ($user.Groups) {
        foreach ($g in $user.Groups) {
            try { $userGroups += $g.Translate([System.Security.Principal.NTAccount]).Value } catch {}
        }
    }
    foreach ($p in $principals) {
        if ($userGroups -contains $p -or $user.Name -eq $p) { $inPrivGroup = $true; break }
    }
}

# 4) Real capability test: attempt a temp file symlink (no admin)
$capTest = "Unknown"
$testDir = $env:TEMP
$src = Join-Path $testDir "symlink_src_$(Get-Random).txt"
$dst = Join-Path $testDir "symlink_dst_$(Get-Random).txt"
try {
    Set-Content -Path $src -Value "ping" -ErrorAction Stop
    New-Item -ItemType SymbolicLink -Path $dst -Target $src -ErrorAction Stop | Out-Null
    # success
    $capTest = "YES"
}
catch {
    $capTest = "NO (`$($_.Exception.Message)`) "
}
finally {
    Remove-Item -LiteralPath $dst -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $src -Force -ErrorAction SilentlyContinue
}

# 5) Output
Write-Output "Developer Mode (registry): " + ($(if ($devMode) { "ON" } else { "OFF" }))
foreach ($entry in $devReg) {
    if ($entry.Missing) { Write-Output "  $($entry.Path): (key not present)" }
    else {
        Write-Output ("  {0}: AllowDevelopmentWithoutDevLicense={1}, AllowAllTrustedApps={2}" -f `
                $entry.Path, $entry.AllowDevelopmentWithoutDevLicense, $entry.AllowAllTrustedApps)
    }
}
if ($secpolOk) {
    if ($principals.Count -gt 0) {
        Write-Output "SeCreateSymbolicLinkPrivilege is granted to: $($principals -join ', ')"
    }
    else {
        Write-Output "SeCreateSymbolicLinkPrivilege: (no principals found; usually Administrators only)"
    }
}
else {
    Write-Output "Note: Could not export local security policy (secedit unavailable or blocked)."
}
Write-Output ("User is in a privileged group: " + ($(if ($inPrivGroup) { "YES" } else { "NO" })))
Write-Output ("==> Non-admin symlink creation works here: " + $capTest)
