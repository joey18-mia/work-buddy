"""
WorkBuddy Tray
==============
Entry point.  Double-click this (or run: python workbuddy_tray.py)

System tray icon with right-click menu:
  • Open Dashboard
  • Open Console
  • List Connections  →  logs to console page
  • Restart Server    →  full process respawn (reloads .env + code)
  • Stop Server       →  removes icon, exits

Tray icon:
  Looks for  tray_icon.png / static/icons/tray_icon.png  first.
  Falls back to a generated teal circle if not found.
"""

import os
import sys
import time
import socket
import subprocess
import threading
import webbrowser
import requests as _req

# Windows consoles default to cp1252; force UTF-8 so print()/logging with
# arrows or emoji never raises UnicodeEncodeError.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PORT     = int(os.environ.get("PORT", 5000))
BASE_URL = f"http://localhost:{PORT}"


# ─────────────────────────────────────────────────────────────
#  ICON  — load custom or generate fallback
# ─────────────────────────────────────────────────────────────

def _load_icon():
    from PIL import Image, ImageDraw

    for name in ("tray_icon.png", os.path.join("static", "icons", "tray_icon.png")):
        custom = os.path.join(BASE_DIR, name)
        if os.path.exists(custom):
            try:
                img = Image.open(custom).convert("RGBA")
                img = img.resize((64, 64), Image.LANCZOS)
                return img
            except Exception:
                pass

    # Generated fallback: teal circle
    size  = 64
    img   = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw  = ImageDraw.Draw(img)
    teal  = (0, 163, 224, 255)
    draw.ellipse([2, 2, size - 2, size - 2], fill=teal)
    white = (255, 255, 255, 255)
    cx, cy, r = size // 2, size // 2, size // 4
    draw.arc([cx - r, cy - r, cx + r, cy + r], start=30,  end=150, fill=white, width=3)
    draw.arc([cx - r, cy - r, cx + r, cy + r], start=210, end=330, fill=white, width=3)
    return img


# ─────────────────────────────────────────────────────────────
#  FLASK SERVER — runs in a background daemon thread
# ─────────────────────────────────────────────────────────────

_server_obj    = None
_server_thread = None
_server_lock   = threading.Lock()


def _run_flask():
    """Target for the server thread. Always uses a stoppable WSGI server."""
    global _server_obj
    from app import app, api_log
    try:
        from waitress import create_server
        # channel_timeout must be high — EventSource clients never send
        # follow-up request body bytes; Waitress would otherwise drop SSE ~2min.
        srv = create_server(
            app, host="0.0.0.0", port=PORT, threads=8,
            channel_timeout=3600, asyncore_use_poll=True,
        )
        with _server_lock:
            _server_obj = srv
        api_log.info(f"SERVER   | Waitress started on port {PORT} | PID={os.getpid()}")
        srv.run()  # blocks until srv.close()
        api_log.info("SERVER   | Waitress stopped")
    except ImportError:
        # Stoppable fallback — app.run() cannot be stopped by the tray.
        from werkzeug.serving import make_server
        srv = make_server("0.0.0.0", PORT, app, threaded=True)
        with _server_lock:
            _server_obj = srv
        api_log.info(f"SERVER   | Werkzeug server on port {PORT} | PID={os.getpid()}")
        api_log.info("SERVER   | Tip: pip install waitress  (preferred production server)")
        srv.serve_forever()
        api_log.info("SERVER   | Werkzeug server stopped")
    except OSError as e:
        api_log.info(f"SERVER   | Failed to bind port {PORT}: {e}")
    finally:
        with _server_lock:
            if _server_obj is not None:
                _server_obj = None


def _start_server_thread():
    global _server_thread
    _server_thread = threading.Thread(target=_run_flask, daemon=True, name="WorkBuddy-Flask")
    _server_thread.start()
    return _wait_for_server()


