# ai-servers-tray — Project Documentation

Windows system-tray launcher and live status icon for a few locally-run AI servers (two ComfyUI instances, LM Studio, OpenCode web), plus a WebSocket-driven ComfyUI watchdog that sends phone alerts through ntfy, and PyInstaller-built installers. **Archived and superseded. Not maintained.**

_Last audited: 2026-10-10 against main @ 2fe150e._

## Overview / Purpose

`ai-servers-tray` is a personal Windows utility. It has two Python programs that work together, plus installers and launcher scripts:

- **Tray app (`tray.py`)**: a single `pystray` system-tray icon. For each registered local server it can Start, Stop (graceful), Kill (force) and "Open in browser". It shows each server's live state as a coloured vertical bar and has a per-server "Autostart on Windows login" toggle. The tray applies that toggle when it starts up. The tooltip shows the host's Tailscale IPv4. The tray's own logon autostart is a Windows Scheduled Task.
- **Monitor daemon (`monitor.py`)**: a watchdog that holds a persistent WebSocket to each ComfyUI server's `/ws` endpoint. It detects these events and pushes each one to the user's phone through ntfy:
  - DOWN and RECOVERY, via HTTP `/system_stats`
  - FREEZE: a prompt is running but no WebSocket event has arrived within a threshold
  - ERROR: an `execution_error` event
  - DONE: an `execution_success` event

  It also listens on a loopback heartbeat port, so the tray can show it as just another server.
- **Installers (`ai-monitor-installer.py`, `watt-tray-installer.py`)**: single-file `.exe`s built with PyInstaller by `build-installers.bat`. They register Windows Scheduled Tasks for logon autostart and manage process lifecycle. The monitor installer embeds `monitor.py` as a data file, so its `.exe` is self-contained.
- **Launchers**: `start-tray.bat` and `start-monitor.bat` run the two programs with `pythonw.exe`, so no console window appears.

In principle it is generic: any TCP server can be added to the `SERVERS` list in `tray.py`. In practice, the committed configuration hardcodes the author's own workstation: absolute install and venv paths, plus the server list. Per the original README it was built for an AMD-GPU workstation running several ComfyUI instances.

## Status

**Archived and superseded. Kept as a frozen snapshot of the original standalone version.**

- **Archived on GitHub (read-only).** Archived 2026-05-16 02:24 UTC, under an hour after the repo was created and its single commit pushed. It was published as an already-frozen snapshot. It is still public.
- **Superseded.** Per the owner, development continued in a newer private monorepo version, into which the tray app and monitor were merged. The repo itself cannot show this; it comes from the owner's portfolio records.
- **History:**
  - One commit: `2fe150e` (2026-05-15, "Initial commit: tray app + WebSocket-based monitor + installers").
  - One branch (`main`) locally and on GitHub. No tags.
  - No remote branch has commits that are not on `main`.
