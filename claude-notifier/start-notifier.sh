#!/usr/bin/env sh
# Launch the floating notifier in the background (Linux/macOS).
cd "$(dirname "$0")" || exit 1
(python3 notifier.py >/dev/null 2>&1 &)
