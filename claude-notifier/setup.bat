@echo off
rem One-click setup for a new machine: wire the hooks + launch the HUD.
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo [!] Python 3 was not found on PATH. Install it from https://www.python.org
  echo     ^(check "Add python.exe to PATH" during install^), then run setup.bat again.
  pause
  exit /b 1
)

echo Installing Claude Notifier hooks into your global ~/.claude/settings.json ...
python install-hooks.py || goto :err

echo.
echo Launching the floating window ...
start "" pythonw "%~dp0notifier.py"

echo.
echo Done.
echo  - Restart any ALREADY-OPEN Claude Code sessions so the hooks take effect.
echo  - Optional: run "python autostart.py" to launch the window at every login.
echo  - Green = Claude is working, Yellow = a session needs you (title bar blinks).
pause
exit /b 0

:err
echo.
echo [!] Setup failed. See the message above.
pause
exit /b 1
