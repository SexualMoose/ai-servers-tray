"""
AI Monitor Installer
====================
Single-executable installer for the AI Servers Monitor. Run it to install
or uninstall.

When PyInstaller-bundled into ai-monitor-installer.exe, it carries the
monitor source code as an embedded data file (see PyInstaller --add-data
in build-installers.bat), so it's truly standalone — the user can move
the .exe anywhere.

Actions:
  install    - lay down monitor files, register a logon-trigger scheduled
               task, start the monitor process now
  uninstall  - kill monitor, remove the scheduled task, optionally delete
               files
  status     - report current install state
"""
from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
import textwrap
import time
from pathlib import Path

# ---- Paths the monitor lives at ---------------------------------------------
INSTALL_DIR = Path(r"D:\AI\TrayManager")
MONITOR_PY = INSTALL_DIR / "monitor.py"
MONITOR_BAT = INSTALL_DIR / "start-monitor.bat"
MONITOR_LOG = INSTALL_DIR / "monitor.log"
MONITOR_CFG = INSTALL_DIR / "monitor.json"
MONITOR_STATE = INSTALL_DIR / "monitor_state.json"
HEARTBEAT_PORT = 9300
TASK_NAME = "AI Servers Monitor"
VENV_PYTHONW = Path(r"D:\AI\ComfyUI\venv\Scripts\pythonw.exe")
VENV_PIP = Path(r"D:\AI\ComfyUI\venv\Scripts\pip.exe")

START_BAT = """@echo off
rem One-click launcher for the AI Servers monitor (no console window).
cd /d "%~dp0"
start "" "D:\\AI\\ComfyUI\\venv\\Scripts\\pythonw.exe" "%~dp0monitor.py"
"""


# ---- PyInstaller-aware resource loader -------------------------------------
def _bundled_resource(name: str) -> Path:
    """Return path to a file that PyInstaller bundled alongside this script
    via --add-data. When running as a normal .py (development), the file
    is expected next to the installer script."""
    if hasattr(sys, "_MEIPASS"):  # PyInstaller temp-extract dir
        return Path(sys._MEIPASS) / name
    return Path(__file__).resolve().parent / name


# ---- State probes -----------------------------------------------------------
def _monitor_running() -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.3)
    try:
        return s.connect_ex(("127.0.0.1", HEARTBEAT_PORT)) == 0
    finally:
        s.close()


def _task_exists() -> bool:
    r = subprocess.run(["schtasks.exe", "/Query", "/TN", TASK_NAME],
                       capture_output=True, text=True,
                       creationflags=subprocess.CREATE_NO_WINDOW)
    return r.returncode == 0


def status() -> None:
    print("AI Monitor install status")
    print("-------------------------")
    print(f"monitor.py present   : {MONITOR_PY.exists()}")
    print(f"start-monitor.bat    : {MONITOR_BAT.exists()}")
    print(f"monitor.json         : {MONITOR_CFG.exists()}")
    print(f"scheduled task '{TASK_NAME}': {'yes' if _task_exists() else 'no'}")
    print(f"monitor process up   : {'yes (port 9300 open)' if _monitor_running() else 'no'}")
    print(f"venv pythonw         : {'OK' if VENV_PYTHONW.exists() else 'MISSING - install ComfyUI venv first'}")


# ---- Install / uninstall ----------------------------------------------------
def _ensure_websocket_client() -> None:
    if not VENV_PIP.exists():
        print("[!] venv pip missing; cannot install websocket-client dependency")
        return
    print("[*] ensuring websocket-client is installed in the venv...")
    r = subprocess.run([str(VENV_PIP), "install", "--quiet", "websocket-client"],
                       capture_output=True, text=True,
                       creationflags=subprocess.CREATE_NO_WINDOW)
    if r.returncode != 0:
        print(f"    pip install failed: {r.stderr.strip()}")
    else:
        print("    websocket-client OK")


def _write_files() -> None:
    INSTALL_DIR.mkdir(parents=True, exist_ok=True)
    src = _bundled_resource("monitor.py")
    if not src.exists():
        print(f"[!] bundled monitor.py missing at {src}")
        sys.exit(2)
    MONITOR_PY.write_bytes(src.read_bytes())
    print(f"[*] wrote {MONITOR_PY}")
    MONITOR_BAT.write_text(START_BAT, encoding="ascii")
    print(f"[*] wrote {MONITOR_BAT}")


