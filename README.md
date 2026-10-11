# ai-servers-tray

> **Archived and superseded. Not maintained.** This standalone repo is archived on GitHub (read-only). The tray app and monitor were merged into a newer version in the author's private monorepo, and development continued there. What you see here is a frozen snapshot of the original version. For the full audited details, see [PROJECT-DOCUMENTATION.md](PROJECT-DOCUMENTATION.md).

System-tray launcher and live status monitor for a small fleet of locally-run AI servers on Windows. It was built for an AMD-GPU + ZLUDA workstation running multiple ComfyUI instances, LM Studio and OpenCode, but it's generic: any TCP server fits.

The tray icon is a square split into vertical bars, one bar per server, each coloured by live state. A separate daemon process watches the ComfyUI WebSocket APIs. It sends phone notifications through ntfy (default server ntfy.sh) when a server goes down or comes back, and on freeze, error and completion events.

---

## What you get

| Component | Purpose | Entry point |
|---|---|---|
| **Tray app** | Right-click menu to start/stop/kill each server; live status bars; per-server autostart toggle | `tray.py` |
| **Monitor** | WebSocket-driven watchdog with phone push notifications via ntfy | `monitor.py` |
| **AI Monitor installer** | One-file `.exe` that installs/uninstalls the monitor (scheduled-task autostart, lifecycle, config) | `ai-monitor-installer.py` → `dist/ai-monitor-installer.exe` |
| **Watt-Tray installer** | Companion installer for the separate watt-tray repo | `watt-tray-installer.py` → `dist/watt-tray-installer.exe` |

---

## Icon legend

The tray icon is 64×64, with one vertical bar per registered server. Bars are in the same order as `SERVERS` in `tray.py`. Default order:

1. ComfyUI 6950 XT (port 8188)
2. ComfyUI 6900 XT (port 8189)
3. LM Studio (port 1234)
4. OpenCode Web (port 7777)
5. AI Monitor (port 9300 heartbeat)

Bar colours:

