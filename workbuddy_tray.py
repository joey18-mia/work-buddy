"""
WorkBuddy Tray
==============
Entry point.  Double-click this (or run: python workbuddy_tray.py)

System tray icon with right-click menu:
  • Open Dashboard
  • Open Console
  • List Connections  →  logs to console page
  • Restart Server
  • Stop Server       →  removes icon, exits

Tray icon:
  Looks for  workbuddy/tray_icon.png  first.
  Falls back to a generated teal circle with ⚾ if not found.
"""

import os
import sys
import time
import socket
import threading
import webbrowser
import requests as _req

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PORT     = int(os.environ.get("PORT", 5000))
BASE_URL = f"http://localhost:{PORT}"


# ─────────────────────────────────────────────────────────────
#  ICON  — load custom or generate fallback
# ─────────────────────────────────────────────────────────────

def _load_icon():
    from PIL import Image, ImageDraw, ImageFont

    custom = os.path.join(BASE_DIR, "tray_icon.png")
    if os.path.exists(custom):
        try:
            img = Image.open(custom).convert("RGBA")
            img = img.resize((64, 64), Image.LANCZOS)
            return img
        except Exception:
            pass  # Fall through to generated icon

    # Generated fallback: teal circle with ⚾
    size  = 64
    img   = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw  = ImageDraw.Draw(img)
    teal  = (0, 163, 224, 255)
    draw.ellipse([2, 2, size - 2, size - 2], fill=teal)
    # Draw a simple baseball-stitch "B" shape
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
    """Target for the server thread.  Imports app here to avoid circular issues."""
    global _server_obj
    from app import app, api_log
    try:
        from waitress import create_server
        with _server_lock:
            _server_obj = create_server(app, host="0.0.0.0", port=PORT, threads=8)
        api_log.info(f"SERVER   | Waitress started on port {PORT} | PID={os.getpid()}")
        _server_obj.run()   # blocks until _server_obj.close() is called
        api_log.info("SERVER   | Waitress stopped")
    except ImportError:
        api_log.info(f"SERVER   | Flask dev server on port {PORT}")
        app.run(host="0.0.0.0", port=PORT, debug=False, threaded=True, use_reloader=False)


def _start_server_thread():
    global _server_thread
    _server_thread = threading.Thread(target=_run_flask, daemon=True, name="WorkBuddy-Flask")
    _server_thread.start()
    # Give Flask a moment to bind before opening browser
    _wait_for_server()


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


def _stop_server():
    global _server_obj
    with _server_lock:
        if _server_obj is not None:
            try:
                _server_obj.close()
            except Exception:
                pass
            _server_obj = None


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
        # Open console so user sees the output
        webbrowser.open(f"{BASE_URL}/console")
    except Exception as e:
        print(f"[Tray] List connections error: {e}")


def _restart_server(icon, item):
    from app import api_log
    api_log.info("SERVER   | Restart requested via tray")
    _stop_server()
    time.sleep(1)
    _start_server_thread()
    api_log.info("SERVER   | Restart complete")


def _stop_and_exit(icon, item):
    from app import api_log
    api_log.info("SERVER   | Stop requested via tray — shutting down")
    _stop_server()
    icon.stop()     # removes tray icon, returns control to main thread


# ─────────────────────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────────────────────

def main():
    # Initialise DB before anything starts
    from database import init_db
    import app as _app_module
    init_db()

    # Write PID / uptime
    with open(os.path.join(BASE_DIR, "workbuddy.pid"), "w") as f:
        f.write(str(os.getpid()))
    with open(os.path.join(BASE_DIR, "workbuddy.uptime"), "w") as f:
        f.write(str(time.time()))

    # Start Flask in background thread
    print(f"[WorkBuddy] Starting server on port {PORT}...")
    _start_server_thread()
    print(f"[WorkBuddy] Running → {BASE_URL}")
    print(f"[WorkBuddy] Console  → {BASE_URL}/console")
    print("[WorkBuddy] Tray icon active. Right-click to manage.")

    # Build tray icon (pystray must run on main thread on Windows)
    try:
        import pystray
        from pystray import MenuItem as Item, Menu

        icon_image = _load_icon()

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
            icon=icon_image,
            title="WorkBuddy",
            menu=menu,
        )
        tray.run()   # blocks main thread; returns after icon.stop() is called

    except ImportError:
        print("[WorkBuddy] pystray not installed — running without tray icon.")
        print(f"           Install with:  pip install pystray")
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
