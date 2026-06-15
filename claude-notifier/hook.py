#!/usr/bin/env python3
"""Claude Code hook handler -> updates the shared notifier state file.

Wired into the global ~/.claude/settings.json for these events:
  Notification     -> session needs your attention/permission (status: waiting)
  Stop             -> turn finished, your move           (status: idle)
  UserPromptSubmit -> you replied, session is busy again  (remove)
  SessionEnd       -> session closed                      (remove)

Claude Code delivers the event payload as JSON on stdin. The event name is
read from that payload (hook_event_name); the optional argv[1] is a fallback.
Hooks must finish fast and exit 0 so they never block the session.
"""
import sys
import os
import json
import time
import tempfile
import ctypes
from ctypes import wintypes

STATE_DIR = os.path.join(os.path.expanduser("~"), ".claude", "notifier")
STATE_FILE = os.path.join(STATE_DIR, "state.json")
LOCK_FILE = os.path.join(STATE_DIR, "state.lock")
STALE_SECONDS = 24 * 3600


def acquire_lock(timeout=5.0):
    """Cross-process lock via O_EXCL lock file; steals a stale lock on timeout."""
    start = time.monotonic()
    while True:
        try:
            return os.open(LOCK_FILE, os.O_CREAT | os.O_EXCL | os.O_RDWR)
        except FileExistsError:
            if time.monotonic() - start > timeout:
                try:
                    os.unlink(LOCK_FILE)
                except OSError:
                    pass
                start = time.monotonic()
            time.sleep(0.02)


def release_lock(fd):
    try:
        os.close(fd)
    except OSError:
        pass
    try:
        os.unlink(LOCK_FILE)
    except OSError:
        pass


def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, ValueError):
        return {}


def save_state(state):
    fd, tmp = tempfile.mkstemp(dir=STATE_DIR, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        os.replace(tmp, STATE_FILE)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def project_name(cwd):
    name = os.path.basename(cwd.rstrip("/\\"))
    return name or cwd or "?"


def window_title_for_pid(pid):
    """Return the VSCode window title for a PID (the 'which window' identifier).

    Uses Win32 EnumWindows so the Unicode title (incl. Chinese) is read
    correctly and fast, with no subprocess. Empty string on any failure.
    """
    if not pid:
        return ""
    try:
        user32 = ctypes.windll.user32
        titles = []
        EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

        def cb(hwnd, _):
            dw = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(dw))
            if dw.value == pid and user32.IsWindowVisible(hwnd):
                n = user32.GetWindowTextLengthW(hwnd)
                if n > 0:
                    buf = ctypes.create_unicode_buffer(n + 1)
                    user32.GetWindowTextW(hwnd, buf, n + 1)
                    if buf.value.strip():
                        titles.append(buf.value)
            return True

        user32.EnumWindows(EnumWindowsProc(cb), 0)
        for t in titles:
            if "Visual Studio Code" in t:
                return t
        return titles[0] if titles else ""
    except Exception:
        return ""


def page_and_folder(cwd):
    """Best 'page name' + folder for the current session.

    Inside the VSCode extension the window title looks like
    '<conversation/tab> - <folder> - Visual Studio Code'. We split it into
    the tab title (the page name the user recognizes) and the folder.
    Falls back to the cwd basename for the terminal CLI (no VSCODE_PID).
    """
    proj = project_name(cwd)
    pid = 0
    try:
        pid = int(os.environ.get("VSCODE_PID", "0"))
    except ValueError:
        pid = 0
    title = window_title_for_pid(pid)
    if not title:
        return proj, ""
    core = title
    for suffix in (" - Visual Studio Code", " - Code"):
        if core.endswith(suffix):
            core = core[: -len(suffix)]
            break
    if " - " in core:
        page, folder = core.rsplit(" - ", 1)
    else:
        page, folder = core, proj
    return (page.strip() or proj), folder.strip()


def main():
    os.makedirs(STATE_DIR, exist_ok=True)
    raw = sys.stdin.read() if not sys.stdin.isatty() else ""
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        payload = {}

    event = payload.get("hook_event_name") or (sys.argv[1] if len(sys.argv) > 1 else "")
    sid = payload.get("session_id") or "unknown"
    cwd = payload.get("cwd") or payload.get("workspace") or ""
    message = (payload.get("message") or "").strip()

    lock = acquire_lock()
    try:
        state = load_state()
        now = time.time()

        if event == "SessionEnd":
            state.pop(sid, None)
        elif event in ("Notification", "Stop", "UserPromptSubmit"):
            page, folder = page_and_folder(cwd)
            try:
                vscode_pid = int(os.environ.get("VSCODE_PID", "0"))
            except ValueError:
                vscode_pid = 0
            entry = {
                "session_id": sid,
                "cwd": cwd,
                "project": project_name(cwd),
                "page": page,
                "folder": folder,
                "vscode_pid": vscode_pid,
                "ts": now,
            }
            if event == "Notification":
                # needs confirmation / waiting for input -> yellow
                entry["status"] = "needs"
                entry["message"] = message or "Needs confirmation / waiting for input"
            elif event == "Stop":
                # turn finished, your move -> yellow
                entry["status"] = "needs"
                entry["message"] = "Turn finished — your move"
            else:  # UserPromptSubmit -> Claude is now working -> green
                entry["status"] = "working"
                prompt = (payload.get("prompt") or "").strip().replace("\n", " ")
                entry["message"] = (prompt[:60] + "…") if len(prompt) > 60 else (prompt or "Working…")

            # one row per window+page: drop stale entries that represent the
            # same VSCode window + conversation under a different session id
            # (e.g. a subagent/Task session, or a rolled-over session id).
            key = (entry["vscode_pid"], entry["page"])
            for other_sid in list(state.keys()):
                if other_sid != sid:
                    o = state[other_sid]
                    if (o.get("vscode_pid"), o.get("page")) == key:
                        del state[other_sid]
            state[sid] = entry

        # prune anything that has been sitting around too long
        for k in list(state.keys()):
            if now - state[k].get("ts", now) > STALE_SECONDS:
                del state[k]

        save_state(state)
    finally:
        release_lock(lock)

    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # never let a hook failure surface to the session
        sys.exit(0)
