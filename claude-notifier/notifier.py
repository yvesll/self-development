#!/usr/bin/env python3
"""Always-on-top, draggable HUD showing which Claude Code sessions need you.

Reads the shared state written by hook.py and lists every session whose
status is 'waiting' (needs permission/attention) or 'idle' (your turn).
Frameless + topmost so it floats above your editor; drag the title bar to
move it (position is remembered). Launch with pythonw to hide the console.
"""
import os
import re
import json
import time
import ctypes
from ctypes import wintypes
import tkinter as tk
import tkinter.font as tkfont

STATE_DIR = os.path.join(os.path.expanduser("~"), ".claude", "notifier")
STATE_FILE = os.path.join(STATE_DIR, "state.json")
POS_FILE = os.path.join(STATE_DIR, "window.json")
POLL_MS = 600
HIDE_AFTER_S = 3600   # hide a row after this long with no update (reappears on update)

BG = "#1e1e1e"
BAR_BG = "#2d2d30"
BAR_ALERT = "#b8860b"   # amber — only blinks while a session needs your confirmation
FG = "#e8e8e8"
SUB = "#9a9a9a"
WIDTH = 320

STATUS = {
    # status: (dot color, label, priority — lower sorts first)
    # Traffic-light: red = AI busy (wait), yellow = needs your call, green = done.
    "needs": ("#faad14", "Needs you", 0),     # yellow — needs your judgment (blinks)
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


def live_window_pids():
    """Set of PIDs that currently own a visible, titled top-level window.

    Used to hide stale sessions whose VSCode window is gone (e.g. left over in
    state.json after a reboot or a window closed without firing SessionEnd).
    Returns None if it can't be determined (non-Windows) so callers keep all.
    """
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


def abbrev_model(model_id):
    """claude-sonnet-4-6 -> sonnet-4.6, claude-haiku-4-5-20251001 -> haiku-4.5,
    claude-opus-4-8[1m] -> opus-4.8 1m"""
    if not model_id:
        return ""
    m = model_id.lower()
    suffix = ""
    sm = re.search(r'\[([0-9a-z]+)\]\s*$', m)  # keep the [1m]-style variant tag
    if sm:
        suffix = " " + sm.group(1)
        m = m[:sm.start()]
    m = re.sub(r'^.*?claude-', '', m)
    m = re.sub(r'-\d{8}$', '', m)
    m = re.sub(r'-(\d+)-(\d+)$', r'-\1.\2', m)
    return m + suffix


def _session_title_from_transcript(cwd, sid):
    """Read the Claude Code transcript JSONL and return the ai-title, or ''."""
    if not cwd or not sid:
        return ""
    encoded = cwd.replace(":", "-").replace("\\", "-").replace("/", "-")
    path = os.path.join(os.path.expanduser("~"), ".claude", "projects", encoded, f"{sid}.jsonl")
    if not os.path.exists(path):
        return ""
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                if obj.get("type") == "ai-title":
                    return obj.get("aiTitle", "")
    except Exception:
        pass
    return ""


def fmt_tok(n):
    if n >= 1_000_000:
        return f"{n / 1_000_000:.2f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(int(n))


def _is_cloaked(hwnd):
    """True if the window is DWM-cloaked (e.g. parked on another virtual desktop)
    — such a window can't take focus, so we should skip it."""
    DWMWA_CLOAKED = 14
    val = wintypes.DWORD()
    try:
        ctypes.windll.dwmapi.DwmGetWindowAttribute(
            hwnd, DWMWA_CLOAKED, ctypes.byref(val), ctypes.sizeof(val))
        return val.value != 0
    except Exception:
        return False


def _window_title(hwnd):
    user32 = ctypes.windll.user32
    n = user32.GetWindowTextLengthW(hwnd)
    if n <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def _title_folder(title):
    """Folder segment of a VSCode title ('hook.py - self-development - Visual
    Studio Code' -> 'self-development'), with any ' [WSL: …]' tag stripped."""
    core = title
    for suffix in (" - Visual Studio Code", " - Code"):
        if core.endswith(suffix):
            core = core[: -len(suffix)]
            break
    core = re.sub(r'\s*\[[^\]]*\]\s*$', '', core)
    return core.rsplit(" - ", 1)[1].strip() if " - " in core else ""


def _hwnd_for_pid(pid, prefer=""):
    """Best top-level window owned by pid, preferring the one for project `prefer`.

    VSCode's main process is shared across all its windows, so every window has
    the same VSCODE_PID — picking by pid alone can land on the wrong project's
    window. When `prefer` (the session's project folder) is given, choose the
    window whose title folder matches it; otherwise fall back to the largest
    visible, titled, non-cloaked window (avoids hidden helper windows).
    """
    if not pid:
        return None
    user32 = ctypes.windll.user32
    found = []  # (area, hwnd, title)
    EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

    def cb(hwnd, _):
        dw = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(dw))
        if (dw.value == pid and user32.IsWindowVisible(hwnd)
                and user32.GetWindowTextLengthW(hwnd) > 0 and not _is_cloaked(hwnd)):
            r = wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(r))
            area = max(0, r.right - r.left) * max(0, r.bottom - r.top)
            found.append((area, hwnd, _window_title(hwnd)))
        return True

    user32.EnumWindows(EnumWindowsProc(cb), 0)
    if not found:
        return None
    if prefer:
        p = prefer.lower()
        match = [f for f in found if _title_folder(f[2]).lower() == p]
        if not match:
            match = [f for f in found if p in f[2].lower()]
        if match:
            match.sort(key=lambda t: t[0], reverse=True)
            return match[0][1]
        # known project but no matching window (its window was closed) -> don't
        # raise an unrelated window; signal 'not found' so the caller copies path.
        return None
    found.sort(key=lambda t: t[0], reverse=True)
    return found[0][1]


