# ai-servers-tray

System-tray launcher and live status monitor for a small fleet of locally-run AI servers on Windows. Built for an AMD-GPU + ZLUDA workstation running multiple ComfyUI instances, LM Studio, and OpenCode, but it's generic — any TCP server fits.

The tray icon is a square split into vertical bars, one bar per server, each bar coloured by live state. A separate daemon process watches the ComfyUI WebSocket APIs and sends phone notifications (ntfy.sh) on freeze, error, and completion events.

---

## What you get

| Component | Purpose | Entry point |
|---|---|---|
| **Tray app** | Right-click menu to start/stop/kill each server; live status bars; per-server autostart toggle | `tray.py` |
| **Monitor** | WebSocket-driven watchdog with phone push notifications via ntfy.sh | `monitor.py` |
| **AI Monitor installer** | One-file `.exe` that installs/uninstalls the monitor (scheduled-task autostart, lifecycle, config) | `ai-monitor-installer.py` → `dist/ai-monitor-installer.exe` |
| **Watt-Tray installer** | Companion installer for the separate watt-tray repo | `watt-tray-installer.py` → `dist/watt-tray-installer.exe` |

---

## Icon legend

The tray icon is 64×64 with one vertical bar per registered server. Order is the same as `SERVERS` in `tray.py`. Default order:

1. ComfyUI 6950 XT (port 8188)
2. ComfyUI 6900 XT (port 8189)
3. LM Studio (port 1234)
4. OpenCode Web (port 7777)
5. AI Monitor (port 9300 heartbeat)

Bar colours:

