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
import re
import json
import time
import shutil
import subprocess
import tempfile
import ctypes
from ctypes import wintypes

IS_WINDOWS = sys.platform.startswith("win")
IS_LINUX = sys.platform.startswith("linux")

# model spec with a context/variant suffix, e.g. claude-opus-4-8[1m]
_VARIANT_RE = re.compile(r'claude-[a-z]+-[0-9-]+(\[[0-9a-z]+\])')

# Claude list price, USD per 1M tokens: (input, output)
_PRICING = {
    "haiku":  (1.00,  5.00),
    "sonnet": (3.00, 15.00),
    "opus":   (5.00, 25.00),
    "fable":  (10.00, 50.00),
    "mythos": (10.00, 50.00),
}
_DEFAULT_PRICE = _PRICING["sonnet"]


def _price_for(model_id):
    m = (model_id or "").lower()
    for key, pricing in _PRICING.items():
        if key in m:
            return pricing
    return _DEFAULT_PRICE


def _parse_transcript(transcript_path):
    """Return usage stats + ai_title from the JSONL transcript."""
    in_tok = out_tok = cache_tok = 0
    cost = 0.0
    model_id = ""
    variant = ""        # e.g. "[1m]" — the model's context/variant suffix
    ai_title = ""
    seen_usage = set()  # (message id, request id) already counted — see below
    if not transcript_path or not os.path.exists(transcript_path):
        return {"in_tok": 0, "out_tok": 0, "cache_tok": 0, "cost": 0.0,
                "model_id": "", "ai_title": ""}
    try:
        with open(transcript_path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                # the per-message model field drops the [1m]-style suffix; recover
                # it from the full spec that appears in the raw line (e.g. the
                # /model command output). Last hit wins = current selection.
                vm = _VARIANT_RE.search(line)
                if vm:
                    variant = vm.group(1)
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                if obj.get("type") == "ai-title" and not ai_title:
                    ai_title = obj.get("aiTitle", "")
                    continue
                msg = obj.get("message") or {}
                usage = msg.get("usage")
                if not usage:
                    continue
                # Claude Code writes ONE JSONL line per content block of an
                # assistant turn (text, each tool_use, …) and every line repeats
                # the SAME message.usage. Count each API response once — keyed on
                # (message id, request id), the dedup ccusage uses — otherwise the
                # cost/token totals inflate by the block count of each turn (2-3x
                # in practice, and it "jumps" because tool-heavy turns inflate more).
                mkey = (msg.get("id"), obj.get("requestId"))
                if mkey != (None, None):
                    if mkey in seen_usage:
                        continue
                    seen_usage.add(mkey)
                mid = msg.get("model", "")
                if mid:
                    model_id = mid   # last-wins: tracks the most recently used model
                pin, pout = _price_for(mid or model_id)
                i = usage.get("input_tokens", 0) or 0
                o = usage.get("output_tokens", 0) or 0
                cr = usage.get("cache_read_input_tokens", 0) or 0
                cw = usage.get("cache_creation_input_tokens", 0) or 0
                in_tok += i
                out_tok += o
                cache_tok += cr + cw
                cost += i / 1_000_000 * pin
                cost += cr / 1_000_000 * pin * 0.1    # cache read: 10% of input price
                cost += cw / 1_000_000 * pin * 1.25   # cache write: 125% of input price
                cost += o / 1_000_000 * pout
    except Exception:
        pass
    if variant and model_id and "[" not in model_id:
        model_id += variant
    return {"in_tok": in_tok, "out_tok": out_tok, "cache_tok": cache_tok,
            "cost": cost, "model_id": model_id, "ai_title": ai_title}


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


def _window_title_for_pid_win(pid):
    """Win32: read the window title for a PID (Unicode-correct, no subprocess)."""
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


def _window_title_for_pid_linux(pid):
    """Linux/X11: read the window title via wmctrl or xdotool. '' if unavailable."""
    def run(args):
        try:
            r = subprocess.run(args, capture_output=True, text=True, timeout=2.0)
            return r.stdout if r.returncode == 0 else ""
        except Exception:
            return ""

    if shutil.which("wmctrl"):
        for line in run(["wmctrl", "-lp"]).splitlines():
            parts = line.split(None, 4)
            if len(parts) >= 5 and parts[2].isdigit() and int(parts[2]) == pid:
                return parts[4]
    if shutil.which("xdotool"):
        lines = [x for x in run(["xdotool", "search", "--pid", str(pid),
                                 "getwindowname"]).splitlines() if x.strip()]
        for t in lines:
            if "Visual Studio Code" in t:
                return t
        if lines:
            return lines[-1]
    return ""


def window_title_for_pid(pid):
    """Return the editor window title for a PID (the 'which window' identifier).

    Windows uses Win32; Linux/X11 uses wmctrl/xdotool if installed. Returns ''
    on any failure (Wayland, missing tools, etc.) so the caller falls back to
    the cwd folder name.
    """
    if not pid:
        return ""
    if IS_WINDOWS:
        return _window_title_for_pid_win(pid)
    if IS_LINUX:
        return _window_title_for_pid_linux(pid)
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
            existing = state.get(sid, {})
            entry = {
                "session_id": sid,
                "cwd": cwd,
                "project": project_name(cwd),
                "page": page,
                "folder": folder,
                "vscode_pid": vscode_pid,
                "model": existing.get("model", ""),
                "session_title": existing.get("session_title", ""),
                "ts": now,
            }
            if event == "Notification":
                # needs your judgment / permission -> yellow
                entry["status"] = "needs"
                entry["message"] = message or "Needs confirmation / waiting for input"
            elif event == "Stop":
                # turn finished, your move -> green (done)
                entry["status"] = "done"
                entry["message"] = "Turn finished — your move"
            else:  # UserPromptSubmit -> Claude is now working -> red (busy)
                entry["status"] = "working"
                prompt = (payload.get("prompt") or "").strip().replace("\n", " ")
                entry["message"] = (prompt[:60] + "…") if len(prompt) > 60 else (prompt or "Working…")

            # attach usage stats (parse transcript if available, else carry over)
            transcript = payload.get("transcript_path", "")
            if transcript:
                entry["stats"] = _parse_transcript(transcript)
                if entry["stats"].get("ai_title"):
                    entry["session_title"] = entry["stats"]["ai_title"]
                if entry["stats"].get("model_id") and not entry["model"]:
                    entry["model"] = entry["stats"]["model_id"]
            elif "stats" in existing:
                entry["stats"] = existing["stats"]
                if existing["stats"].get("ai_title") and not entry.get("session_title"):
                    entry["session_title"] = existing["stats"]["ai_title"]

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
