"""
AI Servers Monitor (v2)
=======================
Live ComfyUI watchdog: connects to each server's /ws WebSocket and tracks
real execution events. Sends ntfy push notifications when:

  * DOWN       - HTTP /system_stats unreachable
  * BACK UP    - previously-down server reachable again (optional)
  * FREEZE     - currently running a prompt but no WebSocket event has
                 fired for >= freeze_minutes (true progress stall, not
                 just "slow workflow")
  * ERROR      - an execution_error event arrived; includes exception
                 type, node, and a 6-line traceback excerpt
  * COMPLETED  - an execution_success event arrived; only sent if the
                 prompt ran >= completion_min_seconds (so we don't spam
                 for 5-second image previews)

Heartbeat: binds 127.0.0.1:9300 so the tray probe can show it as up.

Config:  D:\\AI\\TrayManager\\monitor.json
State:   D:\\AI\\TrayManager\\monitor_state.json
Log:     D:\\AI\\TrayManager\\monitor.log
"""
from __future__ import annotations

import json
import secrets
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG_PATH = SCRIPT_DIR / "monitor.json"
STATE_PATH = SCRIPT_DIR / "monitor_state.json"
LOG_PATH = SCRIPT_DIR / "monitor.log"
HEARTBEAT_PORT = 9300


# ---- Lazy install of websocket-client --------------------------------------
def _ensure_websocket():
    try:
        import websocket  # noqa: F401
        return
    except ImportError:
        pass
    try:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "--quiet", "websocket-client"],
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except Exception as e:
        _log(f"websocket-client install failed: {e!r}")
        sys.exit(1)


def _log(msg: str) -> None:
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
    except Exception:
        pass


# ---- Config / state IO ------------------------------------------------------
def _default_config() -> dict[str, Any]:
    return {
        "servers": [
            {"name": "ComfyUI 6950 XT", "url": "http://127.0.0.1:8188"},
            {"name": "ComfyUI 6900 XT", "url": "http://127.0.0.1:8189"},
        ],
        "ntfy_server": "https://ntfy.sh",
        "ntfy_topic": f"ai-monitor-{secrets.token_urlsafe(10)}",
        "poll_seconds": 15,
        "freeze_minutes": 8,
        "completion_min_seconds": 30,
        "http_timeout_seconds": 5,
        "notify_recovery": True,
        "notify_on_start": True,
        "notify_completion": True,
        "notify_error": True,
        "notify_freeze": True,
        "traceback_lines": 6,
    }


def load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))
            for k, v in _default_config().items():
                data.setdefault(k, v)
            return data
        except Exception as e:
            _log(f"config load failed, using defaults: {e!r}")
    cfg = _default_config()
    try:
        CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        _log(f"wrote default config; ntfy_topic={cfg['ntfy_topic']!r}")
    except Exception as e:
        _log(f"failed writing default config: {e!r}")
    return cfg


def load_state() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8-sig"))
        except Exception:
            pass
    return {"servers": {}}


def save_state(state: dict) -> None:
    try:
        STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except Exception as e:
        _log(f"state save failed: {e!r}")


# ---- HTTP helpers -----------------------------------------------------------
def http_get_json(url: str, timeout: float) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": "AIServersMonitor/2.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def http_post(url: str, body: bytes, headers: dict[str, str], timeout: float) -> None:
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        r.read()


def ntfy_push(cfg: dict, title: str, message: str,
              priority: int = 3, tags: list[str] | None = None) -> None:
    server = cfg.get("ntfy_server", "https://ntfy.sh").rstrip("/")
    topic = cfg.get("ntfy_topic", "")
    if not topic or "CHANGEME" in topic.upper():
        _log("ntfy_topic not set; notification dropped")
        return
    url = f"{server}/{topic}"
    headers = {
        "Title": title.encode("utf-8", "replace").decode("latin-1", "replace"),
        "Priority": str(priority),
        "Content-Type": "text/plain; charset=utf-8",
    }
    if tags:
        headers["Tags"] = ",".join(tags)
    try:
        http_post(url, message.encode("utf-8"), headers, timeout=8)
    except Exception as e:
        _log(f"ntfy push failed: {e!r} title={title!r}")


# ---- Heartbeat tcp port -----------------------------------------------------
def heartbeat_server() -> None:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind(("127.0.0.1", HEARTBEAT_PORT))
        s.listen(8)
    except OSError as e:
        _log(f"heartbeat bind failed: {e!r}")
        return
    while True:
        try:
            c, _ = s.accept()
            try:
                c.settimeout(2.0)
                c.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok")
            finally:
                try:
                    c.close()
                except Exception:
                    pass
        except Exception:
            pass


