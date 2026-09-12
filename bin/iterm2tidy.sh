#!/bin/bash

usage() {
    echo "Usage: ${0##*/} [--dry-run] [<x-origin> <y-origin>] <x-offset> <y-offset>" >&2
}

dry_run=false

if [[ ${1:-} == --dry-run ]]; then
    dry_run=true
    shift
fi

if [[ $# -ne 2 && $# -ne 4 ]]; then
    usage
    exit 2
fi

if [[ $# -eq 4 ]]; then
    has_origin=true
    x_origin=$1
    y_origin=$2
    shift 2
else
    has_origin=false
    x_origin=0
    y_origin=0
fi

x_offset=$1
y_offset=$2

for argument_name in x_origin y_origin x_offset y_offset; do
    argument_value=${!argument_name}
    if [[ ! $argument_value =~ ^-?[0-9]+$ ]]; then
        echo "Error: ${argument_name//_/-} must be an integer: $argument_value" >&2
        usage
        exit 2
    fi
done

osascript - "$x_offset" "$y_offset" "$dry_run" "$has_origin" "$x_origin" "$y_origin" <<'APPLESCRIPT'
on run argv
    set xOffset to item 1 of argv as integer
    set yOffset to item 2 of argv as integer
    set dryRun to item 3 of argv is "true"
    set hasOrigin to item 4 of argv is "true"
    set requestedX to item 5 of argv as integer
    set requestedY to item 6 of argv as integer

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
        if hasOrigin then
            set anchorX to requestedX
            set anchorY to requestedY
        end if
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

            if not dryRun and (hasOrigin or windowNumber > 1) then
                set bounds of currentWindow to newBounds
            end if
        end repeat

        return output
    end tell
end run
APPLESCRIPT
