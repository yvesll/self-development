#!/usr/bin/env sh
# One-click setup for Linux/macOS: wire the hooks + launch the HUD.
cd "$(dirname "$0")" || exit 1

if ! command -v python3 >/dev/null 2>&1; then
  echo "[!] python3 not found. Install Python 3 (with tkinter), then re-run."
  exit 1
fi

echo "Installing Claude Notifier hooks into ~/.claude/settings.json ..."
python3 install-hooks.py || exit 1

echo "Launching the floating window ..."
(python3 notifier.py >/dev/null 2>&1 &)

echo ""
echo "Done."
echo " - Restart any ALREADY-OPEN Claude Code sessions so the hooks take effect."
echo " - On X11, install 'wmctrl' (or 'xdotool') for page titles + click-to-focus."
echo "   e.g. sudo apt install wmctrl    (Wayland can't query windows; folder name is used)"
echo " - Optional: python3 autostart.py  to launch the window at every login."
echo " - Colors: Red = AI working, Yellow = needs you, Green = done (your turn)."