| Color | Meaning |
|---|---|
| **Green** | TCP port responding: the server is up |
| **Yellow** | Port closed AND autostart-on-login for this server is OFF (intentionally off) |
| **Red** | Port closed AND autostart-on-login for this server is ON (should be up, isn't) |

The icon is repainted only when a server's running state or the Tailscale IP changes, or after a Start/Stop/Kill. After you toggle a down server's "Autostart on Windows login", its bar keeps the old colour (yellow or red) until the next state change.

Hover over the icon for a text version, e.g. `v8188 v8189 x1234 x7777 v9300 | <tailscale-ip>` (`v` = up, `x` = down).

---

## How the tray works (`tray.py`)

* **What it manages.** `SERVERS: list[Server]` defines the servers. Each `Server` has a `key`, `name`, `port`, `launch_args` (the command that starts it) and an optional `launch_cwd`.
* **State cache.** A `State` cache holds the latest TCP-probe result for each server, the latest Tailscale IPv4, and the tray-autostart flag. A background poll thread re-probes the servers every 2 s. The Tailscale IP is cached and refreshed at most once a minute. Menu callbacks read from `State` in O(1), so opening the menu is instant.
* **Starting.** `Server.start()` uses `subprocess.Popen` with `CREATE_NO_WINDOW` *only*. Combining it with `DETACHED_PROCESS` denies `cmd.exe` a console and breaks `.bat`-based launchers.
* **Stopping.** `Server.stop()` and `kill()` find the PIDs listening on the port via `psutil.net_connections` and walk each process tree. Stop terminates gracefully and force-kills after a 3 s timeout. These run in daemon threads, so the pystray callback returns immediately even though `wait_procs` can take ~3 s.
* **Tray autostart.** The tray's own autostart on Windows login is a **Scheduled Task** (`AI Servers Tray`), not an HKCU Run-key entry. Run-key entries occasionally crash Windows 11 Settings → Startup apps when the target is `pythonw.exe <script>`, because icon/metadata extraction fails on the venv launcher stub.
  * Tray autostart is **on by default**: on every launch, if `tray_autostart` is true (the default) and the task is missing, the tray creates it. `config.json` isn't written until you use a toggle, so the default applies until then. Use the menu toggle, not Task Scheduler, to turn it off: deleting the task in Task Scheduler leaves `tray_autostart` true, so the next launch recreates it.
  * Creating an ONLOGON task usually needs elevation, and `start-tray.bat` launches the tray non-elevated. If creation fails, the error goes only to `tray.log` (`schtasks create failed`) and the checkbox stays unticked. Run the tray elevated once, or create the task from an elevated prompt.
  * On every launch the tray also removes leftover Run-key and Startup-folder entries from older versions.
* **Per-server autostart.** The toggles live in `config.json`. When the tray starts, it launches every server whose toggle is on and that isn't already running.

Run with `pythonw.exe` so no console appears:
```bat
"<venv>\Scripts\pythonw.exe" "<install-dir>\tray.py"
```
The provided `start-tray.bat` does that. It contains a hardcoded venv path, so edit it for your machine.

---

## How the monitor works (`monitor.py`)

* **Two parts:**
  * a heartbeat TCP server on `127.0.0.1:9300`, so the tray can probe the monitor like any other server
  * a per-server `ServerWatcher` thread, holding a persistent WebSocket to each ComfyUI's `/ws` endpoint
* **Freeze detection.** Every WebSocket message updates the watcher's last-event clock. The check is simple: if a prompt is running and no event has fired for ≥ `freeze_minutes`, that's a freeze. That means progress has truly died, not just a slow workflow.
* **Events.** The ComfyUI event types the monitor acts on are `execution_start`, `executing`, `progress`, `execution_error`, `execution_success` and `execution_interrupted`. Every other event still resets the idle timer.
* **DOWN/RECOVERY.** The main loop detects these with an HTTP REST probe of `/system_stats`, separate from the WebSocket. If the server is fully unreachable, the WS reconnect loop won't fire useful events. Each probe is compared with the `alive` value saved in `monitor_state.json`. Only a server with no saved state (first run, a renamed server, or a deleted or unreadable state file) is recorded silently on its first probe. So a server that was up when the monitor stopped and is down at startup does send `[DOWN]`.

Notification types:

| Title prefix | Trigger | ntfy priority | Tag |
|---|---|---|---|
| `AI monitor online` | monitor started (if `notify_on_start`); body includes the topic | 2 | `satellite_antenna` |
| `[DOWN]` | `/system_stats` becomes unreachable | 4 | `rotating_light` |
| `[OK] … back up` | down → up transition (if `notify_recovery`) | 3 | `white_check_mark` |
| `[FREEZE]` | running prompt + no WS event for ≥ `freeze_minutes` (if `notify_freeze`) | 4 | `warning` |
| `[ERROR]` | `execution_error` event; includes exception, node, traceback tail (if `notify_error`) | 5 (max) | `x` |
| `[DONE]` | `execution_success` for a prompt that ran ≥ `completion_min_seconds` (if `notify_completion`) | 3 | `white_check_mark` |

Configurable in `monitor.json` (these are the defaults written on first run):

```json
{
  "servers": [
    {"name": "ComfyUI 6950 XT", "url": "http://127.0.0.1:8188"},
    {"name": "ComfyUI 6900 XT", "url": "http://127.0.0.1:8189"}
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

**The ntfy topic.** On first run the monitor generates a hard-to-guess random topic string. Subscribe your phone to that topic from the ntfy mobile app. The topic is also written to `monitor.log` at every start.

**Don't hand-edit `monitor.json` into invalid JSON.** If the file can't be parsed, the monitor rewrites it with defaults, including a *new* topic.

**`monitor.json` is read only at startup.** That includes edits made after the tray's "Open in browser" on the AI Monitor entry opens the file. Restart the monitor after editing it: Stop then Start the AI Monitor entry in the tray, or run `ai-monitor-installer.exe reinstall` and answer **N** to keep `monitor.json`.

State is persisted in `monitor_state.json`, so debounced notifications survive restarts. The log goes to `monitor.log`.

---

## How the installers work

`build-installers.bat` builds two single-file `.exe`s with PyInstaller.

**`ai-monitor-installer.exe`** embeds the current `monitor.py` source as a data file (`--add-data "monitor.py;."`).
* **Install:**
  1. pip-installs `websocket-client` into the venv.
  2. Lays down `monitor.py` and `start-monitor.bat`.
  3. Registers the `AI Servers Monitor` scheduled task (logon trigger, runs as the current user, limited rights).
  4. Starts the process.
* **Uninstall:** kills the process, deletes the task, and optionally deletes the files. One of those files is `monitor.json`, which holds your private ntfy topic; keep it unless you want to lose your topic.

**`watt-tray-installer.exe`** manages only the `WattTray` scheduled task and process lifecycle for the separate watt-tray app. It doesn't bundle watt-tray itself, which lives in its own repo; it just toggles its autostart.

**Running them.** Both `.exe`s show an interactive menu when launched with no arguments. They also accept `install` / `uninstall` / `status` / `reinstall` as positional arguments, plus `--yes`. In the monitor installer, `--yes` makes `uninstall` delete files without prompting.
* `reinstall` runs the uninstall step first, which asks whether to delete files. Answer **N** to keep `monitor.json`.
* The code reports a scheduled-task **create** failure as probably needing an elevated terminal; `delete` does not.

**Rebuilding.** Rebuild any time with `build-installers.bat`. It uses the PyInstaller in the venv path hardcoded at the top of the script, and pip-installs PyInstaller there if it's missing.

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
├── PROJECT-DOCUMENTATION.md      # Full project documentation (audited)
├── LICENSE
└── .gitignore
```

User-specific files that are NOT tracked:
- `config.json` (per-machine tray prefs)
- `monitor.json` (contains the user's private ntfy topic)
- `monitor_state.json`, `tray.log`, `monitor.log` (runtime state)
- `dist/`, `build/`, `__pycache__/`, `*.bak` (build/runtime artifacts)
- `*.spec` (PyInstaller scratch files)

---

## Install from this repo (fresh machine)

The scripts hardcode absolute paths for one Python venv (the ComfyUI venv on the author's machine) and one install directory. `<venv>` and `<install-dir>` below stand for your own.

1. Have Python 3.11 in a venv at `<venv>`. The venv needs `psutil`, `Pillow`, `pystray` and `websocket-client` (1.4.0 or newer; older releases lack the `reconnect=` argument the monitor uses, so only the startup, DOWN and RECOVERY notifications would work).
2. Clone this repo to `<install-dir>`.
3. Edit the hardcoded paths to match. They are in:
   * `SERVERS` in `tray.py`
   * `INSTALL_DIR`, `VENV_PYTHONW`, `VENV_PIP` and `START_BAT` in `ai-monitor-installer.py`
   * `WATT_DIR` in `watt-tray-installer.py`
   * the three `.bat` files

   While you're in `tray.py`, set `SERVERS` to your own launch commands and ports.
4. Build installers: `build-installers.bat`.
5. Run `dist\ai-monitor-installer.exe` from an elevated terminal and choose Install. The first run generates a random ntfy topic in `monitor.json`. Subscribe to it on your phone.
6. Launch the tray by double-clicking `start-tray.bat`. "Tray app: Autostart on login" is on by default, so the tray registers its own scheduled task (no Run-key entry). Creating an ONLOGON task usually needs elevation; if the tray isn't elevated, creation fails (`tray.log`: `schtasks create failed`) and the checkbox stays unticked. Run the tray elevated once, or create the task from an elevated prompt. Right-click the icon and untick the option if you don't want it back after reboots.

---

## Design notes / hard-won lessons

These bit me during initial development; recording them so they don't bite again:

* **`CREATE_NO_WINDOW | DETACHED_PROCESS` is broken for cmd.exe-launched targets.** Microsoft docs say these flags are mutually exclusive. In practice `Popen` succeeds, but `cmd.exe` runs with no console and dies before the `.bat` body executes. Use `CREATE_NO_WINDOW` alone.
* **Don't put `pythonw.exe <script>` in HKCU\…\Run.** Windows 11 Settings → Startup apps occasionally crashes when extracting icon/metadata from a venv-launcher pythonw stub on a non-system drive. A Startup-folder `.lnk` triggered the same crash. Use a Scheduled Task, which Settings doesn't enumerate. The tray uses the Scheduled Task approach and deletes any old Run-key / Startup `.lnk` entries on launch.
* **`Set-Content -Encoding UTF8` writes a BOM in PowerShell 5.1**, and `json.loads` chokes on it. `load_config()` (in both `tray.py` and `monitor.py`) reads with `utf-8-sig` to silently strip it.
* **`pystray` strictly enforces `(icon, item)` action signatures.** Don't try to pass closures with default kwargs; use closure factories.
* **The Python venv launcher (`pythonw.exe` in `venv\Scripts\`) shows up as a 263 KB stub.** Process listings show *two* `pythonw.exe` entries per tray invocation: the launcher, and the actual interpreter spawned from `home = ...` in `pyvenv.cfg`. They're one logical tray with one icon.

---

## Working on this with another Claude session

Open a new conversation pointed at this directory. Start with:

> Read README.md and PROJECT-DOCUMENTATION.md to understand the project, then look at tray.py and monitor.py.

Useful pointers for the LLM:
- The tray's poll cadence and cache structure are in the `State` class and the `poll_loop` function (nested in `main()`).
- The monitor's WebSocket logic is in `ServerWatcher`; freeze detection is in the main loop's freeze block.
- Installer build settings are in `build-installers.bat`. The `--add-data "monitor.py;."` line is what makes the installer self-contained.
- `ntfy` push semantics: `POST /<topic>` with a body, plus `Title`, `Priority` and `Tags` headers. Tags become emoji on the phone.

---

## License

MIT; see [LICENSE](LICENSE).