def focus_window(pid, prefer=""):
    """Raise the VSCode window for pid (project `prefer`) to the foreground *in place*.

    - Targets the window for the session's own project, not just any window of
      the shared VSCode PID — so the right page comes to the front.
    - Never repositions a normal/maximized window: we only un-minimize when the
      window is iconic, so a window living on a secondary monitor is brought to
      the front there instead of being yanked onto the primary screen.
    - Attaches our input thread to BOTH the current foreground thread and the
      target's thread, and momentarily zeroes the foreground-lock timeout, so
      SetForegroundWindow is honored every click (not just the first). No
      synthetic keypress and no window move/resize — just a clean raise.
    Returns True when a target window was found (so callers don't fall back to
    copying the path); the window is flashed if the raise didn't take.
    """
    hwnd = _hwnd_for_pid(pid, prefer=prefer)
    if not hwnd:
        return False
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    SW_RESTORE = 9
    SPI_GETFOREGROUNDLOCKTIMEOUT = 0x2000
    SPI_SETFOREGROUNDLOCKTIMEOUT = 0x2001
    old_timeout = wintypes.DWORD()
    lowered = False
    tids = set()
    this_tid = kernel32.GetCurrentThreadId()
    try:
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, SW_RESTORE)   # un-minimize only — no move/resize

        # Windows blocks a background app from stealing focus until its lock
        # timeout elapses; drop it to 0 for the call, restore it after. This is
        # what makes the raise reliable without moving the window.
        if user32.SystemParametersInfoW(
                SPI_GETFOREGROUNDLOCKTIMEOUT, 0, ctypes.byref(old_timeout), 0):
            user32.SystemParametersInfoW(
                SPI_SETFOREGROUNDLOCKTIMEOUT, 0, ctypes.c_void_p(0), 0)
            lowered = True

        fg = user32.GetForegroundWindow()
        fg_tid = user32.GetWindowThreadProcessId(fg, None) if fg else 0
        tgt_tid = user32.GetWindowThreadProcessId(hwnd, None)
        tids = {t for t in (fg_tid, tgt_tid) if t and t != this_tid}
        for t in tids:
            user32.AttachThreadInput(this_tid, t, True)
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
        user32.SetActiveWindow(hwnd)

        if user32.GetForegroundWindow() != hwnd:
            flash_window(hwnd)   # raise was blocked — blink the taskbar instead
        return True
    except Exception:
        return False
    finally:
        for t in tids:
            try:
                user32.AttachThreadInput(this_tid, t, False)
            except Exception:
                pass
        if lowered:
            user32.SystemParametersInfoW(
                SPI_SETFOREGROUNDLOCKTIMEOUT, 0,
                ctypes.c_void_p(old_timeout.value), 0)