# ---- Per-server live watcher (WebSocket) -----------------------------------
class ServerWatcher(threading.Thread):
    """Persistent WebSocket connection to one ComfyUI server. Records the
    timestamp of every event and the current prompt's progress; the main
    loop reads this snapshot to decide if the server is frozen."""

    def __init__(self, srv: dict, cfg: dict, notif_queue: list, q_lock: threading.Lock):
        super().__init__(daemon=True, name=f"ws-{srv['name']}")
        self.srv = srv
        self.cfg = cfg
        self.notif_queue = notif_queue
        self.q_lock = q_lock

        parsed = urlparse(srv["url"])
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        ws_scheme = "wss" if parsed.scheme == "https" else "ws"
        client_id = uuid.uuid4().hex
        self.ws_url = f"{ws_scheme}://{host}:{port}/ws?clientId={client_id}"
        self.http_url = srv["url"].rstrip("/")

        self._lock = threading.Lock()
        self._last_event_at: float | None = None
        self._current_pid: str | None = None
        self._prompt_started_at: float | None = None
        self._current_node: Any = None
        self._connected: bool = False
        self._stop = threading.Event()

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "last_event_at": self._last_event_at,
                "current_pid": self._current_pid,
                "prompt_started_at": self._prompt_started_at,
                "current_node": self._current_node,
                "connected": self._connected,
            }

    def _stamp(self) -> None:
        with self._lock:
            self._last_event_at = time.time()

    def _queue_notif(self, kind: str, data: dict) -> None:
        with self.q_lock:
            self.notif_queue.append({
                "kind": kind, "server": self.srv["name"],
                "url": self.http_url, "data": data, "at": time.time(),
            })

    def _on_open(self, ws):
        with self._lock:
            self._connected = True
        self._stamp()
        _log(f"{self.srv['name']}: ws connected")

    def _on_close(self, ws, status, msg):
        with self._lock:
            self._connected = False
        _log(f"{self.srv['name']}: ws closed (status={status} msg={msg!r})")

    def _on_error(self, ws, err):
        # Most are transient connect errors during ComfyUI startup.
        _log(f"{self.srv['name']}: ws error: {err!r}")

    def _on_message(self, ws, message):
        # ComfyUI may send binary frames (image previews) - ignore.
        if isinstance(message, (bytes, bytearray)):
            self._stamp()
            return
        try:
            payload = json.loads(message)
        except Exception:
            self._stamp()
            return
        t = payload.get("type")
        d = payload.get("data") or {}
        self._stamp()

        if t == "execution_start":
            with self._lock:
                self._current_pid = d.get("prompt_id")
                self._prompt_started_at = time.time()
                self._current_node = None

        elif t == "executing":
            node = d.get("node")
            pid = d.get("prompt_id")
            with self._lock:
                self._current_node = node
                if pid is not None:
                    self._current_pid = pid
                if node is None:
                    # ComfyUI sends executing with node=null to signal end-
                    # of-graph for this prompt (older protocol). We treat
                    # execution_success as the canonical completion event.
                    pass

        elif t == "progress":
            with self._lock:
                self._current_node = d.get("node", self._current_node)

        elif t == "execution_error":
            self._queue_notif("error", d)

        elif t == "execution_success":
            with self._lock:
                started = self._prompt_started_at
                pid = self._current_pid
            elapsed = (time.time() - started) if started else 0.0
            self._queue_notif("success", {
                "prompt_id": d.get("prompt_id") or pid,
                "elapsed_seconds": elapsed,
            })
            with self._lock:
                self._current_pid = None
                self._prompt_started_at = None
                self._current_node = None

        elif t == "execution_interrupted":
            with self._lock:
                self._current_pid = None
                self._prompt_started_at = None
                self._current_node = None
        # other events (status, executed, execution_cached) just keep the
        # timestamp fresh, which is what we need for freeze detection.

    def run(self):
        import websocket  # imported here so _ensure_websocket() ran first
        while not self._stop.is_set():
            try:
                ws = websocket.WebSocketApp(
                    self.ws_url,
                    on_open=self._on_open,
                    on_message=self._on_message,
                    on_error=self._on_error,
                    on_close=self._on_close,
                )
                ws.run_forever(ping_interval=30, ping_timeout=10,
                               reconnect=0)  # we handle reconnect below
            except Exception as e:
                _log(f"{self.srv['name']}: ws thread exception: {e!r}")
            if not self._stop.is_set():
                time.sleep(5)  # backoff before reconnect


# ---- Notification formatting -----------------------------------------------
def _fmt_error_message(data: dict, traceback_lines: int) -> str:
    pid = data.get("prompt_id") or "?"
    node = data.get("node_type") or data.get("node_id") or "?"
    ex_type = data.get("exception_type") or "Exception"
    ex_msg = (data.get("exception_message") or "").strip()
    tb = data.get("traceback") or []
    if isinstance(tb, list):
        tb_text = "".join(tb)
    else:
        tb_text = str(tb)
    tb_excerpt = "\n".join(tb_text.strip().splitlines()[-traceback_lines:])
    return (
        f"node: {node}\n"
        f"prompt: {pid}\n"
        f"{ex_type}: {ex_msg}\n"
        f"--- traceback (tail) ---\n{tb_excerpt}"
    )


def _fmt_success_message(data: dict) -> str:
    pid = data.get("prompt_id") or "?"
    elapsed = float(data.get("elapsed_seconds") or 0)
    if elapsed >= 60:
        elapsed_str = f"{elapsed/60:.1f} min"
    else:
        elapsed_str = f"{elapsed:.1f} s"
    return f"Prompt {pid} done in {elapsed_str}."


