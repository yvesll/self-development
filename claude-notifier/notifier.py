#!/usr/bin/env python3
"""Always-on-top, draggable HUD showing what your Claude Code sessions are doing.

Reads the shared state written by hook.py and shows one row per session,
traffic-light colored: red = working, yellow = needs you, green = done.
Frameless + topmost so it floats above your editor; drag the title bar to
move it (position is remembered). Cross-platform (Win32 / X11) with graceful
fallback. Launch with pythonw (Windows) or in the background (Linux).
"""
import os
import sys
import json
import time
import shutil
import subprocess
import ctypes
from ctypes import wintypes
import tkinter as tk
import tkinter.font as tkfont

IS_WINDOWS = sys.platform.startswith("win")
IS_LINUX = sys.platform.startswith("linux")

STATE_DIR = os.path.join(os.path.expanduser("~"), ".claude", "notifier")
STATE_FILE = os.path.join(STATE_DIR, "state.json")
POS_FILE = os.path.join(STATE_DIR, "window.json")
POLL_MS = 600

BG = "#1e1e1e"
BAR_BG = "#2d2d30"
FG = "#e8e8e8"
SUB = "#9a9a9a"
WIDTH = 320
UI_FONT = "Segoe UI" if IS_WINDOWS else "Sans"

STATUS = {
    # status: (dot color, label, priority — lower sorts first)
    # Traffic-light: red = AI busy (wait), yellow = needs your call, green = done.
    "needs": ("#faad14", "Needs you", 0),     # yellow — needs your judgment
    "done": ("#52c41a", "Done", 1),           # green  — finished, your turn
    "working": ("#ff4d4f", "Working", 2),     # red    — AI is busy
}


def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, ValueError):
        return {}


def load_pos():
    try:
        with open(POS_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
            return int(d["x"]), int(d["y"])
    except Exception:
        return None


def save_pos(x, y):
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(POS_FILE, "w", encoding="utf-8") as f:
            json.dump({"x": x, "y": y}, f)
    except OSError:
        pass


def ago(ts):
    s = max(0, int(time.time() - ts))
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m"
    return f"{s // 3600}h"


# ===================== window helpers (platform-branched) =====================
# Windows uses the Win32 API directly. Linux shells out to wmctrl/xdotool when
# present (X11 only); otherwise it degrades gracefully. Nothing here changes
# Windows behavior — the Linux branches never run on win32.

def live_window_pids():
    """Windows: set of PIDs owning a visible titled window. Else: None.

    Used to hide stale sessions whose VSCode window is gone. On non-Windows we
    return None and callers fall back to a process-alive check (pid_alive).
    """
    if not IS_WINDOWS:
        return None
    try:
        user32 = ctypes.windll.user32
        pids = set()
        EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

        def cb(hwnd, _):
            if user32.IsWindowVisible(hwnd) and user32.GetWindowTextLengthW(hwnd) > 0:
                dw = wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(dw))
                pids.add(dw.value)
            return True

        user32.EnumWindows(EnumWindowsProc(cb), 0)
        return pids
    except Exception:
        return None


def pid_alive(pid):
    """Cross-platform 'is this process still running?' (used off Windows)."""
    if not pid:
        return True
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True   # exists but not ours
    except Exception:
        return True   # never hide a row just because we couldn't tell


def _hwnd_for_pid(pid):
    """Top-level visible, titled window owned by pid (the VSCode window)."""
    if not pid:
        return None
    user32 = ctypes.windll.user32
    found = []
    EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

    def cb(hwnd, _):
        dw = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(dw))
        if dw.value == pid and user32.IsWindowVisible(hwnd):
            if user32.GetWindowTextLengthW(hwnd) > 0:
                found.append(hwnd)
        return True

    user32.EnumWindows(EnumWindowsProc(cb), 0)
    return found[0] if found else None


class _FLASHWINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT),
        ("hwnd", wintypes.HWND),
        ("dwFlags", wintypes.DWORD),
        ("uCount", wintypes.UINT),
        ("dwTimeout", wintypes.DWORD),
    ]


