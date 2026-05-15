@echo off
rem One-click launcher for the AI Servers monitor (no console window).
cd /d "%~dp0"
start "" "D:\AI\ComfyUI\venv\Scripts\pythonw.exe" "%~dp0monitor.py"