def _register_task() -> bool:
    tr = f'\\"{VENV_PYTHONW}\\" \\"{MONITOR_PY}\\"'
    r = subprocess.run(
        ["schtasks.exe", "/Create", "/F",
         "/SC", "ONLOGON", "/RL", "LIMITED",
         "/TN", TASK_NAME, "/TR", tr],
        capture_output=True, text=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if r.returncode != 0:
        print(f"[!] schtasks create failed (Access denied? need elevated terminal): {r.stderr.strip()}")
        return False
    print(f"[*] scheduled task '{TASK_NAME}' registered (fires at next logon)")
    return True


def _start_monitor() -> None:
    if _monitor_running():
        print("[*] monitor already running (port 9300 open)")
        return
    if not VENV_PYTHONW.exists():
        print(f"[!] {VENV_PYTHONW} missing; cannot launch")
        return
    subprocess.Popen([str(VENV_PYTHONW), str(MONITOR_PY)],
                     creationflags=subprocess.CREATE_NO_WINDOW)
    for _ in range(20):
        time.sleep(0.3)
        if _monitor_running():
            print("[*] monitor started (heartbeat port 9300 open)")
            return
    print("[!] monitor process launched but heartbeat not responding yet; check monitor.log")


def _stop_monitor() -> None:
    # Find pythonw processes whose cmdline references monitor.py and kill.
    try:
        import psutil
    except ImportError:
        print("[!] psutil not in this Python; skipping process kill (only deleting task)")
        return
    killed = 0
    for p in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            cmd = " ".join(p.info["cmdline"] or [])
            if "monitor.py" in cmd and (p.info["name"] or "").lower().startswith("python"):
                p.kill()
                killed += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    print(f"[*] killed {killed} monitor process(es)")


def _delete_task() -> None:
    r = subprocess.run(["schtasks.exe", "/Delete", "/F", "/TN", TASK_NAME],
                       capture_output=True, text=True,
                       creationflags=subprocess.CREATE_NO_WINDOW)
    if r.returncode == 0:
        print(f"[*] scheduled task '{TASK_NAME}' removed")
    elif "cannot find" in (r.stderr or "").lower():
        print(f"[*] no scheduled task to remove")
    else:
        print(f"[!] schtasks delete failed: {r.stderr.strip()}")


def do_install(yes: bool) -> int:
    print("==> Installing AI Monitor")
    if not VENV_PYTHONW.exists():
        print(f"[!] {VENV_PYTHONW} missing - install ComfyUI's venv first")
        return 2
    _ensure_websocket_client()
    _write_files()
    _register_task()
    _start_monitor()
    print()
    print("Install complete. Open D:\\AI\\TrayManager\\monitor.json to see")
    print("your ntfy topic, then subscribe to it from the ntfy phone app.")
    return 0


def do_uninstall(yes: bool) -> int:
    print("==> Uninstalling AI Monitor")
    _stop_monitor()
    _delete_task()
    if yes:
        delete_files = True
    else:
        ans = input("Also delete monitor.py / monitor.json / state / log? [y/N]: ").strip().lower()
        delete_files = ans == "y"
    if delete_files:
        for p in (MONITOR_PY, MONITOR_BAT, MONITOR_CFG, MONITOR_STATE, MONITOR_LOG):
            try:
                p.unlink()
                print(f"[*] deleted {p}")
            except FileNotFoundError:
                pass
            except Exception as e:
                print(f"[!] could not delete {p}: {e!r}")
    else:
        print("[*] files left in place under D:\\AI\\TrayManager\\")
    return 0


# ---- CLI / interactive menu -------------------------------------------------
def _menu() -> str:
    is_installed = MONITOR_PY.exists()
    is_running = _monitor_running()
    print()
    print(textwrap.dedent(f"""\
        ===========================================
                AI Servers Monitor installer
        ===========================================
        monitor.py present : {is_installed}
        monitor running    : {is_running}
        scheduled task     : {'yes' if _task_exists() else 'no'}
        """))
    if is_installed:
        print("  [R] Reinstall (overwrite files, keep config)")
        print("  [U] Uninstall")
    else:
        print("  [I] Install")
    print("  [S] Status only")
    print("  [Q] Quit")
    return input("> Choice: ").strip().lower()


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="ai-monitor-installer")
    p.add_argument("action", nargs="?",
                   choices=["install", "uninstall", "status", "reinstall"],
                   help="run a single action then exit (omit for menu)")
    p.add_argument("--yes", action="store_true",
                   help="don't prompt for confirmation (uninstall deletes files)")
    args = p.parse_args(argv)

    if args.action == "install":
        return do_install(args.yes)
    if args.action == "reinstall":
        do_uninstall(yes=False)
        return do_install(args.yes)
    if args.action == "uninstall":
        return do_uninstall(args.yes)
    if args.action == "status":
        status()
        return 0

    # Interactive menu
    while True:
        c = _menu()
        if c in ("i",):
            do_install(yes=False)
        elif c in ("r",):
            do_uninstall(yes=False)
            do_install(yes=False)
        elif c in ("u",):
            do_uninstall(yes=False)
        elif c in ("s",):
            status()
        elif c in ("q", "x", ""):
            return 0
        else:
            print("?")
        print()
        try:
            input("Press Enter to continue...")
        except EOFError:
            return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