- **Code state at that commit.** The code is complete for its scope: there are no `TODO`/`FIXME` markers and no stubbed functions. All four `.py` files parse (checked 2026-10-10 with Python's `ast.parse`). They were not executed: the code is Windows-only and needs the author's local servers.
- **Unpushed edits.** Documentation edits made after archiving, including this file, exist only locally. They can be pushed only if the repo is un-archived.

## Technical Requirements

- **OS:** Windows 10/11. The code uses `winreg`, `schtasks.exe`, `os.startfile`, `subprocess.CREATE_NO_WINDOW`, `pythonw.exe` and Windows path conventions. `tray.py` imports `winreg` at module level, so it will not even import on macOS or Linux.
- **Language:** Python. The original README specifies Python 3.11. No version is pinned anywhere: there is no `requirements.txt`, `pyproject.toml` or lockfile. The code uses `from __future__ import annotations` with PEP 604/585 type hints.
- **Python packages (unpinned):**
  - `tray.py`: `psutil`, `Pillow`, `pystray`.
  - `monitor.py`: requires `websocket-client` >= 1.4.0, the first release whose `WebSocketApp.run_forever()` accepts `reconnect=`. On older versions the WebSocket thread fails every 5 s and only the startup, DOWN and RECOVERY notifications are sent. If the import fails, `monitor.py` pip-installs the package at runtime; an already-installed older version is not upgraded.
  - Installers: `psutil`, imported lazily. It is needed to find and stop processes; in `watt-tray-installer` it also drives the running-state check used by `status`, the menu and `install`'s start step. Without it that check always reports "not running", so a second copy can be launched.
  - Build only: `pyinstaller`. `build-installers.bat` installs it into the venv if it is missing.
- **Hardcoded layout.** Everything assumes one Python venv, the one belonging to the author's ComfyUI install. That venv provides `python.exe`, `pythonw.exe`, `pip.exe` and `pyinstaller.exe`. Everything also assumes one install directory that holds `tray.py`, `monitor.py` and their runtime files. These are absolute paths, hardcoded in:
  - `tray.py`: the `SERVERS` launch commands, including the monitor entry.
  - `ai-monitor-installer.py`: `INSTALL_DIR`, `VENV_PYTHONW`, `VENV_PIP` and the embedded `START_BAT` text.
  - `start-tray.bat`, `start-monitor.bat` and `build-installers.bat`: the venv path.
  - `watt-tray-installer.py`: a separate hardcoded watt-tray app directory, `WATT_DIR`.
- **Software the tray expects on the host:**
  - ComfyUI ×2 on ports 8188 and 8189. Each is started by its own `.bat` launcher in the ComfyUI directory. Those launchers are not in this repo.
  - The LM Studio CLI (`lms`), API on port 1234.
  - The OpenCode binary (`opencode web`), on port 7777.
  - Optional: the Tailscale CLI in its standard Program Files location, for the tooltip IP.
- **Accounts/keys:** none. ntfy is used anonymously, with an auto-generated random topic. To receive alerts, install the ntfy mobile app and subscribe to that topic.

## Dependencies

All third-party libraries come from PyPI. None are vendored.

| Library | Used in | License |
|---|---|---|
| `psutil` | `tray.py`, both installers (bundled into the `.exe`s via `--hidden-import psutil`) | BSD-3-Clause |
| `Pillow` (PIL) | `tray.py` (draws the icon at runtime) | MIT-CMU (HPND-style) |
| `pystray` | `tray.py` | LGPL-3.0 |
| `websocket-client` | `monitor.py` | Apache-2.0 |
| `pyinstaller` (build only) | `build-installers.bat` | GPL-2.0-or-later with the PyInstaller bootloader exception |

Everything else is the standard library: `json`, `socket`, `subprocess`, `threading`, `winreg`, `urllib`, `secrets`, `uuid`, `argparse`, `webbrowser`, `textwrap`, `pathlib`.

Three things install packages at runtime, all unpinned:
- `monitor.py` runs `pip install --quiet websocket-client` into its own interpreter if the import fails.
- `ai-monitor-installer` runs `pip install --quiet websocket-client` with the venv's `pip.exe` on install.
- `build-installers.bat` runs `pip install --quiet pyinstaller` if PyInstaller is missing.

## Setup Instructions

The scripts assume the author's directory layout. On any other machine, edit the hardcoded paths first (step 3). `<venv>` and `<install-dir>` below are placeholders.

1. On Windows, create (or reuse) a Python 3.11 venv and install the runtime dependencies:
   ```bat
   <venv>\Scripts\python.exe -m pip install psutil Pillow pystray "websocket-client>=1.4.0"
   ```
2. Clone the repo into the directory you want to run it from:
   ```bat
   git clone https://github.com/SexualMoose/ai-servers-tray.git <install-dir>
   ```
3. Edit the hardcoded paths so they point at your `<venv>` and `<install-dir>`. The files and constants are listed under Technical Requirements → Hardcoded layout. Then:
   - Edit `SERVERS` in `tray.py` to match your launch commands, ports and working directories.
   - Edit the monitored ComfyUI URLs in `monitor.json`. That file is generated on the monitor's first run; its defaults come from `_default_config()` in `monitor.py`.
4. Optional: build the installers.
   ```bat
   cd /d <install-dir>
   build-installers.bat
   ```
   This writes `dist\ai-monitor-installer.exe` and `dist\watt-tray-installer.exe`.

## Build & Run

**Run the tray (no console window):**
```bat
start-tray.bat
```
This is equivalent to `<venv>\Scripts\pythonw.exe <install-dir>\tray.py`. `start-tray.bat` uses its own hardcoded venv path.

**Run the monitor directly:**
```bat
start-monitor.bat
```
Or run `<venv>\Scripts\python.exe monitor.py`. Output goes to `monitor.log`, not the console. On first launch the monitor writes `monitor.json` with a freshly generated random ntfy topic, and logs the topic to `monitor.log`.

**Build the installer executables:**
```bat
build-installers.bat
```
This runs PyInstaller with `--onefile --console --clean --hidden-import psutil`. The monitor installer also gets `--add-data "monitor.py;."`. Output goes to `dist\`, which is gitignored.

**Install the monitor as a logon-autostart Scheduled Task:**
```bat
dist\ai-monitor-installer.exe install
```
- **Actions:** `install`, `uninstall`, `status`, `reinstall`. With no arguments, the `.exe` shows an interactive menu.
- **`--yes`:** skips the prompt and deletes the files (`monitor.py`, `start-monitor.bat`, `monitor_state.json`, `monitor.log` and `monitor.json`, which holds your ntfy topic). It affects only `uninstall`; `reinstall` always prompts.
- **Running from source:** the installer also runs unbuilt, e.g. `python ai-monitor-installer.py status`. When it is not frozen, it looks for `monitor.py` next to itself.
- **Elevation:** if task creation fails, the installer prints a hint that an elevated terminal may be needed.

**Wire up autostart for the separate watt-tray app:**
```bat
dist\watt-tray-installer.exe install
```
It takes the same four actions and the same interactive menu. `--yes` is accepted but has no effect.

## Usage

**Tray menu.** Right-click the icon. Each server gets a submenu, labelled `[*] <name>` when the server is up and `[-] <name>` when it is down. The submenu items are:

| Item | What it does | Enabled |
|---|---|---|
| Start | Starts the server | Only when it is down |
| Stop (graceful) | Terminates the server's process tree, force-kills whatever survives 3 s | Only when it is up |
| Kill (force) | Kills the server's process tree immediately | Only when it is up |
| Open in browser | Opens the server's web page | Only when it is up |
| Autostart on Windows login | Checkbox: start this server when the tray launches | Always |

Below the server submenus are "Tray app: Autostart on login" (a checkbox) and "Quit tray".

**Icon.** A 64×64 square split into one vertical bar per server, in the same order as `SERVERS`:
- **Green:** the TCP port is responding.
- **Yellow:** the port is down and that server's autostart is OFF.
- **Red:** the port is down but autostart is ON, so the server should be up.

**Tooltip.** For example `v8188 v8189 x1234 x7777 v9300 | <tailscale-ip>`, where `v` means up and `x` means down. It is truncated to 127 characters.

**Per-server autostart.** When the tray starts, it launches every server whose toggle is on and that is not already running. Toggles are stored in `config.json`.

**Tray autostart** is **on by default** (`tray_autostart: true`; `config.json` isn't written until a toggle is used). On every launch, if it is enabled and the `AI Servers Tray` scheduled task does not exist, the tray creates the task. The menu toggle creates or deletes the task; use it, not Task Scheduler, to turn autostart off, or the next launch recreates the task. Creating an ONLOGON task usually needs elevation. If the tray isn't elevated, creation fails (`tray.log`: `schtasks create failed`) and the checkbox stays unticked. Run the tray elevated once, or create the task from an elevated prompt. On launch the tray also deletes a legacy HKCU Run value and a Startup-folder shortcut left by older versions.

**Open in browser** opens `http://127.0.0.1:<port>/`. For the monitor entry it opens `monitor.json` instead, because the monitor's port is only a heartbeat.

**Monitor notifications.** Subscribe the ntfy mobile app to the topic in `monitor.json`. The monitor sends:

| Title | Trigger | Priority | Tag |
|---|---|---|---|
| `AI monitor online` | Monitor start (if `notify_on_start`). The body includes the topic. | 2 | `satellite_antenna` |
| `[DOWN] <name> unreachable` | `/system_stats` goes from reachable to unreachable. Always sent; there is no toggle. | 4 | `rotating_light` |
| `[OK] <name> back up` | Unreachable goes back to reachable (if `notify_recovery`) | 3 | `white_check_mark` |
| `[FREEZE] <name> stalled` | A prompt is running, the WebSocket is connected, and no event has arrived for ≥ `freeze_minutes` (minimum 60 s). Sent once per prompt (if `notify_freeze`). | 4 | `warning` |
| `[ERROR] <name> execution failed` | `execution_error`. The body has the node, prompt id, exception and the last `traceback_lines` lines of the traceback (if `notify_error`). | 5 | `x` |
| `[DONE] <name> prompt completed` | `execution_success` for a prompt that ran ≥ `completion_min_seconds` (if `notify_completion`) | 3 | `white_check_mark` |

## Architecture

**`tray.py`**

- **`Server` class:**
  - `is_running()` makes a TCP connect to `127.0.0.1:<port>` with a 0.1 s timeout.
  - `listener_pids()` finds the processes listening on the port, via `psutil.net_connections`.
  - `start()` uses `subprocess.Popen` with `CREATE_NO_WINDOW` only. Adding `DETACHED_PROCESS` breaks `.bat` launchers.
  - `stop()` and `kill()` walk the process tree. They terminate (or kill), wait 3 s, then force-kill whatever is left.
- **`State` cache:** a thread-safe snapshot of:
  - each server's running state
  - the Tailscale IP
  - the tray-autostart flag

  A background `poll_loop` refreshes the running state every 2 s (`POLL_INTERVAL`). The Tailscale IP is refreshed at most every 60 s (`TS_IP_MAX_AGE`). The autostart flag is refreshed at startup and on toggle. All `pystray` menu callbacks read from this cache, so the menu opens instantly. Start, Stop and Kill run in daemon threads.
- **Tray autostart:** a Scheduled Task, created with `schtasks /Create /SC ONLOGON /RL LIMITED`. It is deliberately not an HKCU Run key or a Startup-folder `.lnk`: per the code comments, both of those crashed Windows 11 Settings → Startup apps.
- **Config:** prefs are stored in `config.json`, which is read as `utf-8-sig` so a BOM does no harm.

**`monitor.py`**

- **`heartbeat_server()`:** a tiny TCP listener on `127.0.0.1:9300`. It answers `HTTP 200 ok`, so the tray can probe the monitor like any other server.
- **`ServerWatcher(threading.Thread)`:** one persistent `websocket-client` connection per ComfyUI server, to `ws(s)://host:port/ws?clientId=<uuid>`. It pings every 30 s and waits 5 s before reconnecting. It timestamps every event (including binary preview frames), tracks the current prompt id and node, and queues success and error notifications.
- **Main loop:** runs every `max(5, poll_seconds)` seconds and does three things in order:
  1. Checks DOWN/RECOVERY with HTTP `GET /system_stats`. Compares the result with the `alive` value saved in `monitor_state.json`; only a server with no saved state is recorded silently on its first observation.
  2. Checks for FREEZE using each watcher's idle timer.
  3. Drains the queued success and error notifications.

  State is saved to `monitor_state.json`.
- **`ntfy_push()`:** sends `POST {ntfy_server}/{ntfy_topic}` with `Title`, `Priority`, `Tags` and `Content-Type` headers.

**Installers**

- **`ai-monitor-installer.py`:**
  - Install (needs the venv's `pythonw.exe` to exist):
    1. pip-installs `websocket-client` into the venv.
    2. Copies `monitor.py` into the install directory, from `sys._MEIPASS` when frozen. Writes `start-monitor.bat` there too.
    3. Registers the `AI Servers Monitor` ONLOGON/LIMITED task, which runs `pythonw.exe monitor.py`.
    4. Starts the monitor and waits up to about 6 s for the heartbeat.
  - Uninstall:
    1. Kills any `python*` process whose command line contains `monitor.py`.
    2. Deletes the task.
    3. Optionally deletes `monitor.py`, `start-monitor.bat`, `monitor.json`, `monitor_state.json` and `monitor.log`.
- **`watt-tray-installer.py`:** registers or deletes the `WattTray` ONLOGON/LIMITED task, which runs the watt-tray app's own venv `pythonw.exe` against `watt_tray.pyw`. It also starts or stops that process. It does not bundle the watt-tray app.

**Data flow.** Two independent paths:
- Monitor: ComfyUI `/ws` events → `ServerWatcher` snapshot or notification queue → monitor main loop → ntfy server → phone.
- Tray: TCP probe of each port → `State` → icon colours, tooltip and menu.

## Integrations & Interconnects

- **ComfyUI (×2):**
  - The tray launches each instance with `cmd.exe /c <ComfyUI dir>\launch-gpu0.bat` or `launch-gpu1.bat`. Those batch files are not in this repo.
  - The monitor connects to each instance's WebSocket `/ws` and polls HTTP `/system_stats`. URLs come from `monitor.json`; the defaults are loopback ports 8188 and 8189.
- **LM Studio:** started with `lms server start --port 1234`.
- **OpenCode:** started as `opencode web` on port 7777.
- **ntfy:** push delivery by HTTP POST. The default server is the public `https://ntfy.sh`; set `ntfy_server` to use another one. There are no auth headers, so the topic is the only access control.
- **Tailscale:** the local CLI (`tailscale ip -4`) supplies the IPv4 shown in the tray tooltip. There are no other network calls to Tailscale.
- **watt-tray:** a separate repo and app, also from this author. `watt-tray-installer.py` only manages the `WattTray` scheduled task and the `watt_tray.pyw` process. It expects the app to be installed already, with its own venv, in a hardcoded directory.
- **Windows Task Scheduler** (`schtasks.exe`). All three tasks use `/SC ONLOGON /RL LIMITED`:

  | Task | Created by |
  |---|---|
  | `AI Servers Tray` | `tray.py` |
  | `AI Servers Monitor` | `ai-monitor-installer` |
  | `WattTray` | `watt-tray-installer` |
- **Windows Registry** (`winreg`): used only to delete the legacy HKCU Run value `AI_Servers_Tray`.
- **Self-hosted fleet server:** none. This standalone version has no fleet-server, self-update or self-report integration. It uses no cloud APIs other than ntfy.
- **Successor:** the newer, merged version lives in the author's private monorepo. It is not linked from here.

## Configuration & Secrets

All runtime files live next to the scripts, in the install directory, and are gitignored. The only environment variable read is `APPDATA`, used to find the legacy Startup-folder shortcut so it can be removed.

| File | Written by | Contents |
|---|---|---|
| `config.json` | `tray.py` | `autostart_on_login`: a bool for each server key (`comfyui_gpu0`, `comfyui_gpu1`, `lm_studio`, `opencode_web`, `monitor`), plus `tray_autostart` (bool) |
| `monitor.json` | `monitor.py` (on first run) | `servers` (a list of `{name, url}`), `ntfy_server`, `ntfy_topic` (auto-generated `ai-monitor-<random>`), `poll_seconds`, `freeze_minutes`, `completion_min_seconds`, `http_timeout_seconds`, `notify_recovery`, `notify_on_start`, `notify_completion`, `notify_error`, `notify_freeze`, `traceback_lines` |
| `monitor_state.json` | `monitor.py` | Each server's `alive` and `freeze_notified_for_pid` |
| `tray.log`, `monitor.log` | Both programs | Append-only runtime logs, never rotated |

**Secrets.** `ntfy_topic` is the only sensitive value. Anyone who knows the topic can read and publish to it, so it works like a bearer token. It is stored only in `monitor.json`, but it is also written to `monitor.log` on every start and included in the body of the "AI monitor online" notification. Never commit `monitor.json` or the logs. There are no API keys, passwords, OAuth secrets or private keys in the repo or its git history (scanned 2026-10-10).

**Behaviour to know about:**
- `monitor.json` is read only at startup. Restart the monitor after editing it (Stop then Start the AI Monitor entry in the tray, or `ai-monitor-installer.exe reinstall`, answering N). This applies to edits made via the tray's "Open in browser" on the monitor entry too.
- If `ntfy_topic` is empty or contains `CHANGEME` (case-insensitive), notifications are dropped and the drop is logged.
- If `monitor.json` exists but cannot be parsed, the monitor falls back to defaults and **overwrites** the file. That gives it a new random topic, so the phone subscription stops matching.

## Testing

There are no automated tests, no test framework and no CI (no `.github/` directory). Verification is manual:
- Run the tray and check the bar colours and menu actions.
- Run the monitor and watch `monitor.log`, plus the startup phone notification (`notify_on_start`).
- Use the installer's `status` action to check install state.

The only check run for this audit was an offline `ast.parse` of all four `.py` files, which succeeded. Nothing was executed.

## Known Issues / TODO

The repo is archived, so none of these will be fixed here:

- **Hardcoded, machine-specific absolute paths** in `tray.py`, both installers and all three `.bat` files. Nothing is parameterized, and they must be edited for any other machine.
- **Stale docstrings:**
  - The `tray.py` module docstring still says tray autostart is an "HKCU Run-key entry" and mentions a `tray.pyw` launcher. The implementation uses a Scheduled Task, and no `tray.pyw` exists.
  - The `monitor.py` docstring gives absolute paths for its config, state and log files. The code actually uses paths relative to the script's directory.
- **Unpinned dependencies and runtime `pip install`s** (see Dependencies).
- **Monitor gaps:**
  - On a first run (no saved state for that server, e.g. a new or renamed server or a deleted or unreadable `monitor_state.json`) it sends no DOWN alert if the server is already down, because that first observation is recorded silently. Otherwise the first probe is compared with the last persisted state.
  - DOWN alerts cannot be switched off; there is no `notify_down` key.
- **Malformed `monitor.json` is overwritten** with defaults and a new topic (see Configuration & Secrets).
- **`ai-monitor-installer` reinstall can delete config.** `reinstall` (CLI, and the menu's "[R] Reinstall (overwrite files, keep config)") runs the uninstall step with a y/N prompt to delete files, `monitor.json` included. Answering `y` loses the config, despite the menu label.
- **Stale bar colour after an autostart toggle.** After toggling a server's autostart, its bar colour (yellow/red) doesn't update until the next state change (a running-state or Tailscale-IP change, or a Start/Stop/Kill).
- **`watt-tray-installer --yes` does nothing.**
- **Logs are never rotated.**
- **Windows-only**, with no tests and no CI.
- **Migrating to the successor.** Retire this version BEFORE installing the newer one:
  1. Untick "Tray app: Autostart on login" if it is ticked. This deletes the old `AI Servers Tray` task; do it only now, before the newer version is installed. Then Quit the tray. (Quitting alone leaves the task in place, so the old tray would come back at the next logon.)
  2. Stop the monitor: run `ai-monitor-installer.exe uninstall` and answer **N**, so `monitor.json` and your topic are kept.
  3. Make sure the old monitor task is gone: `schtasks /Delete /TN "AI Servers Monitor" /F` (the uninstaller already tries this; the command is a safety net).

  After the newer version is installed, do not run this repo's tray toggle or installers. The newer version reuses the `AI Servers Tray` task name, the same default install directory, the `monitor.py` filename and heartbeat port 9300, so these tools would kill, delete or overwrite its processes, task and files. It also registers a `WattTray` task, so `watt-tray-installer` would delete it and kill watt-tray (`uninstall`) or re-create it with limited rights (`install`, `reinstall`).

## Third-party & Licensing

- **License:** MIT. See `LICENSE`.
- **No vendored code or assets.** There is no third-party source, font, image, model or binary in the repo. The tray icon is drawn at runtime with Pillow. No NOTICE file is needed for the repo contents.
- **Not a fork.** History starts with one original commit.
- **Copyleft:**
  - `pystray` is **LGPL-3.0**, a weak copyleft licence. `tray.py` uses it as an unmodified PyPI library, and the installers do not package `tray.py`. If anyone bundles `tray.py` into a single executable, LGPL obligations apply: the user must be able to replace or relink the library.
  - PyInstaller is GPL, but its bootloader exception lets the built `.exe`s be distributed under the project's own terms.
- **Built installers.** The `.exe`s are not committed (`dist/` is gitignored). If they are distributed, include notices for what is inside them: the Python runtime (PSF License), `psutil` (BSD-3-Clause) and the PyInstaller bootloader.
- **Trademarks.** ComfyUI, LM Studio, OpenCode, ntfy, Tailscale and AMD GPU model numbers appear only as descriptive labels for the user's own servers and integrations. None of them is in the project name, a package id or an icon, and no affiliation is claimed. Risk is low (nominative use).

## Security notes

High level only. Details are in the owner's local audit.

- **Trust boundary.** This is a single-user local desktop tool:
  - The tray probes only loopback ports and launches local executables with argument lists (no shell strings).
  - The monitor talks to the configured ComfyUI URLs (loopback by default) and makes outbound HTTPS calls to the ntfy server.
  - The heartbeat listener binds to loopback only.
- **Auth model.** There is no authentication anywhere. ntfy delivery relies only on the topic being unguessable: it is randomly generated, and `monitor.json` is gitignored.
- **Where secrets live.** The ntfy topic lives in `monitor.json`, and also appears in `monitor.log` and the startup notification. There are no other secrets.
- **Known risk categories:**
  - Notification content goes to a third-party push service. Tracebacks and URLs can reveal workflow and path details.
  - Unpinned runtime dependency installation from PyPI (supply chain).
  - Network exposure of the managed servers. Their bind addresses come from their launch arguments, and not every one is loopback-only. Rely on a host firewall or private network, or change the launch arguments.
  - Identifying information in source: the hardcoded per-user absolute paths.
- **Scheduled tasks** are all created with the Limited run level.
