#Requires AutoHotkey v2.0

; winget install --id AutoHotkey.AutoHotkey --source winget
; copy rwgk_hotkeys.ahk "$env:USERPROFILE\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup"
; To reload after changes:
;     Open Explorer → double-click rwgk_hotkeys.ahk → click Yes if asked to Replace
; macOS: disable Mission Control shortcuts for Ctrl+Left / Ctrl+Right
;     System Settings → Keyboard → Keyboard Shortcuts → Mission Control
;         uncheck “Move left a space” / “Move right a space”

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

; -------- Ctrl+Right / Ctrl+Left → cycle *Windows Terminal app windows* --------
; Works for stable + preview. Enumerates by EXE, no dependence on window class/host.

#HotIf WinActive("ahk_exe WindowsTerminal.exe") || WinActive("ahk_exe WindowsTerminalPreview.exe")

^Right:: CycleWTWindows(1)   ; next window
^Left::  CycleWTWindows(-1)  ; previous window

#HotIf

CycleWTWindows(direction) {
    ; Build a list of visible WT windows (stable + preview)
    winList := []
    for exe in ["WindowsTerminal.exe", "WindowsTerminalPreview.exe"] {
        for hwnd in WinGetList("ahk_exe " exe) {
            try {
                ; keep only valid, visible top-level windows
                if (WinExist("ahk_id " hwnd)) {
                    style := WinGetStyle(hwnd)
                    if (style & 0x10000000) { ; WS_VISIBLE
                        winList.Push(hwnd)
                    }
                }
            }
        }
    }

    ; Need at least two to cycle
    if (winList.Length < 2)
        return

    ; Find which WT window is currently active
    activeIdx := 0
    for i, h in winList {
        if WinActive("ahk_id " h) {
            activeIdx := i
            break
        }
    }

    ; If focus isn't one of our WT windows, just activate the first
    if (activeIdx = 0) {
        WinActivate winList[1]
        return
    }

    ; Compute next/prev index and activate
    nextIdx := (direction > 0)
        ? (activeIdx = winList.Length ? 1 : activeIdx + 1)
        : (activeIdx = 1 ? winList.Length : activeIdx - 1)

    WinActivate winList[nextIdx]
}
