@echo off
rem One-click launcher for the AI Servers tray icon.
rem Uses pythonw.exe so no console window appears.
cd /d "%~dp0"
start "" "D:\AI\ComfyUI\venv\Scripts\pythonw.exe" "%~dp0tray.py"
