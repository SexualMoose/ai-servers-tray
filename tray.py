"""
AI Servers Tray Manager
=======================
Single tray icon to start/stop/kill ComfyUI x2, LM Studio, OpenCode web.
Each server has its own submenu + autostart-on-boot toggle.
Tooltip shows live running state + IPs/ports.

Config persisted in config.json (next to this script).
Autostart toggle for the tray itself = Run-key entry under HKCU.

Run via tray.pyw (no console) or `pythonw tray.py`.

Performance notes
-----------------
All UI callbacks (label text, enabled, checked, tooltip) read from a
shared `State` cache that is refreshed by a background poll thread.
The menu therefore opens instantly: no socket probes, no subprocess
calls happen on the pystray UI thread. Start/Stop/Kill actions run in
daemon threads so the menu returns immediately even though terminate
waits up to ~3 s for processes to die.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
import webbrowser
import winreg
from pathlib import Path
from typing import Callable

import psutil
from PIL import Image, ImageDraw
import pystray
from pystray import Menu, MenuItem


SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG_PATH = SCRIPT_DIR / "config.json"
LOG_PATH = SCRIPT_DIR / "tray.log"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
TRAY_RUN_NAME = "AI_Servers_Tray"
STARTUP_LNK_NAME = "AI Servers Tray.lnk"

# Poll cadence + caches
POLL_INTERVAL = 2.0          # state refresh tick (s)
TS_IP_MAX_AGE = 60.0         # tailscale IP cache lifetime (s)
SOCKET_PROBE_TIMEOUT = 0.1   # localhost connect probe timeout (s)


def _log(msg: str) -> None:
    """Append a timestamped line to tray.log. pythonw.exe has no console
    so stderr is /dev/null — a file log is the only way to see failures."""
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
    except Exception:
        pass


# ---- Server definitions -----------------------------------------------------

class Server:
    def __init__(self, key: str, name: str, port: int, launch_args: list[str],
                 launch_cwd: str | None = None):
        self.key = key
        self.name = name
        self.port = port
        self.launch_args = launch_args
        self.launch_cwd = launch_cwd

    def is_running(self) -> bool:
        # Cheap TCP-connect check on the configured port. Localhost RST is
        # near-instant on a closed port; the timeout only matters if the
        # port is filtered (e.g. firewall) or AV is intercepting.
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(SOCKET_PROBE_TIMEOUT)
            return s.connect_ex(("127.0.0.1", self.port)) == 0

    def listener_pids(self) -> list[int]:
        out = []
        try:
            for c in psutil.net_connections(kind="inet"):
                if c.laddr and c.laddr.port == self.port and c.status == "LISTEN":
                    if c.pid:
                        out.append(c.pid)
        except (psutil.AccessDenied, PermissionError):
            pass
        return list(set(out))

    def start(self) -> None:
        if self.is_running():
            return
        # CREATE_NO_WINDOW alone: hides the console window for GUI children
        # AND still gives cmd.exe / a .bat a working console (so the script
        # can actually run). DETACHED_PROCESS would deny cmd.exe its console
        # and the .bat would silently die at the first redirection.
        try:
            subprocess.Popen(
                self.launch_args,
                cwd=self.launch_cwd,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        except Exception as e:
            _log(f"[{self.key}] launch failed: {e!r}\n"
                 f"  args={self.launch_args!r} cwd={self.launch_cwd!r}")

    def stop(self) -> None:
        for pid in self.listener_pids():
            try:
                p = psutil.Process(pid)
                self._terminate_tree(p, force=False)
            except psutil.NoSuchProcess:
                pass

    def kill(self) -> None:
        for pid in self.listener_pids():
            try:
                p = psutil.Process(pid)
                self._terminate_tree(p, force=True)
            except psutil.NoSuchProcess:
                pass

    @staticmethod
    def _terminate_tree(p: psutil.Process, force: bool) -> None:
        try:
            children = p.children(recursive=True)
        except psutil.NoSuchProcess:
            children = []
        targets = children + [p]
        for t in targets:
            try:
                t.kill() if force else t.terminate()
            except psutil.NoSuchProcess:
                pass
        gone, alive = psutil.wait_procs(targets, timeout=3)
        for t in alive:
            try:
                t.kill()
            except psutil.NoSuchProcess:
                pass


SERVERS: list[Server] = [
    Server(
        key="comfyui_gpu0",
        name="ComfyUI 6950 XT (8188)",
        port=8188,
        launch_args=["cmd.exe", "/c", r"D:\AI\ComfyUI\launch-gpu0.bat"],
        launch_cwd=r"D:\AI\ComfyUI",
    ),
    Server(
        key="comfyui_gpu1",
        name="ComfyUI 6900 XT (8189)",
        port=8189,
        launch_args=["cmd.exe", "/c", r"D:\AI\ComfyUI\launch-gpu1.bat"],
        launch_cwd=r"D:\AI\ComfyUI",
    ),
    Server(
        key="lm_studio",
        name="LM Studio API (1234)",
        port=1234,
        launch_args=[r"C:\Users\Tyler\.lmstudio\bin\lms.exe", "server", "start", "--port", "1234"],
    ),
    Server(
        key="opencode_web",
        name="OpenCode Web (7777)",
        port=7777,
        launch_args=[r"D:\AI\opencode\opencode.exe", "web", "--hostname", "0.0.0.0", "--port", "7777"],
    ),
    Server(
        key="monitor",
        name="AI Monitor (alerts on 9300)",
        port=9300,
        launch_args=[r"D:\AI\ComfyUI\venv\Scripts\pythonw.exe",
                     r"D:\AI\TrayManager\monitor.py"],
        launch_cwd=r"D:\AI\TrayManager",
    ),
]


# ---- Config -----------------------------------------------------------------
DEFAULT_CONFIG: dict = {
    "autostart_on_login": {s.key: False for s in SERVERS},
    "tray_autostart": True,
}


def load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            # utf-8-sig silently strips a leading BOM (PowerShell 5.1's
            # `Set-Content -Encoding UTF8` writes one and json.loads would
            # otherwise blow up and we'd silently fall back to defaults).
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))
            for k, v in DEFAULT_CONFIG.items():
                if k not in data:
                    data[k] = v
            for s in SERVERS:
                data["autostart_on_login"].setdefault(s.key, False)
            return data
        except Exception as e:
            _log(f"load_config failed, using defaults: {e!r}")
    return dict(DEFAULT_CONFIG)


def save_config(cfg: dict) -> None:
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


# ---- Tailscale IP discovery -------------------------------------------------
def tailscale_ip() -> str | None:
    for ts in (r"C:\Program Files\Tailscale\tailscale.exe",
               r"C:\Program Files (x86)\Tailscale\tailscale.exe"):
        if os.path.isfile(ts):
            try:
                out = subprocess.run([ts, "ip", "-4"], capture_output=True,
                                     text=True, timeout=3,
                                     creationflags=subprocess.CREATE_NO_WINDOW)
                if out.returncode == 0:
                    ip = out.stdout.strip().splitlines()[0]
                    if ip:
                        return ip
            except Exception:
                pass
    return None


# ---- Tray autostart (Scheduled Task) ----------------------------------------
# Earlier attempts to register the tray under HKCU\...\Run or as a Startup-
# folder .lnk both caused Windows 11 Settings -> Startup apps to crash when
# it tried to render the entry (probably icon/metadata extraction failing on
# the venv pythonw.exe launcher stub on D:\). Settings does NOT enumerate
# Scheduled Tasks, so registering the autostart there cannot trigger that
# crash class no matter what's in the target.

TASK_NAME = "AI Servers Tray"


def _startup_lnk_path() -> Path:
    """Legacy path kept only so we can clean up old .lnk files on launch."""
    appdata = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / STARTUP_LNK_NAME


def _remove_legacy_run_key() -> None:
    """Strip any leftover HKCU Run-key entry from an older tray version."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            try:
                winreg.DeleteValue(key, TRAY_RUN_NAME)
            except FileNotFoundError:
                pass
    except FileNotFoundError:
        pass


