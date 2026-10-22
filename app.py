import os
import sys
import json
import math
import time
import queue
import logging
import socket
import threading
import requests

# Windows consoles default to cp1252, which can't encode arrows/emoji used in
# our log lines. Force UTF-8 so logging never crashes on non-ASCII characters.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
from datetime import date, timedelta, datetime
from flask import (Flask, Response, jsonify, redirect, render_template,
                   request, stream_with_context, url_for)
from dotenv import load_dotenv

load_dotenv()

from database import (get_calendar_db, get_routes_db, get_settings_db,
                      init_db, DATABASES_DIR)
import auth as wb_auth

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "dev-fallback-change-me")
# Cookie max age matches auth lifetime; actual expiry is enforced via auth_at.
app.permanent_session_lifetime = timedelta(hours=wb_auth.SESSION_LIFETIME_HOURS)
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

OPENWEATHER_KEY = os.getenv("OPENWEATHER_API_KEY", "")
TOMTOM_KEY      = os.getenv("TOMTOM_API_KEY", "")
LAT             = os.getenv("LOCATION_LAT",  "")
LON             = os.getenv("LOCATION_LON",  "")
LOCATION_NAME   = os.getenv("LOCATION_NAME", "")
BASE_DIR        = os.path.dirname(os.path.abspath(__file__))

TRAFFIC_CACHE = {"data": None, "ts": 0}
WEATHER_CACHE = {"data": None, "ts": 0}
CACHE_TTL     = 4 * 60 * 60
CONNECTIONS: dict = {}
PID_FILE    = os.path.join(BASE_DIR, "workbuddy.pid")
UPTIME_FILE = os.path.join(BASE_DIR, "workbuddy.uptime")

# In-memory geofence state for active live sessions (NOT a breadcrumb trail).
# { session_id: {"passed": set[int], "dwell_start": float|None,
#                "last_lat": float, "last_lng": float, "last_loc_at": float} }
SESSION_STATE: dict = {}
_session_lock = threading.Lock()
GPS_STALE_SECS = 90  # no fix within this window → report gps_ok=False

# ─────────────────────────────────────────────────────────────
#  LOGGING + SSE
# ─────────────────────────────────────────────────────────────

LOG_PATH     = os.path.join(BASE_DIR, "workbuddy_api.log")
LOG_HTML_HOURS = 24          # HTML console seeds / retains this window
LOG_KEEP_DAYS  = 14          # prune non-error file lines older than this
LOG_ERROR_KEEP_DAYS = 90     # keep ERROR/FAILED lines longer on disk
_sse_clients = []
_sse_lock    = threading.Lock()
_log_prune_lock = threading.Lock()

class _SSEHandler(logging.Handler):
    def emit(self, record):
        msg = self.format(record)
        with _sse_lock:
            dead = []
            for q in _sse_clients:
                try:    q.put_nowait(msg)
                except: dead.append(q)
            for q in dead: _sse_clients.remove(q)

_fh = logging.FileHandler(LOG_PATH, encoding="utf-8")
_fh.setFormatter(logging.Formatter("%(asctime)s  %(message)s", "%Y-%m-%d %H:%M:%S"))
_sh = _SSEHandler()
_sh.setFormatter(logging.Formatter("%(asctime)s  %(message)s", "%Y-%m-%d %H:%M:%S"))
api_log = logging.getLogger("workbuddy")
api_log.setLevel(logging.INFO)
api_log.addHandler(_fh)
api_log.addHandler(_sh)
api_log.propagate = False


