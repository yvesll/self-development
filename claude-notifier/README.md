# Claude Notifier — always-on-top "who needs me" HUD

A small, always-on-top, draggable window that shows **which Claude Code session
is waiting for you**, so you don't miss a confirmation prompt while doing
something else. Works across every VSCode window/session at once.

UI text is English; **session/page names render in any language** (Chinese
included) because titles are read as Unicode.

## Quick start (new machine)

1. Make sure **Python 3** is installed and on PATH, and you use the **Claude
   Code VSCode extension**.
2. Copy this whole `claude-notifier` folder anywhere on your machine.
3. Double-click **`setup.bat`** (wires the hooks + opens the window).
4. **Restart any Claude Code sessions** that were already open — hooks only
   apply to newly started sessions.

That's it. A green row means Claude is working; a yellow row (title bar blinks)
means a session is waiting for you. Left-click a row to jump to that window.
Optional: run `python autostart.py` to launch the window at every login.

## How it works

Claude Code's **hook** mechanism is the state signal. On each event `hook.py`
writes status into a shared file `~/.claude/notifier/state.json` (keyed by
`session_id`), and the floating window `notifier.py` polls it every ~0.6 s.

| Hook event | Meaning | Shown as |
|---|---|---|
| `UserPromptSubmit` | You submitted — Claude is working | 🟢 Working (calm) |
| `Notification` | Needs permission / waiting for input | 🟡 Needs you (title bar blinks amber) |
| `Stop` | A turn finished — your move | 🟡 Needs you (blinks) |
| `SessionEnd` | Session closed | removed |

Green = busy, yellow = needs you. The title bar blinks amber **only** while a
yellow session is present, so a session that needs you is easy to spot while
green ones stay quiet.

**Page name (which window).** In the VSCode extension the hook reads the
`VSCODE_PID` env var, finds that window via Win32 `EnumWindows`/`GetWindowTextW`,
and parses its title `<conversation/tab> - <folder> - Visual Studio Code` into
`page` (the conversation title — line 1) and `folder` (line 2). It also stores
the window PID so the HUD can jump straight to that window. In a plain terminal
(no `VSCODE_PID`) it falls back to the cwd folder name.

## Install

```bash
# 1) Merge the hooks into the global ~/.claude/settings.json
#    (auto-backs-up first; safe to re-run, never duplicates)
python install-hooks.py

# 2) Start the floating window (no console window)
start-notifier.bat
```

Hook changes only apply to **newly started** sessions; restart any session
that was already running.

## Usage

- **Drag** the title bar to move it anywhere (position is remembered in
  `window.json`).
- **Hover** a row → tooltip with the full page name, full path, and session id
  (handy when a long title is truncated).
- **Left-click** a row → bring that VSCode window to the foreground.
- **Right-click** a row → copy that session's working directory path.
- **Same-named pages**: when two windows share a page name, line 2 appends
  `#xxxx` (last 4 of the session id) to tell them apart.
- **Left-click** also flashes the target window's caption/taskbar button so
  your eye lands on it after the switch.
- **`—`** collapse / expand the list.
- **`✕`** close the window (hooks keep running; relaunch with
  `start-notifier.bat`).
- The saved position is re-validated against your current monitors on launch,
  so the window never lands off-screen after unplugging a display.

Auto-start on login:

```bash
python autostart.py              # add a Startup shortcut (runs pythonw, no console)
python autostart.py --uninstall  # remove it
```

## Uninstall

```bash
python install-hooks.py --uninstall   # removes the hooks (also auto-backs-up)
```

Delete this folder to remove it completely. The `~/.claude/notifier/` state and
position files can be deleted too.

## Files

| File | Role |
|---|---|
| `setup.bat` | One-click: wire hooks + launch the window (for a new machine) |
| `hook.py` | Hook handler Claude Code calls; updates the shared state |
| `notifier.py` | Tkinter HUD (always-on-top, borderless, draggable) |
| `install-hooks.py` | Safely merge/remove the hooks in global settings.json |
| `autostart.py` | Add/remove a Windows Startup shortcut |
| `start-notifier.bat` | Launch the HUD with `pythonw` (no console) |

## Requirements

- Windows + Python 3 with `tkinter` (bundled with the standard installer).
- No third-party packages.
- The window-focus and page-name features use Win32 APIs, so they are
  Windows-only. On other OSes the tool still works but shows the cwd folder
  name and has no click-to-focus.