def _remove_legacy_startup_lnk() -> None:
    """Remove the Startup-folder .lnk from the previous tray version."""
    try:
        _startup_lnk_path().unlink()
    except FileNotFoundError:
        pass
    except Exception as e:
        _log(f"remove legacy startup lnk failed: {e!r}")


def _run_schtasks(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["schtasks.exe", *args],
        creationflags=subprocess.CREATE_NO_WINDOW,
        capture_output=True, text=True, timeout=15, check=False,
    )


def tray_autostart_enabled() -> bool:
    r = _run_schtasks(["/Query", "/TN", TASK_NAME])
    return r.returncode == 0


def set_tray_autostart(enabled: bool) -> None:
    if enabled:
        pyw = sys.executable
        pyw_candidate = pyw.replace("python.exe", "pythonw.exe")
        if os.path.isfile(pyw_candidate):
            pyw = pyw_candidate
        script = str(Path(__file__).resolve())
        # schtasks /Create has a tiny escape grammar. /TR takes one string;
        # to embed quotes around an arg we double them.
        tr = f'\\"{pyw}\\" \\"{script}\\"'
        r = _run_schtasks([
            "/Create", "/F",
            "/SC", "ONLOGON",
            "/RL", "LIMITED",
            "/TN", TASK_NAME,
            "/TR", tr,
        ])
        if r.returncode != 0:
            _log(f"schtasks create failed rc={r.returncode} stderr={r.stderr!r}")
    else:
        r = _run_schtasks(["/Delete", "/F", "/TN", TASK_NAME])
        if r.returncode != 0 and "cannot find" not in (r.stderr or "").lower():
            _log(f"schtasks delete failed rc={r.returncode} stderr={r.stderr!r}")