class _FLASHWINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT),
        ("hwnd", wintypes.HWND),
        ("dwFlags", wintypes.DWORD),
        ("uCount", wintypes.UINT),
        ("dwTimeout", wintypes.DWORD),
    ]


def flash_window(hwnd, count=4):
    """Blink the target window's caption + taskbar button to draw the eye."""
    FLASHW_ALL = 0x3
    try:
        info = _FLASHWINFO(ctypes.sizeof(_FLASHWINFO), hwnd, FLASHW_ALL, count, 120)
        ctypes.windll.user32.FlashWindowEx(ctypes.byref(info))
    except Exception:
        pass


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
                 font=("Segoe UI", 8), justify="left", padx=8, pady=5,
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
        self.blink_on = False
        self._needs_attention = False
        self._acked = {}           # sid -> ts the user clicked/acknowledged
        self._drag = (0, 0)
        self._sig = None          # signature of last rendered item set
        self._age_labels = []      # (label_widget, prefix, ts) for in-place age updates
        self._title_cache = {}     # sid -> ai-title from transcript (positive results only)
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

        self.title_font = tkfont.Font(family="Segoe UI", size=10, weight="bold")
        self.proj_font = tkfont.Font(family="Segoe UI", size=10, weight="bold")
        self.sub_font = tkfont.Font(family="Segoe UI", size=8)

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
        self.blink()

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
        # hide rows idle for over an hour; the entry stays in state, so the row
        # reappears as soon as a new hook event refreshes its timestamp.
        now = time.time()
        items = [v for v in items if now - v.get("ts", 0) <= HIDE_AFTER_S]
        # drop sessions whose VSCode window is gone (stale state after a reboot
        # or a window closed without firing SessionEnd). pid 0 = terminal CLI,
        # which we can't verify, so keep it.
        live = live_window_pids()
        if live is not None:
            items = [v for v in items if not v.get("vscode_pid") or v["vscode_pid"] in live]
        # one row per session id: every concurrent session shows, even several
        # sharing the same VSCode window + active tab. (Keying by session id only
        # de-dups exact repeats; it never merges distinct sessions together.)
        collapsed = {}
        for v in items:
            key = v.get("session_id") or id(v)
            cur = collapsed.get(key)
            if cur is None or v.get("ts", 0) > cur.get("ts", 0):
                collapsed[key] = v
        items = list(collapsed.values())
        items.sort(key=lambda v: (STATUS[v["status"]][2], -v.get("ts", 0)))

        # Lazily resolve session titles for sessions that don't have one yet.
        # Positive results are cached; negatives retry each poll so a title that
        # appears mid-session is picked up within one poll interval.
        for v in items:
            if not v.get("session_title"):
                sid = v.get("session_id", "")
                if sid and sid not in self._title_cache:
                    found = _session_title_from_transcript(v.get("cwd", ""), sid)
                    if found:
                        self._title_cache[sid] = found
                if sid and sid in self._title_cache:
                    v["session_title"] = self._title_cache[sid]

        # blink only for "needs" sessions you haven't acknowledged yet: clicking
        # a row marks it seen (at its current ts) so the bar stops nagging; a
        # fresh Notification bumps ts and makes it blink again.
        self._needs_attention = any(
            v["status"] == "needs" and self._acked.get(v.get("session_id")) != v.get("ts")
            for v in items
        )

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
            (v["session_id"], v["status"], v.get("session_title"), v.get("page"),
             v.get("folder"), (v.get("stats") or {}).get("model_id") or v.get("model"))
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
            self._apply_geometry(self._needed_height())
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

            # line 1: session title from Claude Code, falling back to VSCode tab name
            full_page = v.get("session_title") or v.get("page") or v.get("project", "?")
            page = full_page if len(full_page) <= 34 else full_page[:33] + "…"
            tk.Label(text, text=page, bg=BG, fg=FG, font=self.proj_font,
                     anchor="w").pack(fill="x")

            # line 2: folder [· #id] · model · status · age  (age updated in place)
            folder = v.get("folder") or v.get("project", "")
            sid = v.get("session_id", "")
            raw_page = v.get("page") or v.get("project", "?")
            short = f"  ·  #{sid[-4:]}" if page_counts.get(raw_page, 0) > 1 and sid else ""
            model_str = abbrev_model((v.get("stats") or {}).get("model_id") or v.get("model", ""))
            model_part = f"  ·  {model_str}" if model_str else ""
            prefix = f"{folder}{short}{model_part}  ·  {label}"
            sub_lbl = tk.Label(text, text=f"{prefix}  ·  {ago(v.get('ts', 0))}",
                               bg=BG, fg=SUB, font=self.sub_font, anchor="w")
            sub_lbl.pack(fill="x")
            self._age_labels.append((sub_lbl, prefix, v.get("ts", 0)))

            # line 3: token/cost stats
            stats = v.get("stats")
            if stats and (stats.get("in_tok", 0) or stats.get("out_tok", 0)):
                s_text = (f"  ↑{fmt_tok(stats['in_tok'])} ↓{fmt_tok(stats['out_tok'])}"
                          f"  ~${stats['cost']:.3f}")
                tk.Label(text, text=s_text, bg=BG, fg="#606060", font=self.sub_font,
                         anchor="w").pack(fill="x")

            # hover -> details; left-click -> focus window; right-click -> copy path
            cwd = v.get("cwd", "")
            pid = v.get("vscode_pid", 0)
            tip_stats = ""
            if stats and (stats.get("in_tok", 0) or stats.get("out_tok", 0)):
                tip_stats = (f"\n{stats.get('model_id', '')}  "
                             f"↑{fmt_tok(stats['in_tok'])} ↓{fmt_tok(stats['out_tok'])}"
                             f"  ~${stats['cost']:.4f}")
            tip = (f"{full_page}\n{cwd}\nsession {sid}{tip_stats}\n"
                   f"left-click: focus window  ·  right-click: copy path")
            for w in (row, dot, text, *text.winfo_children()):
                w.configure(cursor="hand2")
                w.bind("<Button-1>", lambda e, p=pid, c=cwd, s=sid: self.on_focus(p, c, s))
                w.bind("<Button-3>", lambda e, c=cwd: self.copy_path(c))
                w.bind("<Enter>", lambda e, t=tip: self.tooltip.show(e.x_root, e.y_root, t))
                w.bind("<Leave>", lambda e: self.tooltip.hide())

        self._apply_geometry(self._needed_height())
        self.root.after(POLL_MS, self.refresh)

    def _needed_height(self):
        self.body.update_idletasks()
        return 28 + max(self.body.winfo_reqheight(), 1)

    def _apply_geometry(self, height):
        """Resize to fit content, then nudge the window so the FULL height stays
        on-screen — otherwise a tall list (many sessions) spills past the screen
        bottom and those extra rows become invisible."""
        self.root.update_idletasks()
        x, y = self.root.winfo_x(), self.root.winfo_y()
        x, y = clamp_to_visible(x, y, WIDTH, height)
        self.root.geometry(f"{WIDTH}x{height}+{x}+{y}")

    def on_focus(self, pid, cwd, sid=""):
        """Left-click: jump to the session's VSCode window; copy path if none.

        Also marks the row acknowledged (at its current ts) so the title bar
        stops blinking — until a newer Notification bumps the ts.
        """
        self.tooltip.hide()
        if sid:
            v = load_state().get(sid)
            if v:
                self._acked[sid] = v.get("ts")   # refresh re-checks blink next poll
        proj = os.path.basename(cwd.rstrip("/\\")) if cwd else ""
        if not focus_window(pid, prefer=proj):
            self.copy_path(cwd)

    def copy_path(self, path):
        if not path:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(path)
        old = self.title.cget("text")
        self.title.config(text="📋 Path copied")
        self.root.after(900, lambda: self.title.config(text=old))

    # ---------- attention blink ----------
    def blink(self):
        # Only the yellow "needs you" (Notification) state blinks; "done" and
        # "working" stay calm. refresh() sets the flag, excluding rows you've
        # already clicked so the bar stops nagging once acknowledged.
        if self._needs_attention:
            self.blink_on = not self.blink_on
            bg = BAR_ALERT if self.blink_on else BAR_BG
            for w in (self.bar, self.title, self.btn_min, self.btn_close):
                w.config(bg=bg)
        elif self.blink_on or self.bar.cget("bg") != BAR_BG:
            self.blink_on = False
            for w in (self.bar, self.title, self.btn_min, self.btn_close):
                w.config(bg=BAR_BG)
        self.root.after(700, self.blink)


def main():
    root = tk.Tk()
    root.title("Claude Notifier")
    Notifier(root)
    root.mainloop()


if __name__ == "__main__":
    main()
