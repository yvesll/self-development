# 🔴🟡🟢 claude-notifier — a little always-on-top HUD for Claude Code

I made a fun little tool that floats on top of your screen and tells you, at a
glance, **what every Claude Code session is doing** — so you can go do other
things and never miss a prompt that needs you.

## Traffic-light status

- 🔴 **Red** = the AI is working (sit tight)
- 🟡 **Yellow** = it needs your call / confirmation
- 🟢 **Green** = done — your turn

## Features

- **One HUD for all your VSCode windows** — open a new Claude page and it shows
  up automatically; close it and it disappears in sync.
- **Stale sessions auto-hide** — no phantom "waiting" rows at login when VSCode
  isn't even open.
- **Left-click a row → jump straight to that VSCode window** (it even flashes
  the window so your eye lands on it).
- **Hover** a row → full page name + path · **Right-click** → copy the path.
- Draggable, remembers its position, multi-monitor safe.
- Same-named conversations get a short `#id` so you can tell them apart.
- Optional: launch at login with `python autostart.py`.

## Try it

👉 https://github.com/lucien-nxp/self-development/tree/main/claude-notifier

Just clone it and double-click **`setup.bat`** — that's it.

## Notes

- Windows + Python 3 + the Claude Code VSCode extension.
- Hooks only apply to **newly started** sessions, so restart any Claude pages
  that were already open.
- Uninstall anytime: `python install-hooks.py --uninstall`.

Feedback & ideas welcome! 🙌
