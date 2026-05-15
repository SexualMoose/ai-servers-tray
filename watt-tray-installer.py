"""
Watt-Tray Installer
===================
Toggles the WattTray scheduled task and start/stops the process. Assumes
the watt-tray application files already exist at C:\\Users\\Tyler\\watt-tray\\
(this installer is for autostart wiring, not bundling the .NET DLLs).

Actions:
  install    - register the logon-trigger scheduled task, start now
  uninstall  - stop process, remove task; leaves files on disk
  status     - report install state
"""
from __future__ import annotations

import argparse
import socket
import subprocess
import sys
import textwrap
import time
from pathlib import Path

WATT_DIR = Path(r"C:\Users\Tyler\watt-tray")
WATT_PYTHONW = WATT_DIR / "venv" / "Scripts" / "pythonw.exe"
WATT_ENTRY = WATT_DIR / "watt_tray.pyw"
TASK_NAME = "WattTray"


def _task_exists() -> bool:
    r = subprocess.run(["schtasks.exe", "/Query", "/TN", TASK_NAME],
                       capture_output=True, text=True,
                       creationflags=subprocess.CREATE_NO_WINDOW)
    return r.returncode == 0


def _running() -> bool:
    try:
        import psutil
    except ImportError:
        return False
    for p in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            cmd = " ".join(p.info["cmdline"] or [])
            if "watt_tray" in cmd and (p.info["name"] or "").lower().startswith("python"):
                return True
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return False


def status() -> None:
    print("Watt-Tray install status")
    print("------------------------")
    print(f"watt-tray dir        : {WATT_DIR} ({'exists' if WATT_DIR.exists() else 'MISSING'})")
    print(f"watt_tray.pyw        : {'OK' if WATT_ENTRY.exists() else 'MISSING'}")
    print(f"watt-tray venv pythonw: {'OK' if WATT_PYTHONW.exists() else 'MISSING'}")
    print(f"scheduled task '{TASK_NAME}': {'yes' if _task_exists() else 'no'}")
    print(f"process running      : {'yes' if _running() else 'no'}")


def _register_task() -> bool:
    tr = f'\\"{WATT_PYTHONW}\\" \\"{WATT_ENTRY}\\"'
    r = subprocess.run(
        ["schtasks.exe", "/Create", "/F",
         "/SC", "ONLOGON", "/RL", "LIMITED",
         "/TN", TASK_NAME, "/TR", tr],
        capture_output=True, text=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if r.returncode != 0:
        print(f"[!] schtasks create failed (need elevated terminal): {r.stderr.strip()}")
        return False
    print(f"[*] scheduled task '{TASK_NAME}' registered")
    return True


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


def _start_now() -> None:
    if _running():
        print("[*] watt-tray already running")
        return
    if not WATT_PYTHONW.exists() or not WATT_ENTRY.exists():
        print(f"[!] missing pythonw or entry point; cannot start")
        return
    subprocess.Popen([str(WATT_PYTHONW), str(WATT_ENTRY)],
                     cwd=str(WATT_DIR),
                     creationflags=subprocess.CREATE_NO_WINDOW)
    time.sleep(1.5)
    print("[*] watt-tray started" if _running() else "[*] watt-tray launched (verify icon appeared)")


def _stop_now() -> None:
    try:
        import psutil
    except ImportError:
        print("[!] psutil missing; cannot kill process")
        return
    killed = 0
    for p in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            cmd = " ".join(p.info["cmdline"] or [])
            if "watt_tray" in cmd and (p.info["name"] or "").lower().startswith("python"):
                p.kill()
                killed += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    print(f"[*] killed {killed} watt-tray process(es)")


def do_install(yes: bool) -> int:
    print("==> Installing Watt-Tray autostart")
    if not WATT_ENTRY.exists():
        print(f"[!] {WATT_ENTRY} missing - install watt-tray app first")
        return 2
    _register_task()
    _start_now()
    return 0


def do_uninstall(yes: bool) -> int:
    print("==> Uninstalling Watt-Tray autostart")
    _stop_now()
    _delete_task()
    print("[*] watt-tray files left on disk at " + str(WATT_DIR))
    return 0


def _menu() -> str:
    print()
    print(textwrap.dedent(f"""\
        ===========================================
                  Watt-Tray installer
        ===========================================
        watt_tray.pyw    : {'present' if WATT_ENTRY.exists() else 'MISSING'}
        process running  : {_running()}
        scheduled task   : {'yes' if _task_exists() else 'no'}
        """))
    if _task_exists() or _running():
        print("  [U] Uninstall (stop process + remove task)")
        print("  [R] Reinstall (re-register task)")
    else:
        print("  [I] Install (register task + start)")
    print("  [S] Status only")
    print("  [Q] Quit")
    return input("> Choice: ").strip().lower()


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="watt-tray-installer")
    p.add_argument("action", nargs="?",
                   choices=["install", "uninstall", "status", "reinstall"])
    p.add_argument("--yes", action="store_true")
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

    while True:
        c = _menu()
        if c == "i":
            do_install(False)
        elif c == "r":
            do_uninstall(False)
            do_install(False)
        elif c == "u":
            do_uninstall(False)
        elif c == "s":
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
