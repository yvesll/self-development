#!/usr/bin/env python3
"""Merge the notifier hooks into ~/.claude/settings.json (idempotent).

Backs up the existing settings file, then adds command hooks for
Notification / Stop / UserPromptSubmit / SessionEnd that call hook.py.
Re-running is safe: it removes any prior notifier entries first, so it
never duplicates. Run with --uninstall to remove them again.
"""
import os
import sys
import json
import time
import shutil

SETTINGS = os.path.join(os.path.expanduser("~"), ".claude", "settings.json")
HOOK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hook.py").replace("\\", "/")
EVENTS = ["Notification", "Stop", "UserPromptSubmit", "SessionEnd"]
TAG = "claude-notifier"  # marker so we can find/replace our own entries


def command_for(event):
    return f'python "{HOOK}" {event}'


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
