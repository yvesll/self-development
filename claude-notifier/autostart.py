#!/usr/bin/env python3
"""Add / remove an autostart entry so the notifier launches at login.

    python autostart.py              # install
    python autostart.py --uninstall  # remove it

Windows: a Startup shortcut running `pythonw notifier.py` (no console).
Linux:   a ~/.config/autostart/claude-notifier.desktop entry.
"""
import os
import sys
import shutil
import subprocess

IS_WINDOWS = sys.platform.startswith("win")

HERE = os.path.dirname(os.path.abspath(__file__))
NOTIFIER = os.path.join(HERE, "notifier.py")

# ---- Windows ----
STARTUP = os.path.join(os.environ.get("APPDATA", ""),
                       "Microsoft", "Windows", "Start Menu", "Programs", "Startup")
LNK = os.path.join(STARTUP, "Claude Notifier.lnk")

# ---- Linux ----
DESKTOP = os.path.join(os.path.expanduser("~"), ".config", "autostart",
                       "claude-notifier.desktop")


def _pythonw_path():
    found = shutil.which("pythonw")
    if found:
        return found
    return os.path.join(os.path.dirname(sys.executable), "pythonw.exe")


def install_windows():
    os.makedirs(STARTUP, exist_ok=True)
    pyw = _pythonw_path()
    ps = f"""
$ws = New-Object -ComObject WScript.Shell
$s = $ws.CreateShortcut('{LNK}')
$s.TargetPath = '{pyw}'
$s.Arguments = '"{NOTIFIER}"'
$s.WorkingDirectory = '{HERE}'
$s.WindowStyle = 7
$s.Description = 'Claude Notifier'
$s.Save()
"""
    r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                       capture_output=True, text=True)
    if r.returncode != 0:
        print("failed to create shortcut:\n", r.stderr.strip())
        sys.exit(1)
    print(f"autostart installed -> {LNK}")
    print(f"  runs: {pyw} \"{NOTIFIER}\"")


def install_linux():
    py = shutil.which("python3") or shutil.which("python") or sys.executable
    os.makedirs(os.path.dirname(DESKTOP), exist_ok=True)
    content = (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=Claude Notifier\n"
        f"Exec={py} \"{NOTIFIER}\"\n"
        "X-GNOME-Autostart-enabled=true\n"
        "NoDisplay=false\n"
        "Terminal=false\n"
    )
    with open(DESKTOP, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"autostart installed -> {DESKTOP}")
    print(f"  runs: {py} \"{NOTIFIER}\"")


def uninstall():
    path = LNK if IS_WINDOWS else DESKTOP
    if os.path.exists(path):
        os.remove(path)
        print(f"autostart removed -> {path}")
    else:
        print("autostart entry not present, nothing to do")


if __name__ == "__main__":
    if "--uninstall" in sys.argv:
        uninstall()
    elif IS_WINDOWS:
        install_windows()
    else:
        install_linux()