# ---- HTTP alive check (DOWN/RECOVERY) ---------------------------------------
def check_alive(srv: dict, cfg: dict) -> tuple[bool, str | None]:
    try:
        http_get_json(srv["url"].rstrip("/") + "/system_stats",
                      timeout=cfg["http_timeout_seconds"])
        return True, None
    except Exception as e:
        return False, repr(e)[:300]


# ---- Main loop --------------------------------------------------------------
def main() -> None:
    _ensure_websocket()  # blocks until websocket-client is importable

    cfg = load_config()
    state = load_state()
    state.setdefault("servers", {})

    threading.Thread(target=heartbeat_server, daemon=True).start()
    _log(f"monitor v2 started; topic={cfg['ntfy_topic']!r} "
         f"poll={cfg['poll_seconds']}s freeze>={cfg['freeze_minutes']}min "
         f"complete>={cfg['completion_min_seconds']}s")

    if cfg.get("notify_on_start"):
        ntfy_push(
            cfg, "AI monitor online",
            (f"Watching {len(cfg['servers'])} ComfyUI servers.\n"
             f"freeze>={cfg['freeze_minutes']}min, "
             f"completion>={cfg['completion_min_seconds']}s\n"
             f"topic: {cfg['ntfy_topic']}"),
            priority=2, tags=["satellite_antenna"],
        )

    notif_queue: list[dict] = []
    q_lock = threading.Lock()
    watchers: dict[str, ServerWatcher] = {}
    for srv in cfg["servers"]:
        w = ServerWatcher(srv, cfg, notif_queue, q_lock)
        w.start()
        watchers[srv["name"]] = w

    while True:
        try:
            now = time.time()

            # 1) DOWN / RECOVERY transitions via REST probe ------------------
            for srv in cfg["servers"]:
                alive, err = check_alive(srv, cfg)
                prev = state["servers"].setdefault(srv["name"], {})
                prev_alive = prev.get("alive")
                if prev_alive is None:
                    pass  # first observation; record only
                elif alive and not prev_alive:
                    if cfg.get("notify_recovery", True):
                        ntfy_push(cfg,
                                  f"[OK] {srv['name']} back up",
                                  f"{srv['url']} is reachable again.",
                                  priority=3, tags=["white_check_mark"])
                elif not alive and prev_alive:
                    ntfy_push(cfg,
                              f"[DOWN] {srv['name']} unreachable",
                              (err or "no /system_stats response")
                              + f"\n{srv['url']}",
                              priority=4, tags=["rotating_light"])
                prev["alive"] = alive
                prev.setdefault("freeze_notified_for_pid", None)

            # 2) FREEZE detection via WebSocket idle timer -------------------
            if cfg.get("notify_freeze", True):
                freeze_seconds = max(60.0, cfg["freeze_minutes"] * 60.0)
                for srv in cfg["servers"]:
                    w = watchers[srv["name"]]
                    snap = w.snapshot()
                    pid = snap["current_pid"]
                    last_evt = snap["last_event_at"]
                    if pid and snap["connected"] and last_evt:
                        idle = now - last_evt
                        prev = state["servers"][srv["name"]]
                        already = prev.get("freeze_notified_for_pid") == pid
                        if idle >= freeze_seconds and not already:
                            ntfy_push(
                                cfg,
                                f"[FREEZE] {srv['name']} stalled",
                                (f"Prompt {pid} has produced no WebSocket "
                                 f"events for {idle/60:.1f} min "
                                 f"(threshold {cfg['freeze_minutes']} min).\n"
                                 f"Last node: {snap['current_node']}\n"
                                 f"{srv['url']}"),
                                priority=4, tags=["warning"],
                            )
                            prev["freeze_notified_for_pid"] = pid
                    elif not pid:
                        state["servers"][srv["name"]]["freeze_notified_for_pid"] = None

            # 3) Drain success/error notifications queued by WS threads ------
            with q_lock:
                drained, notif_queue[:] = list(notif_queue), []
            for n in drained:
                if n["kind"] == "success" and cfg.get("notify_completion", True):
                    elapsed = float(n["data"].get("elapsed_seconds") or 0)
                    if elapsed >= cfg["completion_min_seconds"]:
                        ntfy_push(
                            cfg,
                            f"[DONE] {n['server']} prompt completed",
                            _fmt_success_message(n["data"])
                            + f"\n{n['url']}",
                            priority=3, tags=["white_check_mark"],
                        )
                elif n["kind"] == "error" and cfg.get("notify_error", True):
                    ntfy_push(
                        cfg,
                        f"[ERROR] {n['server']} execution failed",
                        _fmt_error_message(n["data"], cfg["traceback_lines"])
                        + f"\n{n['url']}",
                        priority=5, tags=["x"],
                    )

            save_state(state)
        except Exception as e:
            _log(f"main loop iteration failed: {e!r}")
        time.sleep(max(5, int(cfg.get("poll_seconds", 15))))


if __name__ == "__main__":
    main()
