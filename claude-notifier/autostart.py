#!/usr/bin/env python3
"""Add / remove a Windows Startup shortcut so the notifier launches at login.

    python autostart.py              # install (create the Startup shortcut)
    python autostart.py --uninstall  # remove it

The shortcut runs `pythonw notifier.py` (no console window) from this folder.
"""
import os
import sys
import shutil
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
NOTIFIER = os.path.join(HERE, "notifier.py")
STARTUP = os.path.join(os.environ.get("APPDATA", ""),
                       "Microsoft", "Windows", "Start Menu", "Programs", "Startup")
LNK = os.path.join(STARTUP, "Claude Notifier.lnk")


def pythonw_path():
    found = shutil.which("pythonw")
    if found:
        return found
    return os.path.join(os.path.dirname(sys.executable), "pythonw.exe")


def install():
    os.makedirs(STARTUP, exist_ok=True)
    pyw = pythonw_path()
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


def uninstall():
    if os.path.exists(LNK):
        os.remove(LNK)
        print(f"autostart removed -> {LNK}")
    else:
        print("autostart shortcut not present, nothing to do")


if __name__ == "__main__":
    if "--uninstall" in sys.argv:
        uninstall()
    else:
        install()