def _flash_window_win(hwnd, count=4):
    """Blink the target window's caption + taskbar button to draw the eye."""
    FLASHW_ALL = 0x3
    try:
        info = _FLASHWINFO(ctypes.sizeof(_FLASHWINFO), hwnd, FLASHW_ALL, count, 120)
        ctypes.windll.user32.FlashWindowEx(ctypes.byref(info))
    except Exception:
        pass


def _focus_window_win(pid):
    """Bring the VSCode window for pid to the foreground (Win32)."""
    hwnd = _hwnd_for_pid(pid)
    if not hwnd:
        return False
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    SW_RESTORE = 9
    try:
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, SW_RESTORE)
        fg = user32.GetForegroundWindow()
        target_tid = user32.GetWindowThreadProcessId(fg, None)
        this_tid = kernel32.GetCurrentThreadId()
        user32.AttachThreadInput(this_tid, target_tid, True)
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
        user32.AttachThreadInput(this_tid, target_tid, False)
        _flash_window_win(hwnd)
        return True
    except Exception:
        return False


def _run(args, timeout=2.0):
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return r.stdout if r.returncode == 0 else ""
    except Exception:
        return ""


def _linux_window_for_pid(pid):
    """(window_id, title) for pid via wmctrl -lp, or (None, '')."""
    for line in _run(["wmctrl", "-lp"]).splitlines():
        parts = line.split(None, 4)
        if len(parts) >= 5 and parts[2].isdigit() and int(parts[2]) == pid:
            return parts[0], parts[4]
    return None, ""


def _focus_window_linux(pid):
    """Activate (and request attention on) the X11 window for pid. X11 only."""
    if shutil.which("wmctrl"):
        winid, _ = _linux_window_for_pid(pid)
        if winid:
            _run(["wmctrl", "-i", "-a", winid])
            _run(["wmctrl", "-i", "-r", winid, "-b", "add,demands_attention"])
            return True
    if shutil.which("xdotool"):
        ids = [x for x in _run(["xdotool", "search", "--pid", str(pid)]).splitlines() if x.strip()]
        if ids:
            _run(["xdotool", "windowactivate", ids[-1]])
            return True
    return False


def focus_window(pid):
    """Jump to the editor window for pid. Returns True on success."""
    if not pid:
        return False
    if IS_WINDOWS:
        return _focus_window_win(pid)
    if IS_LINUX:
        return _focus_window_linux(pid)
    return False


def clamp_to_visible(x, y, w, h, margin=90):
    """Keep the window on a real monitor (handles unplugged/changed displays).

    Uses the virtual screen (bounding box of all monitors). Ensures at least
    `margin` px of the window stays on-screen, then clamps fully into bounds.
    """
    try:
        g = ctypes.windll.user32.GetSystemMetrics
        vx, vy, vw, vh = g(76), g(77), g(78), g(79)  # SM_*VIRTUALSCREEN
        if vw <= 0 or vh <= 0:
            return x, y
    except Exception:
        return x, y
    if x + margin > vx + vw:
        x = vx + vw - w
    if y + margin > vy + vh:
        y = vy + vh - h
    if x + w - margin < vx:
        x = vx
    if y + h - margin < vy:
        y = vy
    x = max(vx, min(x, vx + vw - w))
    y = max(vy, min(y, vy + vh - h))
    return x, y


class Tooltip:
    """A simple topmost hover tooltip (overrideredirect windows have none)."""

    def __init__(self, root):
        self.root = root
        self.tip = None

    def show(self, x, y, text):
        self.hide()
        self.tip = tw = tk.Toplevel(self.root)
        tw.overrideredirect(True)
        tw.attributes("-topmost", True)
        tk.Label(tw, text=text, bg="#000000", fg="#e8e8e8",
                 font=(UI_FONT, 8), justify="left", padx=8, pady=5,
                 wraplength=380, relief="solid", borderwidth=1).pack()
        tw.geometry(f"+{x + 14}+{y + 18}")

    def hide(self):
        if self.tip is not None:
            self.tip.destroy()
            self.tip = None