# ---- Shared state cache -----------------------------------------------------
class State:
    """Snapshot of server-running + tailscale-IP state, refreshed by the
    background poll loop. All UI callbacks read from here so the menu opens
    instantly instead of blocking on socket probes / subprocess calls."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._running: dict[str, bool] = {s.key: False for s in SERVERS}
        self._ts_ip: str | None = None
        self._ts_last: float = 0.0
        self._tray_autostart: bool = False

    def refresh_running(self) -> bool:
        new = {s.key: s.is_running() for s in SERVERS}
        with self._lock:
            changed = new != self._running
            self._running = new
        return changed

    def refresh_ts_ip(self, force: bool = False) -> bool:
        now = time.time()
        with self._lock:
            if not force and (now - self._ts_last) < TS_IP_MAX_AGE:
                return False
        ip = tailscale_ip()
        with self._lock:
            changed = ip != self._ts_ip
            self._ts_ip = ip
            self._ts_last = now
        return changed

    def refresh_tray_autostart(self) -> bool:
        v = tray_autostart_enabled()
        with self._lock:
            changed = v != self._tray_autostart
            self._tray_autostart = v
        return changed

    def get_running(self, key: str) -> bool:
        with self._lock:
            return self._running.get(key, False)

    def snapshot_running(self) -> dict[str, bool]:
        with self._lock:
            return dict(self._running)

    def get_ts_ip(self) -> str | None:
        with self._lock:
            return self._ts_ip

    def get_tray_autostart(self) -> bool:
        with self._lock:
            return self._tray_autostart


# ---- Tray icon image --------------------------------------------------------
# Per-server state colors:
#   green  = port responding (server is up)
#   yellow = port closed AND autostart-on-login is OFF (intentionally off)
#   red    = port closed AND autostart-on-login is ON (should be up, isn't)
_COLORS = {
    "green":  (38, 170, 80, 255),
    "yellow": (240, 180, 40, 255),
    "red":    (200, 50, 50, 255),
    "gray":   (100, 100, 100, 255),
}
_BAR_BORDER = (0, 0, 0, 200)


def make_icon(server_states: list[str]) -> Image.Image:
    """Render a square icon split into vertical bars, one per server.
    server_states is a list of color names in SERVERS order."""
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    n = max(1, len(server_states))
    gap = 1
    total_gap = gap * (n + 1)
    bar_w = (size - total_gap) // n
    extra = size - total_gap - bar_w * n
    x = gap
    for i, state in enumerate(server_states):
        w = bar_w + (1 if i < extra else 0)
        fill = _COLORS.get(state, _COLORS["gray"])
        d.rectangle([x, gap, x + w - 1, size - gap - 1],
                    fill=fill, outline=_BAR_BORDER, width=1)
        x += w + gap
    return img


def derive_server_color(srv, cfg, running: bool) -> str:
    """Decide bar color for one server."""
    if running:
        return "green"
    if cfg["autostart_on_login"].get(srv.key, False):
        return "red"
    return "yellow"


# ---- Menu construction (cache-driven, no live probes) -----------------------
def build_tooltip(state: State) -> str:
    snap = state.snapshot_running()
    parts = []
    for s in SERVERS:
        mark = "v" if snap.get(s.key) else "x"
        parts.append(f"{mark}{s.port}")
    line = " ".join(parts)
    ip = state.get_ts_ip()
    ip_part = f" | {ip}" if ip else ""
    return (line + ip_part)[:127]


def build_menu(cfg: dict, state: State, on_action: Callable) -> Menu:
    # pystray strictly enforces (icon, item) -> None signature on actions.
    # Closure factories below bind `s` without leaking via default kwargs.

    def make_start(s: Server) -> Callable:
        def cb(icon, item):
            def work():
                s.start()
                on_action(delay=1.0)
            threading.Thread(target=work, daemon=True).start()
        return cb

    def make_stop(s: Server) -> Callable:
        def cb(icon, item):
            def work():
                s.stop()
                on_action(delay=0.2)
            threading.Thread(target=work, daemon=True).start()
        return cb

    def make_kill(s: Server) -> Callable:
        def cb(icon, item):
            def work():
                s.kill()
                on_action(delay=0.2)
            threading.Thread(target=work, daemon=True).start()
        return cb

    def make_open(s: Server) -> Callable:
        def cb(icon, item):
            # The monitor's "port" is a tiny heartbeat that only returns "ok";
            # opening its config file is far more useful than a blank page.
            if s.key == "monitor":
                cfg_path = SCRIPT_DIR / "monitor.json"
                try:
                    os.startfile(str(cfg_path))
                except Exception:
                    webbrowser.open(cfg_path.as_uri())
            else:
                webbrowser.open(f"http://127.0.0.1:{s.port}/")
        return cb

    def make_toggle_autostart(s: Server) -> Callable:
        def cb(icon, item):
            cfg["autostart_on_login"][s.key] = not cfg["autostart_on_login"].get(s.key, False)
            save_config(cfg)
        return cb

    def make_enabled_if_running(s: Server, want_running: bool) -> Callable:
        def cb(item):
            return state.get_running(s.key) == want_running
        return cb

    def make_checked_autostart(s: Server) -> Callable:
        def cb(item):
            return cfg["autostart_on_login"].get(s.key, False)
        return cb

    def make_top_label(s: Server) -> Callable:
        def cb(item):
            dot = "*" if state.get_running(s.key) else "-"
            return f"[{dot}] {s.name}"
        return cb

    def server_submenu(s: Server) -> Menu:
        return Menu(
            MenuItem("Start", make_start(s),
                     enabled=make_enabled_if_running(s, want_running=False)),
            MenuItem("Stop (graceful)", make_stop(s),
                     enabled=make_enabled_if_running(s, want_running=True)),
            MenuItem("Kill (force)", make_kill(s),
                     enabled=make_enabled_if_running(s, want_running=True)),
            Menu.SEPARATOR,
            MenuItem("Open in browser", make_open(s),
                     enabled=make_enabled_if_running(s, want_running=True)),
            MenuItem("Autostart on Windows login",
                     make_toggle_autostart(s),
                     checked=make_checked_autostart(s)),
        )

    def server_top(s: Server) -> MenuItem:
        return MenuItem(make_top_label(s), server_submenu(s))

    def toggle_tray_autostart(icon, item):
        new = not state.get_tray_autostart()
        set_tray_autostart(new)
        cfg["tray_autostart"] = new
        save_config(cfg)
        state.refresh_tray_autostart()

    def quit_tray(icon, item):
        icon.stop()

    def tray_autostart_check(item):
        return state.get_tray_autostart()

    return Menu(
        *[server_top(s) for s in SERVERS],
        Menu.SEPARATOR,
        MenuItem("Tray app: Autostart on login", toggle_tray_autostart,
                 checked=tray_autostart_check),
        MenuItem("Quit tray", quit_tray),
    )


# ---- Main -------------------------------------------------------------------
def main() -> None:
    cfg = load_config()
    state = State()

    # One-time migration: scrub any old HKCU Run-key entry or Startup-folder
    # .lnk from previous tray versions so Settings -> Startup apps stops
    # crashing on them. The new autostart lives in a Scheduled Task that
    # Settings does not enumerate.
    _remove_legacy_run_key()
    _remove_legacy_startup_lnk()

    # Warm the cache before showing the icon so the first paint is correct.
    state.refresh_running()
    state.refresh_ts_ip(force=True)
    state.refresh_tray_autostart()

    # Reconcile tray-autostart preference.
    if cfg.get("tray_autostart") and not state.get_tray_autostart():
        try:
            set_tray_autostart(True)
            state.refresh_tray_autostart()
        except Exception:
            pass

    # Honour per-server autostart-on-login.
    for s in SERVERS:
        if cfg["autostart_on_login"].get(s.key) and not state.get_running(s.key):
            try:
                s.start()
            except Exception:
                pass

    icon_obj: pystray.Icon | None = None

    def apply_icon():
        if icon_obj is None:
            return
        snap = state.snapshot_running()
        colors = [derive_server_color(s, cfg, snap.get(s.key, False)) for s in SERVERS]
        icon_obj.icon = make_icon(colors)
        icon_obj.title = build_tooltip(state)
        # Dynamic text/enabled/checked re-evaluate from State on next open;
        # no need to rebuild the Menu object.
        icon_obj.update_menu()

    def on_action(delay: float = 0.0):
        """Called from action threads after start/stop/kill. Slight delay
        lets the OS finish the port-state transition before we re-probe."""
        def go():
            if delay:
                time.sleep(delay)
            state.refresh_running()
            apply_icon()
        threading.Thread(target=go, daemon=True).start()

    def poll_loop():
        while True:
            try:
                changed = state.refresh_running()
                ts_changed = state.refresh_ts_ip()
                if changed or ts_changed:
                    apply_icon()
            except Exception:
                pass
            time.sleep(POLL_INTERVAL)

    menu = build_menu(cfg, state, on_action)
    snap = state.snapshot_running()
    initial_colors = [derive_server_color(s, cfg, snap.get(s.key, False)) for s in SERVERS]
    icon_obj = pystray.Icon(
        "AI Servers",
        make_icon(initial_colors),
        build_tooltip(state),
        menu,
    )

    threading.Thread(target=poll_loop, daemon=True).start()
    icon_obj.run()


if __name__ == "__main__":
    main()
