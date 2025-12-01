mkdir -p "$HOME/Library/LaunchAgents"
cat > "$HOME/Library/LaunchAgents/com.rwgk.mute-at-22.plist" << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <!-- Unique identifier for this job -->
    <key>Label</key>
    <string>com.rwgk.mute-at-22</string>

    <!-- Run as the current user -->
    <key>ProgramArguments</key>
    <array>
        <string>$HOME/rwgk_config/macos/set_audio_volume_0.sh</string>
    </array>

    <!-- Run every day at 22:00 local time -->
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>22</integer>
        <key>Minute</key>
        <integer>0</integer>
    </dict>

    <!-- Optional but useful: log output -->
    <key>StandardOutPath</key>
    <string>$HOME/Library/Logs/mute-at-22.log</string>
    <key>StandardErrorPath</key>
    <string>$HOME/Library/Logs/mute-at-22.err</string>
</dict>
</plist>
EOF