class Notifier:
    def __init__(self, root):
        self.root = root
        self.collapsed = False
        self._drag = (0, 0)
        self._sig = None          # signature of last rendered item set
        self._age_labels = []      # (label_widget, prefix, ts) for in-place age updates
        self.tooltip = Tooltip(root)

        root.overrideredirect(True)
        root.attributes("-topmost", True)
        try:
            root.attributes("-alpha", 0.96)
        except tk.TclError:
            pass
        root.configure(bg=BG)

        pos = load_pos() or (60, 60)
        x, y = clamp_to_visible(pos[0], pos[1], WIDTH, 28)
        self._start_pos = (x, y)
        root.geometry(f"+{x}+{y}")

        self.title_font = tkfont.Font(family=UI_FONT, size=10, weight="bold")
        self.proj_font = tkfont.Font(family=UI_FONT, size=10, weight="bold")
        self.sub_font = tkfont.Font(family=UI_FONT, size=8)

        # ---- title bar (drag handle) ----
        self.bar = tk.Frame(root, bg=BAR_BG, height=28)
        self.bar.pack(fill="x")
        self.bar.pack_propagate(False)

        self.title = tk.Label(self.bar, text="🔔 Claude", bg=BAR_BG, fg=FG,
                              font=self.title_font, anchor="w", padx=8)
        self.title.pack(side="left", fill="x", expand=True)

        self.btn_min = tk.Label(self.bar, text="—", bg=BAR_BG, fg=SUB,
                                font=self.title_font, padx=6, cursor="hand2")
        self.btn_min.pack(side="right")
        self.btn_close = tk.Label(self.bar, text="✕", bg=BAR_BG, fg=SUB,
                                  font=self.title_font, padx=8, cursor="hand2")
        self.btn_close.pack(side="right")

        for w in (self.bar, self.title):
            w.bind("<Button-1>", self.start_drag)
            w.bind("<B1-Motion>", self.on_drag)
            w.bind("<ButtonRelease-1>", self.end_drag)
        self.btn_close.bind("<Button-1>", lambda e: self.root.destroy())
        self.btn_min.bind("<Button-1>", lambda e: self.toggle_collapse())

        # ---- body ----
        self.body = tk.Frame(root, bg=BG)
        self.body.pack(fill="both", expand=True)

        root.geometry(f"{WIDTH}x28+{self._start_pos[0]}+{self._start_pos[1]}")
        self.refresh()

    # ---------- dragging ----------
    def start_drag(self, e):
        self._drag = (e.x_root - self.root.winfo_x(), e.y_root - self.root.winfo_y())

    def on_drag(self, e):
        x = e.x_root - self._drag[0]
        y = e.y_root - self._drag[1]
        self.root.geometry(f"+{x}+{y}")

    def end_drag(self, e):
        save_pos(self.root.winfo_x(), self.root.winfo_y())

    def toggle_collapse(self):
        self.collapsed = not self.collapsed
        self.refresh()

    # ---------- rendering ----------
    def refresh(self):
        state = load_state()
        items = [v for v in state.values() if v.get("status") in STATUS]
        # drop sessions whose editor window is gone (stale state after a reboot
        # or a window closed without firing SessionEnd). pid 0 = terminal CLI,
        # which we can't verify, so keep it. Windows checks open windows; other
        # platforms fall back to a process-alive check.
        live = live_window_pids()

        def _alive(v):
            pid = v.get("vscode_pid", 0)
            if not pid:
                return True
            return pid in live if live is not None else pid_alive(pid)

        items = [v for v in items if _alive(v)]
        # one row per window+page: collapse duplicate session ids (subagent /
        # rolled-over sessions for the same VSCode window) keeping the newest.
        collapsed = {}
        for v in items:
            key = (v.get("vscode_pid", 0), v.get("page") or v.get("cwd", ""))
            cur = collapsed.get(key)
            if cur is None or v.get("ts", 0) > cur.get("ts", 0):
                collapsed[key] = v
        items = list(collapsed.values())
        items.sort(key=lambda v: (STATUS[v["status"]][2], -v.get("ts", 0)))

        needs = sum(1 for v in items if v["status"] == "needs")
        done = sum(1 for v in items if v["status"] == "done")
        working = sum(1 for v in items if v["status"] == "working")
        if needs:
            self.title.config(text=f"🟡 Claude · {needs} need you")
        elif done:
            self.title.config(text=f"🟢 Claude · {done} your turn")
        elif working:
            self.title.config(text=f"🔴 Claude · {working} working")
        else:
            self.title.config(text="✓ Claude — all clear")

        # signature excludes age so steady state only updates the time labels,
        # avoiding a full rebuild (= flicker + tooltip churn) every poll.
        sig = (self.collapsed,) + tuple(
            (v["session_id"], v["status"], v.get("page"), v.get("folder"))
            for v in items
        )
        if sig == self._sig:
            for lbl, prefix, ts in self._age_labels:
                lbl.config(text=f"{prefix}  ·  {ago(ts)}")
            self.root.after(POLL_MS, self.refresh)
            return

        self._sig = sig
        self._age_labels = []
        self.tooltip.hide()
        for child in self.body.winfo_children():
            child.destroy()

        if self.collapsed or not items:
            if not items and not self.collapsed:
                tk.Label(self.body, text="No active sessions", bg=BG, fg=SUB,
                         font=self.sub_font, anchor="w", padx=10, pady=6).pack(fill="x")
            self.root.geometry(f"{WIDTH}x{self._needed_height()}")
            self.root.after(POLL_MS, self.refresh)
            return

        # only show the session-id tiebreaker when page names collide
        page_counts = {}
        for v in items:
            key = v.get("page") or v.get("project", "?")
            page_counts[key] = page_counts.get(key, 0) + 1

        for v in items:
            dot_color, label, _ = STATUS[v["status"]]
            row = tk.Frame(self.body, bg=BG)
            row.pack(fill="x", padx=2, pady=1)

            dot = tk.Label(row, text="●", bg=BG, fg=dot_color, font=self.proj_font)
            dot.pack(side="left", padx=(8, 6))

            text = tk.Frame(row, bg=BG)
            text.pack(side="left", fill="x", expand=True)

            # line 1: the page name (VSCode tab / conversation title)
            full_page = v.get("page") or v.get("project", "?")
            page = full_page if len(full_page) <= 34 else full_page[:33] + "…"
            tk.Label(text, text=page, bg=BG, fg=FG, font=self.proj_font,
                     anchor="w").pack(fill="x")

            # line 2: folder [· #id] · status · age  (age updated in place)
            folder = v.get("folder") or v.get("project", "")
            sid = v.get("session_id", "")
            short = f"  ·  #{sid[-4:]}" if page_counts.get(full_page, 0) > 1 and sid else ""
            prefix = f"{folder}{short}  ·  {label}"
            sub_lbl = tk.Label(text, text=f"{prefix}  ·  {ago(v.get('ts', 0))}",
                               bg=BG, fg=SUB, font=self.sub_font, anchor="w")
            sub_lbl.pack(fill="x")
            self._age_labels.append((sub_lbl, prefix, v.get("ts", 0)))

            # hover -> details; left-click -> focus window; right-click -> copy path
            cwd = v.get("cwd", "")
            pid = v.get("vscode_pid", 0)
            tip = (f"{full_page}\n{cwd}\nsession {sid}\n"
                   "left-click: focus window  ·  right-click: copy path")
            for w in (row, dot, text, *text.winfo_children()):
                w.configure(cursor="hand2")
                w.bind("<Button-1>", lambda e, p=pid, c=cwd: self.on_focus(p, c))
                w.bind("<Button-3>", lambda e, c=cwd: self.copy_path(c))
                w.bind("<Enter>", lambda e, t=tip: self.tooltip.show(e.x_root, e.y_root, t))
                w.bind("<Leave>", lambda e: self.tooltip.hide())

        self.root.geometry(f"{WIDTH}x{self._needed_height()}")
        self.root.after(POLL_MS, self.refresh)

    def _needed_height(self):
        self.body.update_idletasks()
        return 28 + max(self.body.winfo_reqheight(), 1)

    def on_focus(self, pid, cwd):
        """Left-click: jump to the VSCode window; fall back to copy if no window."""
        self.tooltip.hide()
        if not focus_window(pid):
            self.copy_path(cwd)

    def copy_path(self, path):
        if not path:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(path)
        old = self.title.cget("text")
        self.title.config(text="📋 Path copied")
        self.root.after(900, lambda: self.title.config(text=old))


def main():
    root = tk.Tk()
    root.title("Claude Notifier")
    Notifier(root)
    root.mainloop()


if __name__ == "__main__":
    main()
