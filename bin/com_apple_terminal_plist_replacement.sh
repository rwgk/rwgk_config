#! /bin/bash
# Problem:
#   Terminal app overwrites the plist file when quitting.
# Solution:
#   com_apple_terminal_plist_replacement.sh >& /dev/null &
#   Quit Terminal app quickly.
#   Wait ~20 seconds.
#   Restart Terminal app. It will use the new plist settings.
sleep 20
cp /Users/rwgk/Downloads/com.apple.Terminal.plist /Users/rwgk/Library/Preferences/com.apple.Terminal.plist