def _wait_for_server(timeout: int = 10):
    """Poll localhost until server responds or timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            _req.get(f"{BASE_URL}/api/admin/status", timeout=1)
            return True
        except Exception:
            time.sleep(0.3)
    return False


def _port_in_use() -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", PORT)) == 0
    finally:
        s.close()


def _wait_for_port_free(timeout: float = 8.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not _port_in_use():
            return True
        time.sleep(0.2)
    return not _port_in_use()


def _stop_server():
    """Close the WSGI server and wait for the thread / port to release."""
    global _server_obj, _server_thread
    with _server_lock:
        obj = _server_obj
        _server_obj = None
    if obj is not None:
        try:
            # Waitress: close(); Werkzeug: shutdown() (+ server_close)
            if hasattr(obj, "close"):
                obj.close()
            if hasattr(obj, "shutdown"):
                obj.shutdown()
            if hasattr(obj, "server_close"):
                obj.server_close()
        except Exception:
            pass
    t = _server_thread
    if t is not None and t.is_alive():
        t.join(timeout=8)
    _server_thread = None
    _wait_for_port_free()


# ─────────────────────────────────────────────────────────────
#  TRAY MENU ACTIONS
# ─────────────────────────────────────────────────────────────

def _open_dashboard(icon, item):
    webbrowser.open(BASE_URL)


def _open_console(icon, item):
    webbrowser.open(f"{BASE_URL}/console")


def _list_connections(icon, item):
    """Fetches connected devices and logs them to the console page."""
    try:
        r    = _req.get(f"{BASE_URL}/api/admin/status", timeout=5)
        data = r.json()
        conns = data.get("connections", [])
        msg   = f"LIST     | {len(conns)} device(s) active in last 30 min"
        _req.post(f"{BASE_URL}/api/console/log", json={"message": msg}, timeout=3)
        for c in conns:
            name = c['hostname'] if c['hostname'] != c['ip'] else '(no hostname)'
            line = f"LIST     |   {c['ip']:<18} {name:<30} last seen {c['last_seen']}"
            _req.post(f"{BASE_URL}/api/console/log", json={"message": line}, timeout=3)
        if not conns:
            _req.post(f"{BASE_URL}/api/console/log",
                      json={"message": "LIST     | No active connections in last 30 min"}, timeout=3)
        webbrowser.open(f"{BASE_URL}/console")
    except Exception as e:
        print(f"[Tray] List connections error: {e}")


def _restart_server(icon, item):
    """
    True restart: stop the listener, spawn a fresh process in a new CMD
    window (so live print logs come back), then exit this one.
    """
    try:
        from app import api_log
        api_log.info("SERVER   | Restart requested via tray — respawning process")
    except Exception:
        print("[WorkBuddy] Restart requested via tray — respawning process")

    _stop_server()

    script = os.path.abspath(__file__)
    # Prefer the .bat launcher so the new window title/cwd match a normal start.
    bat = os.path.join(BASE_DIR, "workbuddy.bat")
    if sys.platform == "win32" and os.path.isfile(bat):
        args = ["cmd.exe", "/k", bat]
    else:
        args = [sys.executable, script] + sys.argv[1:]

    kwargs = {"cwd": BASE_DIR, "close_fds": True}
    if sys.platform == "win32":
        # New visible console (NOT DETACHED_PROCESS — that hid the CMD log).
        kwargs["creationflags"] = (
            subprocess.CREATE_NEW_CONSOLE | subprocess.CREATE_NEW_PROCESS_GROUP
        )

    try:
        subprocess.Popen(args, **kwargs)
    except Exception as e:
        print(f"[WorkBuddy] Failed to respawn: {e}")
        if not _start_server_thread():
            print("[WorkBuddy] Server failed to come back after failed respawn")
        return

    icon.stop()


def _stop_and_exit(icon, item):
    try:
        from app import api_log
        api_log.info("SERVER   | Stop requested via tray — shutting down")
    except Exception:
        pass
    _stop_server()
    icon.stop()


# ─────────────────────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────────────────────

def main():
    from database import init_db
    import app as _app_module  # noqa: F401 — load app + logging
    init_db()
    _app_module.start_log_prune_scheduler()

    with open(os.path.join(BASE_DIR, "workbuddy.pid"), "w") as f:
        f.write(str(os.getpid()))
    with open(os.path.join(BASE_DIR, "workbuddy.uptime"), "w") as f:
        f.write(str(time.time()))

    print(f"[WorkBuddy] Starting server on port {PORT}...")
    if not _start_server_thread():
        print(f"[WorkBuddy] WARNING: server did not respond on {BASE_URL}")
    else:
        print(f"[WorkBuddy] Running → {BASE_URL}")
        print(f"[WorkBuddy] Console  → {BASE_URL}/console")
    print("[WorkBuddy] Tray icon active. Right-click to manage.")

    try:
        import pystray
        from pystray import MenuItem as Item, Menu

        menu = Menu(
            Item("⚾ Open Dashboard",      _open_dashboard,    default=True),
            Item("🖥  Open Console",        _open_console),
            Menu.SEPARATOR,
            Item("📋 List Connections",    _list_connections),
            Menu.SEPARATOR,
            Item("🔄 Restart Server",      _restart_server),
            Item("⏹  Stop Server",         _stop_and_exit),
        )

        tray = pystray.Icon(
            name="WorkBuddy",
            icon=_load_icon(),
            title="WorkBuddy",
            menu=menu,
        )
        tray.run()

    except ImportError:
        print("[WorkBuddy] pystray not installed — running without tray icon.")
        print("           Install with:  pip install pystray")
        print("           Server is running. Press Ctrl+C to stop.")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\n[WorkBuddy] Stopping...")
            _stop_server()

    print("[WorkBuddy] Exited.")
    sys.exit(0)


if __name__ == "__main__":
    main()
