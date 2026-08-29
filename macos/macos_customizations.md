# macOS Customizations and Backup Notes

This file documents macOS and iTerm2 customizations that are worth preserving
across laptop replacements.

The intended use is both:

- as a reminder for manual maintenance; and
- as context for Codex or another agent. For example:

  > Please read macos/macos_customizations.md and remind me how to back up my macOS customizations.

This is expected to be incomplete. Add new customizations as they are made.

## iTerm2 workflow customizations

### Goal

Use iTerm2 efficiently for running multiple coding agents concurrently.

For each project, use one iTerm2 tab with two horizontally split panes:

- large upper pane: normal bash / development work
- smaller lower pane: Codex / agent session

A useful approximate split is:

- upper pane: 75%
- lower pane: 25%

The lower pane remains visible while working in the upper pane, making it easy to notice when Codex finishes or asks for input.

### Useful built-in iTerm2 shortcuts

- `Cmd-Shift-D`: split horizontally
- `Cmd-Shift-Enter`: maximize / restore the active pane
- `Cmd-Option-Up` / `Cmd-Option-Down`: move between split panes

With only two panes, switching to the next pane is sufficient.

### Custom iTerm2 shortcut: F12 = Next Pane

Configured in:

`iTerm2 -> Settings -> Keys -> Key Bindings`

Add a key binding:

- Keyboard Shortcut: `F12`
- Action: `Next Pane`

This makes `F12` toggle conveniently between the two panes.

### Custom macOS shortcut: F11 = Maximize Active Pane

iTerm2 does not expose `Maximize Active Pane` as a directly assignable iTerm2 Key Binding action.

Instead, use macOS App Shortcuts:

`System Settings -> Keyboard -> Keyboard Shortcuts... -> App Shortcuts`

Create:

- Application: `iTerm2`
- Menu Title: `Maximize Active Pane`
- Keyboard Shortcut: `F11`

`F11` then toggles the active pane between:

- its normal split-pane size; and
- filling the entire iTerm2 window.

This is the equivalent of iTerm2's built-in `Cmd-Shift-Enter`.

## iTerm2 saved arrangements

Saved arrangements are useful for recreating the normal collection of iTerm2 windows/tabs/panes after starting iTerm2 fresh.

To update an existing arrangement:

1. Arrange the current iTerm2 windows, tabs, and panes as desired.
2. Use:

   `Window -> Save Window Arrangement`

3. Save it using the same name as the existing arrangement.
4. Confirm replacement/overwrite.

A restored arrangement is effectively a snapshot/template; it is not continuously linked to the saved arrangement.

# Backing up customizations

## 1. Back up all iTerm2 settings

Do this manually from time to time:

`iTerm2 -> Settings -> General -> Settings -> Export All Settings and Data`

Drop the generated files into the Google Drive `abcd/Backups` folder.

This export is the primary iTerm2 backup and should preserve substantially
more than just key bindings, including profiles and other iTerm2 configuration.

When setting up a replacement Mac, use the corresponding iTerm2 import functionality.

## 2. Back up macOS keyboard settings

macOS stores keyboard customizations in its preferences database.

Two particularly relevant preference domains are:

- `NSGlobalDomain`
- `com.apple.symbolichotkeys`

`NSUserKeyEquivalents`, which is used for custom application menu shortcuts such as the iTerm2 `F11 -> Maximize Active Pane` shortcut, is part of the global/user preferences system.

### Backup script

Run `bin/backup_macos_settings.sh`, which will write to ~/Downloads.
Drop the generated files from there into the Google Drive `abcd/Backups` folder.

The exported `.plist` files are structured preference files.

To inspect one in a readable form:

```bash
plutil -p macos-preferences/NSGlobalDomain.plist
```
