#Requires AutoHotkey v2.0

; copy rwgk_hotkeys.ahk "$env:USERPROFILE\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup"

; Alt + F7 → EM DASH
!F7::SendText "—"

; Alt + F8 → RIGHTWARDS ARROW
!F8::SendText "→"

; F12 acts like holding the Win key
F12::
{
    Send("{LWin Down}")
}

F12 Up::
{
    Send("{LWin Up}")
}
