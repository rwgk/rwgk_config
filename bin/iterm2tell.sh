#!/bin/bash

osascript <<'APPLESCRIPT'
on formatRelativeCoordinate(axisName, coordinateDelta)
    if coordinateDelta >= 0 then
        return axisName & "+" & coordinateDelta
    end if
    return axisName & coordinateDelta
end formatRelativeCoordinate

tell application "iTerm2"
    set output to ""
    set unorderedWindows to every window
    set windowCount to count of unorderedWindows
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

    repeat with windowNumber from 1 to windowCount
        set w to item windowNumber of orderedWindows
        set windowId to id of w
        set windowName to name of w
        set {x1, y1, x2, y2} to bounds of w

        set windowWidth to x2 - x1
        set windowHeight to y2 - y1

        if windowNumber is 1 then
            set positionOutput to "  x=" & x1 & "  y=" & y1
        else
            set xOffset to x1 - previousX
            set yOffset to y1 - previousY
            set positionOutput to ¬
                "  " & my formatRelativeCoordinate("x", xOffset) & ¬
                "  " & my formatRelativeCoordinate("y", yOffset)
        end if

        set output to output & ¬
            "⌘" & windowNumber & ¬
            "  id=" & windowId & ¬
            positionOutput & ¬
            "  width=" & windowWidth & ¬
            "  height=" & windowHeight & ¬
            "  title=" & quoted form of windowName & linefeed

        set previousX to x1
        set previousY to y1
    end repeat

    return output
end tell
APPLESCRIPT
