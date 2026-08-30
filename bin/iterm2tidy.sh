#!/bin/bash

usage() {
    echo "Usage: ${0##*/} [--dry-run] <x-offset> <y-offset>" >&2
}

dry_run=false

if [[ ${1:-} == --dry-run ]]; then
    dry_run=true
    shift
fi

if [[ $# -ne 2 ]]; then
    usage
    exit 2
fi

x_offset=$1
y_offset=$2

if [[ ! $x_offset =~ ^-?[0-9]+$ ]]; then
    echo "Error: x-offset must be an integer: $x_offset" >&2
    usage
    exit 2
fi

if [[ ! $y_offset =~ ^-?[0-9]+$ ]]; then
    echo "Error: y-offset must be an integer: $y_offset" >&2
    usage
    exit 2
fi

osascript - "$x_offset" "$y_offset" "$dry_run" <<'APPLESCRIPT'
on run argv
    set xOffset to item 1 of argv as integer
    set yOffset to item 2 of argv as integer
    set dryRun to item 3 of argv is "true"

    tell application "iTerm2"
        set unorderedWindows to every window
        set windowCount to count of unorderedWindows

        if windowCount is 0 then
            error "iTerm2 has no windows to arrange."
        end if

        set orderedWindows to {}
        set lastWindowId to -1

        repeat windowCount times
            set nextWindow to missing value
            set nextWindowId to missing value

            repeat with candidateWindow in unorderedWindows
                set candidateWindowId to id of candidateWindow
                if candidateWindowId > lastWindowId then
                    if nextWindow is missing value or candidateWindowId < nextWindowId then
                        set nextWindow to candidateWindow
                        set nextWindowId to candidateWindowId
                    end if
                end if
            end repeat

            set end of orderedWindows to nextWindow
            set lastWindowId to nextWindowId
        end repeat

        set anchorWindow to item 1 of orderedWindows
        set {anchorX, anchorY, anchorRight, anchorBottom} to bounds of anchorWindow
        set anchorWidth to anchorRight - anchorX
        set anchorHeight to anchorBottom - anchorY
        set output to ""

        repeat with windowNumber from 1 to windowCount
            set currentWindow to item windowNumber of orderedWindows
            set windowId to id of currentWindow
            set {oldX, oldY, oldRight, oldBottom} to bounds of currentWindow
            set newX to anchorX + ((windowNumber - 1) * xOffset)
            set newY to anchorY + ((windowNumber - 1) * yOffset)
            set newBounds to {newX, newY, newX + anchorWidth, newY + anchorHeight}

            set output to output & ¬
                "⌘" & windowNumber & ¬
                "  id=" & windowId & ¬
                "  from={" & oldX & ", " & oldY & ", " & oldRight & ", " & oldBottom & "}" & ¬
                "  to={" & newX & ", " & newY & ", " & (newX + anchorWidth) & ", " & (newY + anchorHeight) & "}" & linefeed

            if not dryRun and windowNumber > 1 then
                set bounds of currentWindow to newBounds
            end if
        end repeat

        return output
    end tell
end run
APPLESCRIPT
