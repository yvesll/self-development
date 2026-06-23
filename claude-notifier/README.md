# Claude Notifier — always-on-top "who needs me" HUD

A small, always-on-top, draggable window that shows **which Claude Code session
is waiting for you**, so you don't miss a confirmation prompt while doing
something else. Works across every VSCode window/session at once.

UI text is English; **session/page names render in any language** (Chinese
included) because titles are read as Unicode.

## Quick start (new machine)

1. Make sure **Python 3** (with `tkinter`) is installed and on PATH, and you use
   the **Claude Code VSCode extension**.
2. Copy this whole `claude-notifier` folder anywhere on your machine.
3. Run setup:
   - **Windows**: double-click **`setup.bat`**
   - **Linux/macOS**: `sh setup.sh`
4. **Restart any Claude Code sessions** that were already open — hooks only
   apply to newly started sessions.

> **Linux note:** install `wmctrl` (or `xdotool`) on **X11** to get page titles
> and click-to-focus (`sudo apt install wmctrl`). On **Wayland** windows can't be
> queried, so rows show the folder name and click-to-focus is disabled — colors
> and auto add/remove still work everywhere.

That's it. Traffic-light colors: 🔴 red = Claude is working, 🟡 yellow = it
needs your call, 🟢 green = it's done and it's your turn. Left-click a row to
jump to that window. Optional: run `python autostart.py` to launch at login.

## How it works

Claude Code's **hook** mechanism is the state signal. On each event `hook.py`
writes status into a shared file `~/.claude/notifier/state.json` (keyed by
`session_id`), and the floating window `notifier.py` polls it every ~0.6 s.

| Hook event | Meaning | Shown as |
|---|---|---|
| `UserPromptSubmit` | You submitted — Claude is working | 🔴 Working |
| `Notification` | Needs permission / your judgment | 🟡 Needs you |
| `Stop` | A turn finished — your move | 🟢 Done (your turn) |
| `SessionEnd` | Session closed | removed |

Traffic-light reading: 🔴 red = AI busy (wait), 🟡 yellow = needs your call,
🟢 green = done / your turn. No blinking — the colors and always-on-top
placement are enough.

**Stale sessions are auto-hidden.** Each row remembers its VSCode window; if
that window is gone (closed, or left over in state after a reboot) the row is
hidden automatically — so you won't see phantom "waiting" rows at login when no
VSCode is even open.

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
- Rows for **closed VSCode windows are hidden automatically** (no stale
  "waiting" entries after a reboot).
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
| `setup.bat` / `setup.sh` | One-click: wire hooks + launch the window (Windows / Linux·macOS) |
| `hook.py` | Hook handler Claude Code calls; updates the shared state |
| `notifier.py` | Tkinter HUD (always-on-top, borderless, draggable) |
| `install-hooks.py` | Safely merge/remove the hooks in global settings.json |
| `autostart.py` | Add/remove an autostart entry (Startup shortcut / `.desktop`) |
| `start-notifier.bat` / `start-notifier.sh` | Launch the HUD (no console / background) |

## Requirements & platform support

Python 3 with `tkinter`, no third-party Python packages. The core (status
colors, auto add/remove, drag, dedup, stale-hiding) is cross-platform; the
window-aware extras depend on the OS:

| Feature | Windows | Linux (X11) | Linux (Wayland) / macOS |
|---|:--:|:--:|:--:|
| Status colors + auto add/remove | ✅ | ✅ | ✅ |
| Stale-session hiding | ✅ (window) | ✅ (process) | ✅ (process) |
| Page name (conversation title) | ✅ Win32 | ✅ via `wmctrl`/`xdotool` | ➖ folder name |
| Left-click → focus window | ✅ | ✅ via `wmctrl`/`xdotool` | ➖ copies path |

- **Linux/X11**: `sudo apt install wmctrl` (or `xdotool`) enables page titles and
  click-to-focus. Without them it degrades to the folder name.
- **Wayland**: querying/activating other windows is blocked by design, so those
  two features are unavailable; everything else works.
