# copy Microsoft.PowerShell_profile.ps1 "$PROFILE"
$uh = "\\wsl$\Ubuntu\home\$env:USERNAME"
$env:Path += ";C:\Program Files\Vim\vim91"
function gitbash { & "C:\Program Files\Git\bin\bash.exe" -l }
function mf3path { & "$env:USERPROFILE\AppData\Local\miniforge3\shell\condabin\conda-hook.ps1" }