| Color | Meaning |
|---|---|
| **Green** | TCP port responding — server is up |
| **Yellow** | Port closed AND autostart-on-login for this server is OFF (intentionally off) |
| **Red** | Port closed AND autostart-on-login for this server is ON (should be up, isn't) |

Hover the icon for a text version (`v8188 v8189 -1234 -7777 v9300 | <tailscale-ip>`).

---

## How the tray works (`tray.py`)

* `SERVERS: list[Server]` defines what to manage. Each `Server` has a `key`, `name`, `port`, and `launch_args` (the command to start it).
* A `State` cache holds the latest TCP-probe result for each server and the latest Tailscale IPv4. The background poll thread refreshes both every 2 s; the menu UI callbacks read from `State` in O(1) so opening the menu is instant.
* `Server.start()` uses `subprocess.Popen` with `CREATE_NO_WINDOW` *only* (combining it with `DETACHED_PROCESS` denies `cmd.exe` a console and breaks `.bat`-based launchers).
* `Server.stop()` / `kill()` enumerate listener PIDs via `psutil.net_connections`, walk the process tree, and graceful-terminate then force-kill on timeout. These run in daemon threads so the pystray callback returns immediately even though `wait_procs` can take ~3 s.
* Tray autostart on Windows login is registered as a **Scheduled Task** (not an HKCU Run-key entry — those occasionally crash Windows 11 Settings → Startup apps when the target is `pythonw.exe <script>` because icon/metadata extraction fails on the venv launcher stub).
* Per-server autostart toggles live in `config.json`.

Run with `pythonw.exe` so no console appears:
```bat
"D:\AI\ComfyUI\venv\Scripts\pythonw.exe" "D:\AI\TrayManager\tray.py"
```
The provided `start-tray.bat` does that.

---

## How the monitor works (`monitor.py`)

* Two parts: a heartbeat TCP server on `127.0.0.1:9300` so the tray can probe it like any other server, and a per-server `ServerWatcher` thread holding a persistent WebSocket to each ComfyUI's `/ws` endpoint.
* Every WebSocket message timestamps the watcher's last-event clock. Hang detection is simple: if a prompt is currently running and no event has fired for ≥ `freeze_minutes`, that's a freeze (true progress death, not a slow workflow).
* ComfyUI event types we care about: `execution_start`, `executing`, `progress`, `execution_error`, `execution_success`, `execution_interrupted`. Other events still freshen the idle timer.
* DOWN/RECOVERY transitions are detected by an HTTP REST probe of `/system_stats` from the main loop (separate from the WebSocket, because if the server is fully unreachable the WS reconnect loop won't fire useful events).

Notification types:

| Title prefix | Trigger | ntfy priority | Tag |
|---|---|---|---|
| `[DOWN]` | `/system_stats` unreachable | 4 | `rotating_light` |
| `[OK] back up` | down → up transition | 3 | `white_check_mark` |
| `[FREEZE]` | running prompt + no WS event for ≥ `freeze_minutes` | 4 | `warning` |
| `[ERROR]` | `execution_error` event; includes exception, node, traceback tail | 5 (max) | `x` |
| `[DONE]` | `execution_success` for a prompt that ran ≥ `completion_min_seconds` | 3 | `white_check_mark` |

Configurable in `monitor.json`:

```json
{
  "servers": [
    {"name": "6950 XT", "url": "http://127.0.0.1:8188"},
    {"name": "6900 XT", "url": "http://127.0.0.1:8189"}
  ],
  "ntfy_server": "https://ntfy.sh",
  "ntfy_topic": "ai-monitor-<random-suffix>",
  "poll_seconds": 15,
  "freeze_minutes": 8,
  "completion_min_seconds": 30,
  "http_timeout_seconds": 5,
  "notify_recovery": true,
  "notify_on_start": true,
  "notify_completion": true,
  "notify_error": true,
  "notify_freeze": true,
  "traceback_lines": 6
}
```

On first run the monitor auto-generates a hard-to-guess random topic string. Subscribe your phone to that topic from the ntfy mobile app.

State is persisted in `monitor_state.json` so debounced notifications survive restarts. Log goes to `monitor.log`.

---

## How the installers work

Two PyInstaller-bundled single-file `.exe`s:

* `ai-monitor-installer.exe` — embeds the current `monitor.py` source as a data file (`--add-data "monitor.py;."`). On Install it lays down `monitor.py`, `start-monitor.bat`, registers the `AI Servers Monitor` scheduled task (logon trigger, runs as the current user, limited rights), and starts the process. On Uninstall it kills the process, deletes the task, and optionally deletes the files (the `monitor.json` with your private ntfy topic is one of those files — keep it unless you want to lose your topic).
* `watt-tray-installer.exe` — manages only the `WattTray` scheduled task and process lifecycle for the separate watt-tray app. Doesn't bundle the watt-tray itself (it lives in its own repo); just toggles its autostart.

Both `.exe`s show an interactive menu when launched with no args, or accept `install` / `uninstall` / `status` / `reinstall` as positional args. The scheduled-task **create** step needs an elevated terminal; `delete` does not.

Rebuild any time with `build-installers.bat`. That script uses the PyInstaller in `D:\AI\ComfyUI\venv\`.

---

## Repository layout

```
ai-servers-tray/
├── tray.py                       # The system-tray app
├── monitor.py                    # WebSocket watchdog daemon
├── ai-monitor-installer.py       # Installer source (PyInstaller wraps this + monitor.py)
├── watt-tray-installer.py        # Installer source for the watt-tray companion repo
├── start-tray.bat                # No-console launcher for tray.py
├── start-monitor.bat             # No-console launcher for monitor.py
├── build-installers.bat          # PyInstaller build script
├── README.md
├── LICENSE
└── .gitignore
```

User-specific files NOT tracked:
- `config.json` (per-machine tray prefs)
- `monitor.json` (contains the user's private ntfy topic)
- `monitor_state.json`, `tray.log`, `monitor.log` (runtime state)
- `dist/`, `build/`, `__pycache__/`, `*.bak` (build/runtime artifacts)
- `*.spec` (PyInstaller scratch files)

---

## Install from this repo (fresh machine)

1. Have Python 3.11 installed at `D:\AI\ComfyUI\venv\` (or edit paths in the scripts). The venv needs `psutil`, `Pillow`, `pystray`, and `websocket-client`.
2. Clone this repo to `D:\AI\TrayManager\`.
3. Edit `SERVERS` in `tray.py` to match your launch commands and ports.
4. Build installers: `build-installers.bat`.
5. Run `dist\ai-monitor-installer.exe` from an elevated terminal → choose Install. First run generates a random ntfy topic in `monitor.json`. Subscribe to it on your phone.
6. Launch tray: double-click `start-tray.bat`. Right-click the icon → "Tray app: Autostart on login" if you want it to come back after reboots (this also uses a scheduled task — no Run-key entry).

---

## Design notes / hard-won lessons

These bit me during initial development; recording so they don't bite again:

* **`CREATE_NO_WINDOW | DETACHED_PROCESS` is broken for cmd.exe-launched targets.** Microsoft docs say these flags are mutually exclusive; in practice `Popen` succeeds but `cmd.exe` runs with no console and dies before the `.bat` body executes. Use `CREATE_NO_WINDOW` alone.
* **Don't put `pythonw.exe <script>` in HKCU\…\Run.** Windows 11 Settings → Startup apps occasionally crashes when extracting icon/metadata from a venv-launcher pythonw stub on `D:\`. Use a Startup-folder `.lnk` with an explicit icon, *or* a Scheduled Task. The tray currently does the Scheduled Task approach.
* **`Set-Content -Encoding UTF8` writes a BOM in PowerShell 5.1**, and `json.loads` chokes on it. `load_config()` uses `utf-8-sig` to silently strip it.
* **`pystray` strictly enforces `(icon, item)` action signatures.** Don't try to pass closures with default kwargs; use closure factories.
* **The Python venv launcher (`pythonw.exe` in `venv\Scripts\`) shows up as a 263 KB stub.** Process listings show *two* `pythonw.exe` entries per tray invocation — the launcher and the actual interpreter spawned from `home = ...` in `pyvenv.cfg`. They're one logical tray, one icon.

---

## Working on this with another Claude session

Open a new conversation pointed at this directory. Start with:

> Read README.md to understand the project, then look at tray.py and monitor.py.

Useful pointers for the LLM:
- The tray's poll cadence and cache structure are in the `State` class and `poll_loop` function.
- The monitor's WebSocket logic is in `ServerWatcher`; freeze detection is in the main loop's freeze block.
- Installer build settings are in `build-installers.bat`; the `--add-data "monitor.py;."` line is what makes the installer self-contained.
- `ntfy.sh` push semantics: `POST /<topic>` with body, plus `Title`, `Priority`, `Tags` headers. Tags become emoji on the phone.

---

## License

MIT — see [LICENSE](LICENSE).
