#!/usr/bin/env python3
"""Merge the notifier hooks into ~/.claude/settings.json (idempotent).

Backs up the existing settings file, then adds command hooks for
SessionStart / Notification / Stop / UserPromptSubmit / SessionEnd that call
hook.py. Re-running is safe: it removes any prior notifier entries first, so it
never duplicates. Run with --uninstall to remove them again.

Run it from each environment whose sessions you want to track:
  - On Windows:  python install-hooks.py   -> wires the Windows settings.
  - Inside WSL:  python3 install-hooks.py  -> wires the WSL settings to call
    the SAME Windows hook via `python.exe` interop, so WSL sessions land in the
    one shared Windows state file the HUD reads.
"""
import os
import re
import sys
import json
import time
import shutil


def _is_wsl():
    if not sys.platform.startswith("linux"):
        return False
    try:
        with open("/proc/version", "r", encoding="utf-8", errors="ignore") as f:
            return "microsoft" in f.read().lower()
    except OSError:
        return False


def _win_path(p):
    """/mnt/c/Users/x/h.py -> C:/Users/x/h.py (for handing to a Windows exe)."""
    m = re.match(r"^/mnt/([a-zA-Z])/(.*)$", p)
    return f"{m.group(1).upper()}:/{m.group(2)}" if m else p


SETTINGS = os.path.join(os.path.expanduser("~"), ".claude", "settings.json")
_HOOK_FS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hook.py").replace("\\", "/")
# In WSL the hook runs on Windows (it uses Win32 APIs), so call it via interop
# with the Windows-style path; on Windows just call it directly.
if _is_wsl():
    PYTHON, HOOK = "python.exe", _win_path(_HOOK_FS)
else:
    PYTHON, HOOK = "python", _HOOK_FS
EVENTS = ["SessionStart", "Notification", "Stop", "UserPromptSubmit", "SessionEnd"]
TAG = "claude-notifier"  # marker so we can find/replace our own entries


def command_for(event):
    return f'{PYTHON} "{HOOK}" {event}'


def is_ours(entry):
    for h in entry.get("hooks", []):
        if TAG in h.get("command", "") or "claude-notifier" in h.get("command", ""):
            return True
    return False


def main():
    uninstall = "--uninstall" in sys.argv
    os.makedirs(os.path.dirname(SETTINGS), exist_ok=True)

    settings = {}
    if os.path.exists(SETTINGS):
        with open(SETTINGS, "r", encoding="utf-8") as f:
            settings = json.load(f)
        backup = f"{SETTINGS}.bak-{int(time.time())}"
        shutil.copy2(SETTINGS, backup)
        print(f"backup -> {backup}")

    hooks = settings.setdefault("hooks", {})

    for event in EVENTS:
        arr = hooks.get(event, [])
        # drop any previous notifier entries for a clean re-install
        arr = [e for e in arr if not is_ours(e)]
        if not uninstall:
            arr.append({
                "matcher": "",
                "hooks": [{"type": "command", "command": command_for(event)}],
            })
        if arr:
            hooks[event] = arr
        else:
            hooks.pop(event, None)

    if not hooks:
        settings.pop("hooks", None)

    with open(SETTINGS, "w", encoding="utf-8") as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)

    action = "removed from" if uninstall else "installed into"
    print(f"notifier hooks {action} {SETTINGS}")
    print("events:", ", ".join(EVENTS))
    if not uninstall:
        print(f"hook command: {command_for('<Event>')}")


if __name__ == "__main__":
    main()
