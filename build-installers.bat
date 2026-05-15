@echo off
rem Build single-file installer .exes via PyInstaller.
rem Outputs land in D:\AI\TrayManager\dist\
setlocal
cd /d "%~dp0"

set "PY=D:\AI\ComfyUI\venv\Scripts\python.exe"
set "PYI=D:\AI\ComfyUI\venv\Scripts\pyinstaller.exe"

if not exist "%PYI%" (
  echo Installing pyinstaller...
  "%PY%" -m pip install --quiet pyinstaller || goto error
)

rem ---- AI Monitor installer ------------------------------------------------
rem --add-data embeds monitor.py so the .exe carries the payload.
echo === Building ai-monitor-installer.exe ===
"%PYI%" --onefile --console --clean ^
  --name ai-monitor-installer ^
  --add-data "monitor.py;." ^
  --hidden-import psutil ^
  ai-monitor-installer.py || goto error

rem ---- Watt-Tray installer -------------------------------------------------
echo === Building watt-tray-installer.exe ===
"%PYI%" --onefile --console --clean ^
  --name watt-tray-installer ^
  --hidden-import psutil ^
  watt-tray-installer.py || goto error

echo.
echo === Done. Executables:
echo   %CD%\dist\ai-monitor-installer.exe
echo   %CD%\dist\watt-tray-installer.exe
goto :eof

:error
echo BUILD FAILED
exit /B 1