def _parse_log_ts(line: str):
    """Parse leading 'YYYY-MM-DD HH:MM:SS' from a log line → datetime or None."""
    if len(line) < 19:
        return None
    try:
        return datetime.strptime(line[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def _is_error_line(line: str) -> bool:
    u = line.upper()
    return "ERROR" in u or "FAILED" in u or "TRACEBACK" in u or "ASSERTIONERROR" in u


def _read_log_lines_since(hours: float):
    """Return log lines with timestamp within the last `hours` (or unparseable kept)."""
    if not os.path.exists(LOG_PATH):
        return []
    cutoff = datetime.now() - timedelta(hours=hours)
    out = []
    try:
        with open(LOG_PATH, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                raw = line.rstrip("\n\r")
                if not raw:
                    continue
                ts = _parse_log_ts(raw)
                if ts is None or ts >= cutoff:
                    out.append(raw)
    except OSError:
        return []
    return out


def prune_log_file(force: bool = False) -> dict:
    """
    Trim workbuddy_api.log:
      • normal lines older than LOG_KEEP_DAYS → dropped
      • ERROR/FAILED lines kept for LOG_ERROR_KEEP_DAYS
    Safe to call while the server is running (reopens the FileHandler).
    """
    result = {"pruned": False, "before": 0, "after": 0, "removed": 0}
    if not os.path.exists(LOG_PATH):
        return result
    if not _log_prune_lock.acquire(blocking=force):
        return result
    try:
        try:
            with open(LOG_PATH, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except OSError:
            return result

        result["before"] = len(lines)
        now = datetime.now()
        keep_cut = now - timedelta(days=LOG_KEEP_DAYS)
        err_cut  = now - timedelta(days=LOG_ERROR_KEEP_DAYS)
        kept = []
        for line in lines:
            raw = line.rstrip("\n\r")
            if not raw:
                continue
            ts = _parse_log_ts(raw)
            if ts is None:
                kept.append(raw)
                continue
            if _is_error_line(raw):
                if ts >= err_cut:
                    kept.append(raw)
            elif ts >= keep_cut:
                kept.append(raw)

        result["after"] = len(kept)
        result["removed"] = result["before"] - result["after"]
        if result["removed"] <= 0:
            return result

        # Rewrite file under the same path; swap FileHandler so Windows releases the handle.
        global _fh
        api_log.removeHandler(_fh)
        try:
            _fh.close()
        except Exception:
            pass
        try:
            with open(LOG_PATH, "w", encoding="utf-8", errors="replace") as f:
                for raw in kept:
                    f.write(raw + "\n")
            result["pruned"] = True
        finally:
            _fh = logging.FileHandler(LOG_PATH, encoding="utf-8")
            _fh.setFormatter(logging.Formatter("%(asctime)s  %(message)s", "%Y-%m-%d %H:%M:%S"))
            api_log.addHandler(_fh)
        return result
    finally:
        _log_prune_lock.release()


_log_prune_thread_started = False

def start_log_prune_scheduler():
    """Run an immediate prune, then once a day. Idempotent."""
    global _log_prune_thread_started
    if _log_prune_thread_started:
        return
    _log_prune_thread_started = True

    def _loop():
        while True:
            try:
                r = prune_log_file(force=True)
                if r.get("pruned"):
                    api_log.info(
                        f"SERVER   | Log pruned | removed={r['removed']} kept={r['after']} "
                        f"(keep={LOG_KEEP_DAYS}d, errors={LOG_ERROR_KEEP_DAYS}d)"
                    )
            except Exception as e:
                try:
                    api_log.info(f"SERVER   | Log prune error: {e}")
                except Exception:
                    pass
            time.sleep(24 * 3600)

    threading.Thread(target=_loop, daemon=True, name="WorkBuddy-LogPrune").start()


@app.before_request
def track_connection():
    ip = request.remote_addr
    if ip:
        hostname = CONNECTIONS[ip]["hostname"] if ip in CONNECTIONS else ip
        try:
            if ip not in CONNECTIONS:
                hostname = socket.gethostbyaddr(ip)[0]
        except Exception:
            pass
        CONNECTIONS[ip] = {"last_seen": time.time(), "hostname": hostname}


@app.before_request
def require_auth():
    """Phase 2.5 — gate pages + /api/* when WORKBUDDY_AUTH_ENABLED=true."""
    if not wb_auth.AUTH_ENABLED:
        return None
    if wb_auth.is_auth_exempt(request.path):
        return None
    if wb_auth.session_valid():
        wb_auth.touch_active()
        return None
    # Stale / missing login cookie
    wb_auth.clear_auth()
    if request.path.startswith("/api/"):
        return jsonify({"error": "unauthorized", "message": "Session expired — log in again"}), 401
    return redirect(url_for("login", next=request.full_path if request.query_string else request.path))


@app.context_processor
def inject_auth_flags():
    return {"auth_enabled": wb_auth.AUTH_ENABLED}


# ─────────────────────────────────────────────────────────────
#  AUTH (Phase 2.5)
# ─────────────────────────────────────────────────────────────

def _safe_next_url(raw: str | None) -> str:
    """Only allow relative same-origin redirects after login."""
    if not raw:
        return ""
    raw = raw.strip()
    if not raw.startswith("/") or raw.startswith("//"):
        return ""
    return raw


def _default_post_login_url() -> str:
    ua = (request.headers.get("User-Agent") or "").lower()
    mobile_hints = ("iphone", "android", "ipad", "mobile")
    if any(h in ua for h in mobile_hints):
        return "/m/"
    return "/"


@app.route("/login", methods=["GET", "POST"])
def login():
    next_url = _safe_next_url(request.values.get("next")) or _default_post_login_url()

    # Already signed in → bounce to destination
    if not wb_auth.AUTH_ENABLED or wb_auth.session_valid():
        return redirect(next_url)

    error = None
    if request.method == "POST":
        ip = request.remote_addr
        if wb_auth.login_rate_limited(ip):
            return render_template(
                "login.html",
                error="Too many attempts. Try again later.",
                next=next_url,
                rate_limited=True,
            ), 429
        password = request.form.get("password", "")
        if wb_auth.verify_password(password):
            wb_auth.mark_authenticated()
            wb_auth.clear_login_failures(ip)
            api_log.info(f"Auth: login ok from {ip}")
            return redirect(next_url)
        wb_auth.record_login_failure(ip)
        api_log.info(f"Auth: login failed from {ip}")
        error = "Invalid password"

    return render_template("login.html", error=error, next=next_url, rate_limited=False)


@app.route("/logout", methods=["GET", "POST"])
def logout():
    wb_auth.clear_auth()
    if wb_auth.AUTH_ENABLED:
        return redirect(url_for("login"))
    return redirect("/")


# ─────────────────────────────────────────────────────────────
#  PAGES
# ─────────────────────────────────────────────────────────────

@app.route("/")
def index():
    def _opt_float(key):
        v = os.getenv(key, "").strip()
        try:
            return float(v) if v else None
        except ValueError:
            return None
    return render_template(
        "index.html",
        location=LOCATION_NAME or "Local",
        map_lat=_opt_float("LOCATION_LAT"),
        map_lng=_opt_float("LOCATION_LON"),
    )

@app.route("/console")
def console():
    return render_template("console.html")


# ─────────────────────────────────────────────────────────────
#  MOBILE PAGES  (/m/)  — bookmarkable, server-rendered
# ─────────────────────────────────────────────────────────────

@app.route("/m/")
def m_home():
    return render_template("mobile/home.html")

@app.route("/m/routes")
def m_routes():
    return render_template("mobile/routes.html")

@app.route("/m/routes/<int:route_id>")
def m_route_detail(route_id):
    return render_template("mobile/route_detail.html", route_id=route_id)

@app.route("/m/calendar")
def m_calendar():
    return render_template("mobile/calendar.html")

@app.route("/m/settings")
def m_settings():
    return render_template("mobile/settings.html")

@app.route("/m/manifest.webmanifest")
def m_manifest():
    # Icons derived from tray_icon.png — used for Add to Home Screen / PWA.
    manifest = {
        "name": "WorkBuddy",
        "short_name": "WorkBuddy",
        "start_url": "/m/",
        "scope": "/m/",
        "display": "standalone",
        "orientation": "portrait",
        "background_color": "#060d18",
        "theme_color": "#0b1525",
        "description": "Live route sessions and schedule for WorkBuddy.",
        "icons": [
            {
                "src": "/static/icons/icon-192.png",
                "sizes": "192x192",
                "type": "image/png",
                "purpose": "any",
            },
            {
                "src": "/static/icons/icon-512.png",
                "sizes": "512x512",
                "type": "image/png",
                "purpose": "any",
            },
            {
                "src": "/static/icons/apple-touch-icon.png",
                "sizes": "180x180",
                "type": "image/png",
                "purpose": "any",
            },
        ],
    }
    return Response(json.dumps(manifest), mimetype="application/manifest+json")


@app.route("/favicon.ico")
def favicon():
    """Browsers auto-request /favicon.ico; serve the tray-derived icon."""
    return app.send_static_file("icons/favicon.ico")


# ─────────────────────────────────────────────────────────────
#  CONSOLE SSE
# ─────────────────────────────────────────────────────────────

@app.route("/api/console/stream")
def console_stream():
    def generate():
        cq = queue.Queue(maxsize=500)
        with _sse_lock: _sse_clients.append(cq)
        try:
            # Pad first event so proxies/waitress flush the stream promptly
            yield ": connected\n\n"
            # Seed HTML console with last 24h only (not the full on-disk log)
            for line in _read_log_lines_since(LOG_HTML_HOURS):
                yield f"data: {line}\n\n"
            while True:
                try:
                    yield f"data: {cq.get(timeout=20)}\n\n"
                except queue.Empty:
                    # Comment frame — keeps intermediaries from idling out the socket
                    yield f": ping {int(time.time())}\n\n"
        except GeneratorExit:
            pass
        finally:
            with _sse_lock:
                if cq in _sse_clients: _sse_clients.remove(cq)
    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )

@app.route("/api/console/log", methods=["POST"])
def console_log():
    msg = (request.json or {}).get("message","")
    if msg: api_log.info(msg)
    return jsonify({"ok": True})


# ─────────────────────────────────────────────────────────────
#  WEATHER
# ─────────────────────────────────────────────────────────────

@app.route("/api/weather")
def weather():
    if not OPENWEATHER_KEY:
        return jsonify({"error":"no_key"}), 503
    force = request.args.get("force","0") == "1"
    now   = time.time()
    if not force and WEATHER_CACHE["data"] and now - WEATHER_CACHE["ts"] < CACHE_TTL:
        return jsonify(WEATHER_CACHE["data"])
    try:
        r = requests.get(
            f"https://api.openweathermap.org/data/2.5/weather"
            f"?lat={LAT}&lon={LON}&appid={OPENWEATHER_KEY}&units=imperial", timeout=8)
        r.raise_for_status()
        d = r.json()
        result = {"temp":round(d["main"]["temp"]), "description":d["weather"][0]["description"].title()}
        WEATHER_CACHE["data"] = result; WEATHER_CACHE["ts"] = now
        api_log.info(f"WEATHER  | OpenWeatherMap | {request.remote_addr} | {'FORCED' if force else 'scheduled'}")
        return jsonify(result)
    except Exception as e:
        api_log.error(f"WEATHER  | Error: {e}")
        return jsonify({"error":str(e)}), 502


# ─────────────────────────────────────────────────────────────
#  TRAFFIC
# ─────────────────────────────────────────────────────────────

CORRIDORS = [
    {"id":"turnpike", "name":"Florida's Turnpike",         "point":"25.7450,-80.3380","default":True},
    {"id":"palmetto", "name":"Palmetto (S of NW 36th St)", "point":"25.7700,-80.3200","default":True},
    {"id":"836_east", "name":"SR-836 East  (@ 17th Ave)",  "point":"25.7720,-80.2140","default":True},
    {"id":"836_west", "name":"SR-836 West  (@ 107th Ave)", "point":"25.7735,-80.3560","default":True},
    {"id":"i95_north","name":"I-95 Northbound",            "point":"25.8200,-80.1800","default":False},
    {"id":"i95_south","name":"I-95 Southbound",            "point":"25.7400,-80.1900","default":False},
    {"id":"us1",      "name":"US-1 South",                 "point":"25.6800,-80.3100","default":False},
    {"id":"i75",      "name":"I-75 Homestead Ext.",        "point":"25.7000,-80.4000","default":False},
]

@app.route("/api/corridors")
def get_corridors():
    return jsonify(CORRIDORS)

@app.route("/api/traffic")
def traffic():
    if not TOMTOM_KEY:
        return jsonify({"error":"no_key"}), 503
    force = request.args.get("force","0") == "1"
    req   = request.args.get("ids","")
    now   = time.time()
    if not force and TRAFFIC_CACHE["data"] and now - TRAFFIC_CACHE["ts"] < CACHE_TTL:
        cached = TRAFFIC_CACHE["data"]
        if req: cached = [c for c in cached if c["id"] in req.split(",")]
        return jsonify(cached)
    corridors = [c for c in CORRIDORS if c["id"] in req.split(",")] if req else CORRIDORS
    results   = []
    for c in corridors:
        try:
            r = requests.get(
                f"https://api.tomtom.com/traffic/services/4/flowSegmentData/absolute/10/json"
                f"?point={c['point']}&key={TOMTOM_KEY}", timeout=8)
            r.raise_for_status()
            d = r.json().get("flowSegmentData",{})
            cur = d.get("currentSpeed",0); free = d.get("freeFlowSpeed",1)
            ratio = cur/free if free else 1
            status = "light" if ratio>=0.80 else "moderate" if ratio>=0.50 else "heavy"
            results.append({"id":c["id"],"name":c["name"],"current_speed":round(cur),
                             "free_flow":round(free),"status":status,"ratio":round(ratio,2),"default":c["default"]})
        except Exception:
            results.append({"id":c["id"],"name":c["name"],"status":"unknown","default":c["default"],"error":True})
    TRAFFIC_CACHE["data"] = results; TRAFFIC_CACHE["ts"] = now
    api_log.info(f"TRAFFIC  | TomTom x{len(results)} | {request.remote_addr} | {'FORCED' if force else 'scheduled'}")
    return jsonify(results)


# ─────────────────────────────────────────────────────────────
#  MLB
# ─────────────────────────────────────────────────────────────

def _mlb_tracked_team_ids():
    raw = os.getenv("MLB_TRACKED_TEAM_ID", "").strip()
    if not raw:
        return set()
    ids = set()
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit():
            ids.add(int(part))
    return ids

def _game_is_featured(g):
    ids = _mlb_tracked_team_ids()
    if not ids:
        return False
    a = g["teams"]["away"]["team"]["id"]
    h = g["teams"]["home"]["team"]["id"]
    return a in ids or h in ids

def _parse_game(g):
    a=g["teams"]["away"]; h=g["teams"]["home"]
    return {"game_pk":g["gamePk"],"time":g.get("gameDate",""),
            "away_team":a["team"]["name"],"home_team":h["team"]["name"],
            "is_featured":_game_is_featured(g),
            "venue":g.get("venue",{}).get("name","")}

@app.route("/api/mlb/today")
def mlb_today():
    try:
        r = requests.get(f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={date.today().isoformat()}", timeout=8)
        r.raise_for_status()
        dates = r.json().get("dates",[])
        if not dates: return jsonify({"featured_game":None})
        featured = next((g for g in [_parse_game(g) for g in dates[0].get("games",[])] if g["is_featured"]),None)
        return jsonify({"featured_game":featured})
    except Exception as e: return jsonify({"error":str(e)}),502

@app.route("/api/mlb/week")
def mlb_week():
    start_str = request.args.get("start", date.today().isoformat())
    try: start_dt = date.fromisoformat(start_str)
    except ValueError: start_dt = date.today()
    end_dt = start_dt + timedelta(days=6)
    try:
        r = requests.get(
            f"https://statsapi.mlb.com/api/v1/schedule?sportId=1"
            f"&startDate={start_dt.isoformat()}&endDate={end_dt.isoformat()}", timeout=10)
        r.raise_for_status()
        week = []
        for day in r.json().get("dates",[]):
            games = [_parse_game(g) for g in day.get("games",[])]
            games.sort(key=lambda g:(not g["is_featured"],g["time"]))
            week.append({"date":day["date"],"games":games})
        return jsonify(week)
    except Exception as e: return jsonify({"error":str(e)}),502


# ─────────────────────────────────────────────────────────────
#  HOLIDAYS
# ─────────────────────────────────────────────────────────────

def _nth_weekday(year,month,weekday,n):
    import calendar as _cal
    d = date(year,month,1)
    d = d + timedelta(days=(weekday-d.weekday())%7)
    return d + timedelta(weeks=n-1)

def _last_weekday(year,month,weekday):
    import calendar as _cal
    last = date(year,month,_cal.monthrange(year,month)[1])
    return last - timedelta(days=(last.weekday()-weekday)%7)

def _observed(d):
    if d.weekday()==5: return d-timedelta(days=1)
    if d.weekday()==6: return d+timedelta(days=1)
    return d

def _us_holidays(year):
    H = []
    def add(d,name,obs=True):
        od = _observed(d) if obs else d
        H.append({"date":od.isoformat(),"name":name,"actual_date":d.isoformat()})
    add(date(year,1,1),   "New Year's Day")
    add(_nth_weekday(year,1,0,3),  "Martin Luther King Jr. Day", False)
    add(_nth_weekday(year,2,0,3),  "Presidents' Day",            False)
    add(_last_weekday(year,5,0),   "Memorial Day",               False)
    add(date(year,6,19),  "Juneteenth")
    add(date(year,7,4),   "Independence Day")
    add(_nth_weekday(year,9,0,1),  "Labor Day",                  False)
    add(_nth_weekday(year,10,0,2), "Columbus Day",               False)
    add(date(year,11,11), "Veterans Day")
    add(_nth_weekday(year,11,3,4), "Thanksgiving Day",           False)
    add(date(year,12,25), "Christmas Day")
    ny = _observed(date(year+1,1,1))
    if ny.year == year:
        H.append({"date":ny.isoformat(),"name":"New Year's Day (observed)","actual_date":date(year+1,1,1).isoformat()})
    return sorted(H, key=lambda h:h["date"])

@app.route("/api/holidays")
def holidays():
    return jsonify(_us_holidays(int(request.args.get("year", date.today().year))))


# ─────────────────────────────────────────────────────────────
#  ROUTE SEGMENT
# ─────────────────────────────────────────────────────────────

@app.route("/api/route-segment")
def route_segment():
    if not TOMTOM_KEY: return jsonify({"error":"no_key","fallback":True}), 200
    fp = request.args.get("from",""); tp = request.args.get("to","")
    if not fp or not tp: return jsonify({"error":"from and to required"}), 400
    try:
        r = requests.get(
            f"https://api.tomtom.com/routing/1/calculateRoute/{fp}:{tp}/json",
            params={"key":TOMTOM_KEY,"traffic":"false","travelMode":"car"}, timeout=8)
        r.raise_for_status()
        pts = [{"lat":pt["latitude"],"lng":pt["longitude"]}
               for leg in r.json()["routes"][0]["legs"] for pt in leg["points"]]
        api_log.info(f"ROUTE_SEG| TomTom | {request.remote_addr} | {fp} → {tp}")
        return jsonify({"points":pts})
    except Exception as e:
        return jsonify({"error":str(e),"fallback":True}), 200


# ─────────────────────────────────────────────────────────────
#  ROUTES
# ─────────────────────────────────────────────────────────────

@app.route("/api/routes", methods=["GET"])
def get_routes():
    db   = get_routes_db()
    rows = db.execute("SELECT * FROM routes ORDER BY name").fetchall()
    db.close()
    return jsonify([dict(r) for r in rows])

@app.route("/api/routes", methods=["POST"])
def add_route():
    data = request.json
    if not all([data.get("name"),data.get("origin"),data.get("destination")]):
        return jsonify({"error":"name, origin, and destination required"}), 400
    db  = get_routes_db()
    cur = db.execute("INSERT INTO routes (name,origin,destination,waypoints) VALUES (?,?,?,?)",
                     (data["name"],data["origin"],data["destination"],data.get("waypoints","")))
    db.commit()
    row = db.execute("SELECT * FROM routes WHERE id=?", (cur.lastrowid,)).fetchone()
    db.close()
    return jsonify(dict(row)), 201

@app.route("/api/routes/<int:route_id>", methods=["PUT"])
def update_route(route_id):
    """Partial update. Supports geofence fields (dest_lat/lng, dest_radius_m,
    auto_end_minutes) plus name/origin/destination/waypoints."""
    data = request.json or {}
    db   = get_routes_db()
    row  = db.execute("SELECT id FROM routes WHERE id=?", (route_id,)).fetchone()
    if not row:
        db.close()
        return jsonify({"error": "Route not found"}), 404
    allowed = ("name", "origin", "destination", "waypoints",
               "dest_lat", "dest_lng", "dest_radius_m", "auto_end_minutes")
    sets, vals = [], []
    for key in allowed:
        if key in data:
            sets.append(f"{key}=?")
            vals.append(data[key])
    if sets:
        vals.append(route_id)
        db.execute(f"UPDATE routes SET {', '.join(sets)} WHERE id=?", vals)
        db.commit()
    updated = db.execute("SELECT * FROM routes WHERE id=?", (route_id,)).fetchone()
    db.close()
    return jsonify(dict(updated))

@app.route("/api/routes/<int:route_id>", methods=["DELETE"])
def delete_route(route_id):
    db = get_routes_db()
    db.execute("DELETE FROM routes WHERE id=?", (route_id,))
    db.commit(); db.close()
    return jsonify({"deleted":route_id})

@app.route("/api/routes/<int:route_id>/eta")
def route_eta(route_id):
    if not TOMTOM_KEY: return jsonify({"error":"no_key"}), 503
    db  = get_routes_db()
    row = db.execute("SELECT * FROM routes WHERE id=?", (route_id,)).fetchone()
    db.close()
    if not row: return jsonify({"error":"Route not found"}), 404
    try:
        import json as _j
        def geocode(addr):
            r = requests.get(
                f"https://api.tomtom.com/search/2/geocode/{requests.utils.quote(addr)}.json",
                params={"key":TOMTOM_KEY,"countrySet":"US","limit":1}, timeout=8)
            r.raise_for_status()
            pos = r.json()["results"][0]["position"]
            return f"{pos['lat']},{pos['lon']}"
        wp = row["waypoints"] or ""
        if wp.startswith("{") or wp.startswith("["):
            pts = _j.loads(wp)
            if isinstance(pts,dict): pts = pts.get("points",[])
            rp = ":".join(f"{p['lat']},{p['lng']}" for p in pts)
        else:
            oc = geocode(row["origin"]); dc = geocode(row["destination"])
            ws = ""
            if wp:
                wps=[w.strip() for w in wp.split("|") if w.strip()]
                if wps: ws=":".join(geocode(w) for w in wps)+":"
            rp = f"{oc}:{ws}{dc}"
        r2 = requests.get(
            f"https://api.tomtom.com/routing/1/calculateRoute/{rp}/json",
            params={"key":TOMTOM_KEY,"traffic":"true","travelMode":"car"}, timeout=10)
        r2.raise_for_status()
        s = r2.json()["routes"][0]["summary"]
        tm = round(s["travelTimeInSeconds"]/60, 1)
        dm = round(s.get("trafficDelayInSeconds",0)/60, 1)
        api_log.info(f"ROUTE_ETA| TomTom | {request.remote_addr} | route_id={route_id} → {tm} min")
        return jsonify({"route_id":route_id,"route_name":row["name"],"travel_mins":tm,
                        "delay_mins":dm,"length_km":round(s["lengthInMeters"]/1000,1)})
    except Exception as e:
        api_log.error(f"ROUTE_ETA| Error: {e}")
        return jsonify({"error":str(e)}), 502


# ─────────────────────────────────────────────────────────────
#  CHECKPOINTS
# ─────────────────────────────────────────────────────────────

@app.route("/api/routes/<int:route_id>/checkpoints", methods=["GET"])
def get_checkpoints(route_id):
    db   = get_routes_db()
    rows = db.execute(
        "SELECT * FROM checkpoints WHERE route_id=? ORDER BY position, id",
        (route_id,)).fetchall()
    db.close()
    return jsonify([dict(r) for r in rows])

@app.route("/api/routes/<int:route_id>/checkpoints", methods=["POST"])
def add_checkpoint(route_id):
    data = request.json
    if not data.get("name"):
        return jsonify({"error":"name required"}), 400
    db = get_routes_db()
    # Auto-assign next position
    max_pos = db.execute(
        "SELECT COALESCE(MAX(position),0) FROM checkpoints WHERE route_id=?",
        (route_id,)).fetchone()[0]
    cur = db.execute(
        "INSERT INTO checkpoints (route_id,name,position) VALUES (?,?,?)",
        (route_id, data["name"], max_pos + 1))
    db.commit()
    row = db.execute("SELECT * FROM checkpoints WHERE id=?", (cur.lastrowid,)).fetchone()
    db.close()
    return jsonify(dict(row)), 201

@app.route("/api/checkpoints/<int:cp_id>", methods=["PUT"])
def update_checkpoint(cp_id):
    data = request.json or {}
    db   = get_routes_db()
    if data.get("name"):
        db.execute("UPDATE checkpoints SET name=? WHERE id=?", (data["name"], cp_id))
    if data.get("position") is not None:
        db.execute("UPDATE checkpoints SET position=? WHERE id=?", (data["position"], cp_id))
    # Geofence coords (Phase 2) — each set independently so partial saves work.
    if "lat" in data:
        db.execute("UPDATE checkpoints SET lat=? WHERE id=?", (data["lat"], cp_id))
    if "lng" in data:
        db.execute("UPDATE checkpoints SET lng=? WHERE id=?", (data["lng"], cp_id))
    if "radius_m" in data:
        db.execute("UPDATE checkpoints SET radius_m=? WHERE id=?", (data["radius_m"], cp_id))
    db.commit()
    row = db.execute("SELECT * FROM checkpoints WHERE id=?", (cp_id,)).fetchone()
    db.close()
    return jsonify(dict(row))

@app.route("/api/checkpoints/<int:cp_id>", methods=["DELETE"])
def delete_checkpoint(cp_id):
    db = get_routes_db()
    db.execute("DELETE FROM checkpoints WHERE id=?", (cp_id,))
    db.commit(); db.close()
    return jsonify({"deleted":cp_id})


# ─────────────────────────────────────────────────────────────
#  ROUTE LOGS  (with optional checkpoint times)
# ─────────────────────────────────────────────────────────────

@app.route("/api/routes/<int:route_id>/logs", methods=["GET"])
def get_logs(route_id):
    db   = get_routes_db()
    # Exclude in-progress live sessions (status='active') so they don't show
    # as 0-minute trips; completed/auto_completed/manual logs are included.
    logs = db.execute(
        "SELECT * FROM route_logs WHERE route_id=? AND COALESCE(status,'completed')!='active' "
        "ORDER BY log_date DESC",
        (route_id,)).fetchall()
    result = []
    for log_row in logs:
        entry = dict(log_row)
        # Attach any checkpoint times logged for this trip
        cp_times = db.execute("""
            SELECT cl.*, c.name as checkpoint_name, c.position
            FROM checkpoint_logs cl
            JOIN checkpoints c ON c.id = cl.checkpoint_id
            WHERE cl.route_log_id = ?
            ORDER BY c.position
        """, (log_row["id"],)).fetchall()
        entry["checkpoint_times"] = [dict(ct) for ct in cp_times]
        result.append(entry)
    db.close()
    return jsonify(result)

@app.route("/api/routes/<int:route_id>/logs", methods=["POST"])
def add_log(route_id):
    data = request.json
    if not data.get("actual_mins"):
        return jsonify({"error":"actual_mins required"}), 400
    db  = get_routes_db()
    cur = db.execute(
        "INSERT INTO route_logs (route_id,log_date,estimated_mins,actual_mins,notes) VALUES (?,?,?,?,?)",
        (route_id, data.get("log_date", date.today().isoformat()),
         data.get("estimated_mins"), data["actual_mins"], data.get("notes","")))
    db.commit()
    log_id = cur.lastrowid

    # Save checkpoint times — only for checkpoints where user provided a value
    checkpoint_times = data.get("checkpoint_times", [])
    saved_cp = 0
    for ct in checkpoint_times:
        if ct.get("checkpoint_id") and ct.get("minutes_from_start") is not None:
            try:
                mins = float(ct["minutes_from_start"])
                if mins >= 0:
                    db.execute(
                        "INSERT INTO checkpoint_logs (route_log_id,checkpoint_id,minutes_from_start) VALUES (?,?,?)",
                        (log_id, ct["checkpoint_id"], mins))
                    saved_cp += 1
            except (ValueError, TypeError):
                pass  # Skip malformed values
    if saved_cp: db.commit()

    row = db.execute("SELECT * FROM route_logs WHERE id=?", (log_id,)).fetchone()
    db.close()
    api_log.info(f"ROUTE_LOG| route_id={route_id} | total={data['actual_mins']}min | checkpoints={saved_cp}")
    return jsonify(dict(row)), 201


@app.route("/api/routes/<int:route_id>/logs/<int:log_id>", methods=["PUT"])
def update_log(route_id, log_id):
    """Edit an existing trip's actual time, date, notes, and checkpoint times."""
    data = request.json or {}
    db = get_routes_db()
    row = db.execute(
        "SELECT * FROM route_logs WHERE id=? AND route_id=?",
        (log_id, route_id)).fetchone()
    if not row:
        db.close()
        return jsonify({"error": "not found"}), 404
    if row["status"] == "active":
        db.close()
        return jsonify({"error": "Cannot edit an active live session — end it first"}), 409

    actual = data.get("actual_mins", row["actual_mins"])
    try:
        actual = float(actual)
    except (TypeError, ValueError):
        db.close()
        return jsonify({"error": "actual_mins must be a number"}), 400
    if actual <= 0:
        db.close()
        return jsonify({"error": "actual_mins must be > 0"}), 400

    log_date = data.get("log_date", row["log_date"]) or row["log_date"]
    notes = data["notes"] if "notes" in data else (row["notes"] or "")
    if "estimated_mins" in data:
        est = data["estimated_mins"]
        try:
            est = float(est) if est is not None and est != "" else None
        except (TypeError, ValueError):
            db.close()
            return jsonify({"error": "estimated_mins must be a number"}), 400
    else:
        est = row["estimated_mins"]

    db.execute(
        "UPDATE route_logs SET log_date=?, estimated_mins=?, actual_mins=?, notes=? WHERE id=?",
        (log_date, est, actual, notes, log_id))

    # Replace checkpoint times when the client sends the array (allows clearing).
    if "checkpoint_times" in data:
        db.execute("DELETE FROM checkpoint_logs WHERE route_log_id=?", (log_id,))
        saved_cp = 0
        for ct in data.get("checkpoint_times") or []:
            if ct.get("checkpoint_id") is None or ct.get("minutes_from_start") is None:
                continue
            try:
                mins = float(ct["minutes_from_start"])
                if mins >= 0:
                    db.execute(
                        "INSERT INTO checkpoint_logs (route_log_id,checkpoint_id,minutes_from_start) VALUES (?,?,?)",
                        (log_id, int(ct["checkpoint_id"]), mins))
                    saved_cp += 1
            except (ValueError, TypeError):
                pass
        api_log.info(
            f"ROUTE_LOG| EDIT | route_id={route_id} log_id={log_id} | "
            f"total={actual}min | checkpoints={saved_cp}")
    else:
        api_log.info(
            f"ROUTE_LOG| EDIT | route_id={route_id} log_id={log_id} | total={actual}min")

    db.commit()
    updated = db.execute("SELECT * FROM route_logs WHERE id=?", (log_id,)).fetchone()
    cp_times = db.execute("""
        SELECT cl.*, c.name as checkpoint_name, c.position
        FROM checkpoint_logs cl
        JOIN checkpoints c ON c.id = cl.checkpoint_id
        WHERE cl.route_log_id = ?
        ORDER BY c.position
    """, (log_id,)).fetchall()
    db.close()
    out = dict(updated)
    out["checkpoint_times"] = [dict(ct) for ct in cp_times]
    return jsonify(out)


@app.route("/api/routes/<int:route_id>/logs/<int:log_id>", methods=["DELETE"])
def delete_log(route_id, log_id):
    """Remove a completed trip (and its checkpoint times) from history."""
    db = get_routes_db()
    row = db.execute(
        "SELECT * FROM route_logs WHERE id=? AND route_id=?",
        (log_id, route_id)).fetchone()
    if not row:
        db.close()
        return jsonify({"error": "not found"}), 404
    if row["status"] == "active":
        db.close()
        return jsonify({"error": "Cannot delete an active live session — cancel it instead"}), 409
    db.execute("DELETE FROM checkpoint_logs WHERE route_log_id=?", (log_id,))
    db.execute("DELETE FROM route_logs WHERE id=?", (log_id,))
    db.commit()
    db.close()
    api_log.info(f"ROUTE_LOG| DELETE | route_id={route_id} log_id={log_id}")
    return jsonify({"ok": True})


# ─────────────────────────────────────────────────────────────
#  LIVE ROUTE SESSIONS  (Phase 2)
# ─────────────────────────────────────────────────────────────
#  A live session is a route_logs row with status='active' and
#  source='live_session'.  Only ONE may be active globally.
#  Note: GPS / geofence logic is added later (steps 3 & 5); these
#  endpoints cover the session lifecycle only.
# ─────────────────────────────────────────────────────────────

_ISO_FMTS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S",
             "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S.%f")

def _now_iso():
    """Match the DB's datetime('now','localtime') text format."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def _parse_iso(ts):
    if not ts:
        return None
    for fmt in _ISO_FMTS:
        try:
            return datetime.strptime(ts, fmt)
        except (ValueError, TypeError):
            continue
    return None

def _elapsed_seconds(started_at, ended_at=None):
    start = _parse_iso(started_at)
    if not start:
        return 0
    end = _parse_iso(ended_at) if ended_at else datetime.now()
    if not end:
        end = datetime.now()
    return max(0, int((end - start).total_seconds()))

def _elapsed_minutes(started_at, ended_at=None):
    return round(_elapsed_seconds(started_at, ended_at) / 60.0, 1)

def _find_active_session(db):
    return db.execute(
        "SELECT * FROM route_logs WHERE status='active' AND source='live_session' "
        "ORDER BY id DESC LIMIT 1"
    ).fetchone()


# ── Destination coordinate resolver (Option C priority chain) ──

def _parse_latlng(text):
    """Parse a 'lat,lng' string into (lat, lng) if valid, else None."""
    if not text:
        return None
    parts = str(text).split(",")
    if len(parts) != 2:
        return None
    try:
        lat, lng = float(parts[0].strip()), float(parts[1].strip())
    except (ValueError, TypeError):
        return None
    if -90 <= lat <= 90 and -180 <= lng <= 180:
        return (lat, lng)
    return None

def _waypoints_end_point(waypoints):
    """Return (lat, lng) of the JSON waypoint with type=='end', else None."""
    if not waypoints:
        return None
    wp = str(waypoints).strip()
    if not (wp.startswith("{") or wp.startswith("[")):
        return None
    try:
        data = json.loads(wp)
    except (ValueError, TypeError):
        return None
    pts = data.get("points", []) if isinstance(data, dict) else data
    if not isinstance(pts, list):
        return None
    for p in pts:
        if isinstance(p, dict) and p.get("type") == "end" \
                and p.get("lat") is not None and p.get("lng") is not None:
            try:
                return (float(p["lat"]), float(p["lng"]))
            except (ValueError, TypeError):
                return None
    return None

def _geocode_text(addr):
    """Geocode free text to (lat, lng). TomTom if keyed, else Nominatim."""
    if not addr:
        return None
    if TOMTOM_KEY:
        try:
            r = requests.get(
                f"https://api.tomtom.com/search/2/geocode/{requests.utils.quote(addr)}.json",
                params={"key": TOMTOM_KEY, "countrySet": "US", "limit": 1}, timeout=8)
            r.raise_for_status()
            pos = r.json()["results"][0]["position"]
            return (float(pos["lat"]), float(pos["lon"]))
        except Exception:
            pass
    try:
        r = requests.get("https://nominatim.openstreetmap.org/search",
                         params={"q": addr, "format": "json", "limit": 1},
                         headers={"User-Agent": "WorkBuddy/1.0"}, timeout=8)
        r.raise_for_status()
        res = r.json()
        if res:
            return (float(res[0]["lat"]), float(res[0]["lon"]))
    except Exception:
        pass
    return None

def _resolve_destination_coords(db, route):
    """
    Option C priority chain → (lat, lng, method) or None:
      1. route.dest_lat / dest_lng (already set)
      2. route.destination parsed as 'lat,lng'
      3. waypoints JSON point with type=='end'
      4. geocode destination text (first result) — cached on route
      5. None (caller fails Start with 400)
    """
    if route["dest_lat"] is not None and route["dest_lng"] is not None:
        return (route["dest_lat"], route["dest_lng"], "route_dest")

    ll = _parse_latlng(route["destination"])
    if ll:
        return (ll[0], ll[1], "destination_latlng")

    ll = _waypoints_end_point(route["waypoints"])
    if ll:
        return (ll[0], ll[1], "waypoints_end")

    ll = _geocode_text(route["destination"])
    if ll:
        db.execute("UPDATE routes SET dest_lat=?, dest_lng=? WHERE id=?",
                   (ll[0], ll[1], route["id"]))
        db.commit()
        return (ll[0], ll[1], "geocoded")

    return None


# ── Geofence engine ──

def _haversine_m(lat1, lng1, lat2, lng2):
    """Great-circle distance in metres (no external deps)."""
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = math.sin(dlat / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlng / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))

def _session_state(db, session_id):
    """Get (or lazily rebuild) in-memory geofence state for a session."""
    with _session_lock:
        st = SESSION_STATE.get(session_id)
        if st is None:
            # Rebuild passed-set from DB so a mid-drive server restart is safe.
            rows = db.execute(
                "SELECT checkpoint_id FROM checkpoint_logs WHERE route_log_id=?",
                (session_id,)).fetchall()
            st = {"passed": {r["checkpoint_id"] for r in rows},
                  "dwell_start": None, "last_lat": None,
                  "last_lng": None, "last_loc_at": 0.0}
            SESSION_STATE[session_id] = st
        return st

def _run_geofence(db, session, lat, lng, now_iso):
    """
    Process one location fix for an active session. Returns an events dict:
      { "checkpoints_passed": [...], "auto_ended": bool, "actual_mins": float? }
    Only the NEXT unpassed (coord-bearing) checkpoint is evaluated per fix.
    """
    sid    = session["id"]
    events = {"checkpoints_passed": [], "auto_ended": False}
    route  = db.execute("SELECT * FROM routes WHERE id=?", (session["route_id"],)).fetchone()

    st = _session_state(db, sid)
    with _session_lock:
        st["last_lat"], st["last_lng"] = lat, lng
        st["last_loc_at"] = time.time()
        passed = set(st["passed"])

    # (2) Next unpassed checkpoint in order that actually has coords.
    cps = db.execute(
        "SELECT * FROM checkpoints WHERE route_id=? ORDER BY position, id",
        (session["route_id"],)).fetchall()
    for c in cps:
        if c["id"] in passed:
            continue
        if c["lat"] is None or c["lng"] is None:
            continue  # not geofenceable — don't let it block later checkpoints
        radius = c["radius_m"] if c["radius_m"] is not None else 150
        if _haversine_m(lat, lng, c["lat"], c["lng"]) <= radius:
            mins = _elapsed_minutes(session["started_at"], now_iso)
            db.execute(
                "INSERT INTO checkpoint_logs (route_log_id, checkpoint_id, minutes_from_start, passed_at) "
                "VALUES (?,?,?,?)", (sid, c["id"], mins, now_iso))
            db.commit()
            with _session_lock:
                st["passed"].add(c["id"])
            events["checkpoints_passed"].append(
                {"id": c["id"], "name": c["name"], "minutes_from_start": mins})
        break  # evaluate only the next gate per fix

    # (3)/(4) Destination dwell → auto-end.
    dest_lat, dest_lng = route["dest_lat"], route["dest_lng"]
    if dest_lat is not None and dest_lng is not None:
        dest_radius = route["dest_radius_m"]   if route["dest_radius_m"]   is not None else 150
        auto_end    = route["auto_end_minutes"] if route["auto_end_minutes"] is not None else 5
        inside = _haversine_m(lat, lng, dest_lat, dest_lng) <= dest_radius
        now_ts = time.time()
        with _session_lock:
            if inside:
                if st["dwell_start"] is None:
                    st["dwell_start"] = now_ts
                dwell_secs = now_ts - st["dwell_start"]
            else:
                st["dwell_start"] = None      # left the geofence → reset dwell
                dwell_secs = 0.0
        if inside and dwell_secs >= auto_end * 60:
            actual = _elapsed_minutes(session["started_at"], now_iso)
            db.execute(
                "UPDATE route_logs SET status='auto_completed', ended_at=?, actual_mins=? WHERE id=?",
                (now_iso, actual, sid))
            db.commit()
            events["auto_ended"] = True
            events["actual_mins"] = actual
            with _session_lock:
                SESSION_STATE.pop(sid, None)

    return events


@app.route("/api/sessions/active", methods=["GET"])
def get_active_session():
    """Return the single active live session (with route name) or null."""
    db  = get_routes_db()
    row = db.execute("""
        SELECT rl.*, r.name AS route_name
        FROM route_logs rl JOIN routes r ON r.id = rl.route_id
        WHERE rl.status='active' AND rl.source='live_session'
        ORDER BY rl.id DESC LIMIT 1
    """).fetchone()
    db.close()
    return jsonify(dict(row) if row else None)

@app.route("/api/routes/<int:route_id>/sessions/start", methods=["POST"])
def start_session(route_id):
    """
    Begin a live route session. One globally: 409 if another is active.
    Destination-coord resolution (priority chain) is added in step 3.
    """
    db    = get_routes_db()
    route = db.execute("SELECT * FROM routes WHERE id=?", (route_id,)).fetchone()
    if not route:
        db.close()
        return jsonify({"error": "Route not found"}), 404

    active = _find_active_session(db)
    if active:
        db.close()
        return jsonify({
            "error": "A route session is already active. End or cancel it first.",
            "active_session_id": active["id"],
        }), 409

    resolved = _resolve_destination_coords(db, route)
    if not resolved:
        db.close()
        return jsonify({
            "error": "Could not resolve destination coordinates for this route. "
                     "Set a destination pin, use a 'lat,lng' destination, or a geocodable address."
        }), 400
    dest_lat, dest_lng, dest_method = resolved

    now_iso = _now_iso()
    cur = db.execute(
        "INSERT INTO route_logs (route_id, log_date, actual_mins, status, source, started_at) "
        "VALUES (?,?,?,?,?,?)",
        (route_id, date.today().isoformat(), 0, "active", "live_session", now_iso))
    db.commit()
    row = db.execute("SELECT * FROM route_logs WHERE id=?", (cur.lastrowid,)).fetchone()
    db.close()
    api_log.info(f"SESSION  | START | route_id={route_id} | session_id={cur.lastrowid} "
                 f"| dest=({dest_lat},{dest_lng}) via {dest_method} | {now_iso}")
    result = dict(row)
    result.update({"dest_lat": dest_lat, "dest_lng": dest_lng, "dest_method": dest_method})
    return jsonify(result), 201

@app.route("/api/sessions/<int:session_id>", methods=["GET"])
def get_session(session_id):
    """Session status: elapsed seconds + checkpoint hit/miss list."""
    db  = get_routes_db()
    row = db.execute("""
        SELECT rl.*, r.name AS route_name
        FROM route_logs rl JOIN routes r ON r.id = rl.route_id
        WHERE rl.id=?
    """, (session_id,)).fetchone()
    if not row:
        db.close()
        return jsonify({"error": "Session not found"}), 404

    cps  = db.execute(
        "SELECT * FROM checkpoints WHERE route_id=? ORDER BY position, id",
        (row["route_id"],)).fetchall()
    hits = db.execute(
        "SELECT checkpoint_id, minutes_from_start, passed_at FROM checkpoint_logs WHERE route_log_id=?",
        (session_id,)).fetchall()
    db.close()

    hit_map = {h["checkpoint_id"]: dict(h) for h in hits}
    checkpoints = []
    for c in cps:
        h = hit_map.get(c["id"])
        checkpoints.append({
            "id": c["id"], "name": c["name"], "position": c["position"],
            "passed": bool(h),
            "minutes_from_start": h["minutes_from_start"] if h else None,
            "passed_at": h["passed_at"] if h else None,
        })

    gps_ok = True
    if row["status"] == "active":
        with _session_lock:
            st = SESSION_STATE.get(session_id)
        if st and st.get("last_loc_at"):
            gps_ok = (time.time() - st["last_loc_at"]) < GPS_STALE_SECS

    result = dict(row)
    result.update({
        "elapsed_seconds":    _elapsed_seconds(row["started_at"], row["ended_at"]),
        "checkpoints":        checkpoints,
        "checkpoints_hit":    len(hits),
        "checkpoints_total":  len(cps),
        "gps_ok":             gps_ok,
    })
    return jsonify(result)

@app.route("/api/sessions/<int:session_id>/location", methods=["POST"])
def session_location(session_id):
    """Ingest a GPS fix and run the geofence engine. Ignored if not active."""
    data = request.json or {}
    lat, lng = data.get("lat"), data.get("lng")
    if lat is None or lng is None:
        return jsonify({"error": "lat and lng required"}), 400
    try:
        lat, lng = float(lat), float(lng)
    except (ValueError, TypeError):
        return jsonify({"error": "lat/lng must be numeric"}), 400

    db      = get_routes_db()
    session = db.execute("SELECT * FROM route_logs WHERE id=?", (session_id,)).fetchone()
    if not session:
        db.close()
        return jsonify({"error": "Session not found"}), 404
    if session["status"] != "active":
        db.close()
        return jsonify({"status": session["status"], "ignored": True})

    now_iso = _now_iso()
    events  = _run_geofence(db, session, lat, lng, now_iso)
    updated = db.execute("SELECT status FROM route_logs WHERE id=?", (session_id,)).fetchone()
    db.close()

    for c in events.get("checkpoints_passed", []):
        api_log.info(f"SESSION  | CHECKPOINT | session_id={session_id} | "
                     f"{c['name']} @ {c['minutes_from_start']}min")
    if events.get("auto_ended"):
        api_log.info(f"SESSION  | AUTO-END | session_id={session_id} | "
                     f"actual={events.get('actual_mins')}min")

    return jsonify({"status": updated["status"], "events": events})

@app.route("/api/sessions/<int:session_id>/end", methods=["POST"])
def end_session(session_id):
    """Manual end → status=completed, compute actual_mins from elapsed."""
    db  = get_routes_db()
    row = db.execute("SELECT * FROM route_logs WHERE id=?", (session_id,)).fetchone()
    if not row:
        db.close()
        return jsonify({"error": "Session not found"}), 404
    if row["status"] != "active":
        db.close()
        return jsonify({"error": f"Session is not active (status={row['status']})"}), 409

    now_iso = _now_iso()
    actual  = _elapsed_minutes(row["started_at"], now_iso)
    db.execute(
        "UPDATE route_logs SET status='completed', ended_at=?, actual_mins=? WHERE id=?",
        (now_iso, actual, session_id))
    db.commit()
    updated = db.execute("SELECT * FROM route_logs WHERE id=?", (session_id,)).fetchone()
    db.close()
    with _session_lock:
        SESSION_STATE.pop(session_id, None)
    api_log.info(f"SESSION  | END | session_id={session_id} | actual={actual}min")
    return jsonify(dict(updated))

@app.route("/api/sessions/<int:session_id>/cancel", methods=["POST"])
def cancel_session(session_id):
    """
    Cancel a session. Decision: DELETE the row (cascades checkpoint_logs)
    so cancelled drives never pollute history or stats.
    """
    db  = get_routes_db()
    row = db.execute("SELECT * FROM route_logs WHERE id=?", (session_id,)).fetchone()
    if not row:
        db.close()
        return jsonify({"error": "Session not found"}), 404
    if row["status"] != "active":
        db.close()
        return jsonify({"error": f"Session is not active (status={row['status']})"}), 409

    db.execute("DELETE FROM route_logs WHERE id=?", (session_id,))
    db.commit(); db.close()
    with _session_lock:
        SESSION_STATE.pop(session_id, None)
    api_log.info(f"SESSION  | CANCEL | session_id={session_id} (row deleted)")
    return jsonify({"cancelled": session_id})


# ─────────────────────────────────────────────────────────────
#  EVENTS  (calendar DB)
# ─────────────────────────────────────────────────────────────

@app.route("/api/events", methods=["GET"])
def get_events():
    month = request.args.get("month")
    db    = get_calendar_db()
    rows  = db.execute(
        "SELECT * FROM events WHERE event_date LIKE ? ORDER BY event_date,start_time",
        (f"{month}%",)
    ).fetchall() if month else db.execute(
        "SELECT * FROM events ORDER BY event_date,start_time"
    ).fetchall()
    db.close()
    return jsonify([dict(r) for r in rows])

@app.route("/api/events", methods=["POST"])
def add_event():
    data = request.json
    if not all([data.get("title"),data.get("event_date")]):
        return jsonify({"error":"title and event_date required"}), 400
    db = get_calendar_db()
    try:
        cur = db.execute(
            "INSERT INTO events (title,event_date,end_date,start_time,end_time,event_type,color,notes) VALUES (?,?,?,?,?,?,?,?)",
            (data["title"],data["event_date"],data.get("end_date") or data["event_date"],
             data.get("start_time",""),data.get("end_time",""),
             data.get("event_type","personal"),data.get("color","#00A3E0"),data.get("notes","")))
        db.commit()
        row = db.execute("SELECT * FROM events WHERE id=?", (cur.lastrowid,)).fetchone()
        db.close()
        return jsonify(dict(row)), 201
    except Exception as e:
        db.close(); api_log.error(f"EVENT_ADD| {e}")
        return jsonify({"error":str(e)}), 500

@app.route("/api/events/<int:event_id>", methods=["PUT"])
def update_event(event_id):
    data = request.json
    if not all([data.get("title"),data.get("event_date")]):
        return jsonify({"error":"title and event_date required"}), 400
    db = get_calendar_db()
    try:
        db.execute(
            "UPDATE events SET title=?,event_date=?,end_date=?,start_time=?,end_time=?,event_type=?,color=?,notes=? WHERE id=?",
            (data["title"],data["event_date"],data.get("end_date") or data["event_date"],
             data.get("start_time",""),data.get("end_time",""),
             data.get("event_type","personal"),data.get("color","#00A3E0"),data.get("notes",""),event_id))
        db.commit()
        row = db.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
        db.close()
        return jsonify(dict(row))
    except Exception as e:
        db.close(); api_log.error(f"EVENT_UPD| {e}")
        return jsonify({"error":str(e)}), 500

@app.route("/api/events/<int:event_id>", methods=["DELETE"])
def delete_event(event_id):
    db = get_calendar_db()
    db.execute("DELETE FROM events WHERE id=?", (event_id,))
    db.commit(); db.close()
    return jsonify({"deleted":event_id})


# ─────────────────────────────────────────────────────────────
#  SETTINGS
# ─────────────────────────────────────────────────────────────

@app.route("/api/settings", methods=["GET"])
def get_settings():
    db   = get_settings_db()
    rows = db.execute("SELECT key,value FROM settings").fetchall()
    db.close()
    return jsonify({r["key"]:r["value"] for r in rows})

@app.route("/api/settings", methods=["POST"])
def save_settings():
    data = request.json
    db   = get_settings_db()
    for k, v in data.items():
        db.execute(
            "INSERT INTO settings (key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (k, str(v)))
    db.commit(); db.close()
    return jsonify({"status":"saved"})


# ─────────────────────────────────────────────────────────────
#  SCHEDULE UPLOAD + PARSE
# ─────────────────────────────────────────────────────────────

UPLOAD_FOLDER = os.path.join(BASE_DIR, "uploads")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

@app.route("/api/schedule/upload", methods=["POST"])
def upload_schedule():
    if "file" not in request.files:
        return jsonify({"error":"No file provided"}), 400
    f   = request.files["file"]
    ext = os.path.splitext(f.filename)[1].lower()
    if ext not in {".xlsx",".xls",".csv"}:
        return jsonify({"error":f"Unsupported type: {ext}"}), 400
    save_path = os.path.join(UPLOAD_FOLDER, f.filename)
    f.save(save_path)
    api_log.info(f"SCHEDULE | Uploaded: {f.filename} ({os.path.getsize(save_path)} bytes)")
    return jsonify({"status":"received","file":f.filename})

@app.route("/api/schedule/parse", methods=["POST"])
def parse_schedule():
    import traceback as _tb
    try:
        import pandas as pd
    except ImportError:
        return jsonify({"error":"pandas not installed. Run: pip install pandas openpyxl"}), 503

    data     = request.json or {}
    filename = data.get("filename",""); preview = data.get("preview",False)
    if not filename: return jsonify({"error":"filename required"}), 400
    filepath = os.path.join(UPLOAD_FOLDER, filename)
    if not os.path.exists(filepath): return jsonify({"error":f"File not found: {filename}"}), 404

    try:
        db_s    = get_settings_db()
        s_row   = db_s.execute("SELECT value FROM settings WHERE key='watched_person'").fetchone()
        db_s.close()
        watched = (s_row["value"] if s_row else "").strip()
    except Exception: watched = ""

    try:
        df = pd.read_excel(filepath, sheet_name="2026 Home Events", header=0)
    except Exception as e:
        return jsonify({"error":f"Could not open sheet: {e}"}), 422

    if len(df.columns) != 16:
        return jsonify({"error":f"Expected 16 columns, got {len(df.columns)}"}), 422

    df.columns = ["START_DATE","START_TIME","SUBJECT_FULL","LOCATION","DESCRIPTION",
                  "END_DATE","END_TIME","CATEGORIES","DAY_OF_WEEK",
                  "COV1","COV2","COV3","EXCEPTION1","EXCEPTION2","SUBJECT","PROMOTION"]

    WORK_COLS=[("COV1"),("COV2"),("COV3")]; EXCEPT_COLS=[("EXCEPTION1"),("EXCEPTION2")]
    today_str = date.today().isoformat()

    def is_exception(r):
        return any(watched.lower() in str(r.get(c,"") or "").lower() for c in EXCEPT_COLS)
    def is_working(r):
        if is_exception(r): return False
        return any(watched.lower() in str(r.get(c,"") or "").lower() for c in WORK_COLS)
    def coworkers(r):
        names=[]
        for c in WORK_COLS:
            for n in str(r.get(c,"") or "").split(","):
                n=n.strip()
                if n and n.lower()!=watched.lower() and n not in names: names.append(n)
        return ", ".join(names)
    def fmt_time(t):
        if t is None or str(t)=="nan": return ""
        if hasattr(t,"strftime") and hasattr(t,"hour"): return t.strftime("%H:%M")
        try:
            raw=str(t).strip()
            if len(raw)>=5 and raw[2]==":": return raw[:5]
            ts=pd.Timestamp(t)
            return "" if ts.year<=1900 else ts.strftime("%H:%M")
        except: return str(t)[:5]
    def fmt_date(d):
        try: return pd.Timestamp(d).strftime("%Y-%m-%d")
        except: return None

    imported=[]; skip_person=0; skip_past=0; skip_exc=0
    try:
        for _,r in df.iterrows():
            if is_exception(r): skip_exc+=1; continue
            if not is_working(r): skip_person+=1; continue
            ed = fmt_date(r.get("START_DATE"))
            if not ed: continue
            if ed < today_str: skip_past+=1; continue
            subj=str(r.get("SUBJECT") or r.get("SUBJECT_FULL") or "").strip()
            if not subj or subj=="nan": continue
            cw=coworkers(r)
            imported.append({"title":subj,"event_date":ed,"end_date":ed,
                              "start_time":fmt_time(r.get("START_TIME")),"end_time":fmt_time(r.get("END_TIME")),
                              "event_type":"work","color":"#ff6b35","notes":f"With: {cw}" if cw else ""})
    except Exception as e:
        api_log.error(f"SCHEDULE | Row error: {e}\n{_tb.format_exc()}")
        return jsonify({"error":f"Error reading rows: {e}"}), 500

    api_log.info(f"SCHEDULE | {len(imported)} future matches for '{watched}' (skip: {skip_past} past, {skip_exc} exceptions, {skip_person} other staff)")

    if preview:
        return jsonify({"preview":imported,"count":len(imported),"watched":watched,
                        "skipped_past":skip_past,"skipped_exc":skip_exc})

    db=get_calendar_db(); inserted=0
    try:
        for ev in imported:
            if db.execute("SELECT id FROM events WHERE title=? AND event_date=? AND start_time=?",
                          (ev["title"],ev["event_date"],ev["start_time"])).fetchone(): continue
            db.execute("INSERT INTO events (title,event_date,end_date,start_time,end_time,event_type,color,notes) VALUES (?,?,?,?,?,?,?,?)",
                       (ev["title"],ev["event_date"],ev["end_date"],ev["start_time"],ev["end_time"],ev["event_type"],ev["color"],ev["notes"]))
            inserted+=1
        db.commit(); db.close()
    except Exception as e:
        db.close(); api_log.error(f"SCHEDULE | DB error: {e}")
        return jsonify({"error":f"DB insert failed: {e}"}), 500

    api_log.info(f"SCHEDULE | Imported {inserted}, skipped {len(imported)-inserted} existing")
    return jsonify({"status":"imported","inserted":inserted,"skipped_existing":len(imported)-inserted,
                    "skipped_past":skip_past,"skipped_exc":skip_exc,"watched":watched})


# ─────────────────────────────────────────────────────────────
#  GEOCODE PROXY
# ─────────────────────────────────────────────────────────────

@app.route("/api/geocode")
def geocode_proxy():
    q = request.args.get("q","")
    if not q: return jsonify({"error":"No query"}), 400
    try:
        r = requests.get("https://nominatim.openstreetmap.org/search",
                         params={"q":q,"format":"json","limit":1},
                         headers={"User-Agent":"WorkBuddy/1.0"}, timeout=8)
        r.raise_for_status()
        res = r.json()
        if not res: return jsonify({"error":"Not found"}), 404
        return jsonify({"lat":float(res[0]["lat"]),"lng":float(res[0]["lon"])})
    except Exception as e: return jsonify({"error":str(e)}), 502


# ─────────────────────────────────────────────────────────────
#  ADMIN STATUS
# ─────────────────────────────────────────────────────────────

@app.route("/api/admin/status")
def admin_status():
    now    = time.time()
    active = {ip:info for ip,info in CONNECTIONS.items() if now-info["last_seen"]<1800}
    uptime_s = 0
    if os.path.exists(UPTIME_FILE):
        try:
            with open(UPTIME_FILE) as f: uptime_s = int(now-float(f.read().strip()))
        except: pass
    h,rem=divmod(uptime_s,3600); m,s=divmod(rem,60)
    return jsonify({"uptime":f"{h}h {m}m {s}s","uptime_secs":uptime_s,
                    "connections":[{"ip":ip,"hostname":i["hostname"],
                                    "last_seen":datetime.fromtimestamp(i["last_seen"]).strftime("%H:%M:%S")}
                                   for ip,i in sorted(active.items(),key=lambda x:-x[1]["last_seen"])]})


# ─────────────────────────────────────────────────────────────
#  START
# ─────────────────────────────────────────────────────────────

def start_server(port: int = 5000):
    try:
        from waitress import create_server
        srv = create_server(
            app, host="0.0.0.0", port=port, threads=8,
            channel_timeout=3600, asyncore_use_poll=True,
        )
        api_log.info(f"SERVER   | Waitress on 0.0.0.0:{port} | PID={os.getpid()}")
        srv.run()
    except ImportError:
        app.run(host="0.0.0.0", port=port, debug=False, threaded=True, use_reloader=False)

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    init_db()
    start_log_prune_scheduler()
    with open(PID_FILE,"w") as f: f.write(str(os.getpid()))
    with open(UPTIME_FILE,"w") as f: f.write(str(time.time()))
    api_log.info(f"SERVER   | WorkBuddy started | PID={os.getpid()}")
    print(f"\n🏟  WorkBuddy → http://localhost:{port}   |   Console → http://localhost:{port}/console\n")
    start_server(port)
