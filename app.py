import os
import time
import queue
import logging
import socket
import threading
import requests
from datetime import date, timedelta, datetime
from flask import Flask, Response, jsonify, render_template, request, stream_with_context
from dotenv import load_dotenv
from database import (get_calendar_db, get_routes_db, get_settings_db,
                      init_db, DATABASES_DIR)

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "dev-fallback-change-me")

OPENWEATHER_KEY = os.getenv("OPENWEATHER_API_KEY", "")
TOMTOM_KEY      = os.getenv("TOMTOM_API_KEY", "")
LAT             = os.getenv("LOCATION_LAT",  "25.7617")
LON             = os.getenv("LOCATION_LON",  "-80.1918")
LOCATION_NAME   = os.getenv("LOCATION_NAME", "Miami, FL")
BASE_DIR        = os.path.dirname(os.path.abspath(__file__))

TRAFFIC_CACHE = {"data": None, "ts": 0}
WEATHER_CACHE = {"data": None, "ts": 0}
CACHE_TTL     = 4 * 60 * 60
CONNECTIONS: dict = {}
PID_FILE    = os.path.join(BASE_DIR, "workbuddy.pid")
UPTIME_FILE = os.path.join(BASE_DIR, "workbuddy.uptime")

# ─────────────────────────────────────────────────────────────
#  LOGGING + SSE
# ─────────────────────────────────────────────────────────────

LOG_PATH     = os.path.join(BASE_DIR, "workbuddy_api.log")
_sse_clients = []
_sse_lock    = threading.Lock()

class _SSEHandler(logging.Handler):
    def emit(self, record):
        msg = self.format(record)
        with _sse_lock:
            dead = []
            for q in _sse_clients:
                try:    q.put_nowait(msg)
                except: dead.append(q)
            for q in dead: _sse_clients.remove(q)

_fh = logging.FileHandler(LOG_PATH)
_fh.setFormatter(logging.Formatter("%(asctime)s  %(message)s", "%Y-%m-%d %H:%M:%S"))
_sh = _SSEHandler()
_sh.setFormatter(logging.Formatter("%(asctime)s  %(message)s", "%Y-%m-%d %H:%M:%S"))
api_log = logging.getLogger("workbuddy")
api_log.setLevel(logging.INFO)
api_log.addHandler(_fh)
api_log.addHandler(_sh)
api_log.propagate = False


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


# ─────────────────────────────────────────────────────────────
#  PAGES
# ─────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html", location=LOCATION_NAME)

@app.route("/console")
def console():
    return render_template("console.html")


# ─────────────────────────────────────────────────────────────
#  CONSOLE SSE
# ─────────────────────────────────────────────────────────────

@app.route("/api/console/stream")
def console_stream():
    def generate():
        cq = queue.Queue(maxsize=200)
        with _sse_lock: _sse_clients.append(cq)
        try:
            if os.path.exists(LOG_PATH):
                with open(LOG_PATH, "r", encoding="utf-8", errors="replace") as f:
                    for line in f.readlines()[-60:]:
                        yield f"data: {line.rstrip()}\n\n"
            while True:
                try:    yield f"data: {cq.get(timeout=25)}\n\n"
                except queue.Empty: yield ": keepalive\n\n"
        except GeneratorExit:
            pass
        finally:
            with _sse_lock:
                if cq in _sse_clients: _sse_clients.remove(cq)
    return Response(stream_with_context(generate()), mimetype="text/event-stream",
                    headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no","Connection":"keep-alive"})

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

def _parse_game(g):
    a=g["teams"]["away"]; h=g["teams"]["home"]
    return {"game_pk":g["gamePk"],"time":g.get("gameDate",""),
            "away_team":a["team"]["name"],"home_team":h["team"]["name"],
            "is_marlins":(a["team"]["id"]==146 or h["team"]["id"]==146),
            "venue":g.get("venue",{}).get("name","")}

@app.route("/api/mlb/today")
def mlb_today():
    try:
        r = requests.get(f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={date.today().isoformat()}", timeout=8)
        r.raise_for_status()
        dates = r.json().get("dates",[])
        if not dates: return jsonify({"marlins":None})
        marlins = next((g for g in [_parse_game(g) for g in dates[0].get("games",[])] if g["is_marlins"]),None)
        return jsonify({"marlins":marlins})
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
            games.sort(key=lambda g:(not g["is_marlins"],g["time"]))
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
    data = request.json
    db   = get_routes_db()
    if data.get("name"):
        db.execute("UPDATE checkpoints SET name=? WHERE id=?", (data["name"], cp_id))
    if data.get("position") is not None:
        db.execute("UPDATE checkpoints SET position=? WHERE id=?", (data["position"], cp_id))
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
    logs = db.execute(
        "SELECT * FROM route_logs WHERE route_id=? ORDER BY log_date DESC",
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
        watched = (s_row["value"] if s_row else "Joseph").strip()
    except Exception: watched = "Joseph"

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
        srv = create_server(app, host="0.0.0.0", port=port, threads=8)
        api_log.info(f"SERVER   | Waitress on 0.0.0.0:{port} | PID={os.getpid()}")
        srv.run()
    except ImportError:
        app.run(host="0.0.0.0", port=port, debug=False, threaded=True, use_reloader=False)

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    init_db()
    with open(PID_FILE,"w") as f: f.write(str(os.getpid()))
    with open(UPTIME_FILE,"w") as f: f.write(str(time.time()))
    api_log.info(f"SERVER   | WorkBuddy started | PID={os.getpid()}")
    print(f"\n🏟  WorkBuddy → http://localhost:{port}   |   Console → http://localhost:{port}/console\n")
    start_server(port)
