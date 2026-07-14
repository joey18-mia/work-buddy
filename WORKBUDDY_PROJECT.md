# WorkBuddy — Project Summary
**Stack:** Python Flask · SQLite (3 DBs) · Vanilla JS · Leaflet.js · Waitress · pystray  
**Entry point:** `workbuddy_tray.py` (system tray) or `workbuddy.bat` (one-liner launcher)  
**Dashboard URL:** `http://localhost:5000`  
**Console URL:** `http://localhost:5000/console`

---

## What This Is

WorkBuddy is a personal Windows desktop dashboard built for a single user — currently deployed for an IT/DevOps employee at the Miami Marlins (loanDepot Park). It runs as a background Python server, accessible from any device on the local network, and is managed via a system tray icon. The goal is a single-app daily assistant covering commute, schedule, weather, traffic, MLB games, and calendar — all local, no subscriptions, minimal external dependencies.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.x, Flask 3.1, Waitress (multithreaded) |
| Databases | SQLite × 3 (see below) |
| Frontend | HTML/CSS/JS (vanilla), DM Sans + DM Mono fonts |
| Maps | Leaflet.js + OpenStreetMap (free, no key) |
| Tray | pystray + Pillow |
| Schedule parsing | pandas + openpyxl |
| External APIs | OpenWeatherMap, TomTom (traffic + routing), MLB Stats API (free), Nominatim geocoding |

---

## Project Structure

```
workbuddy/
├── workbuddy_tray.py       ← Primary entry point (system tray icon)
├── workbuddy.bat           ← One-line launcher: python workbuddy_tray.py
├── workbuddy_admin.py      ← CLI helper for tray menu (list/uptime commands)
├── app.py                  ← Flask server, all API routes
├── database.py             ← DB init, migration chain, legacy migration
├── requirements.txt
├── .env                    ← API keys (never committed)
├── .env.example
├── databases/
│   ├── workbuddy_calendar.db   ← events
│   ├── workbuddy_routes.db     ← routes, route_logs, checkpoints, checkpoint_logs
│   └── workbuddy_settings.db  ← all key/value settings
├── old_db/                 ← auto-populated timestamped backups on migration
├── uploads/                ← uploaded schedule files
├── templates/
│   ├── index.html          ← main dashboard
│   └── console.html        ← live log console (SSE-based)
└── static/
    ├── css/style.css
    └── js/app.js
```

---

## Database Architecture

### Three separate DBs — independent lifecycles

**`databases/workbuddy_settings.db`** — schema v1  
Survives any other DB wipe. Contains all user configuration.
```sql
settings (key TEXT PRIMARY KEY, value TEXT)
```
Default keys: `watched_person`, `regular_hours_enabled`, `regular_hours_start`,
`regular_hours_end`, `regular_hours_days`, `uploads_override`, `time_format`

---

**`databases/workbuddy_calendar.db`** — schema v1  
```sql
events (id, title, event_date, end_date, start_time, end_time,
        event_type, color, notes, created_at)
```
Event types: `personal`, `work`, `game`, `other`  
Multi-day events: `event_date` = start, `end_date` = end (chip spans across week view)

---

**`databases/workbuddy_routes.db`** — schema v1  
```sql
routes          (id, name, origin, destination, waypoints, created_at)
route_logs      (id, route_id, log_date, estimated_mins, actual_mins, notes, logged_at)
checkpoints     (id, route_id, name, position, created_at)
checkpoint_logs (id, route_log_id, checkpoint_id, minutes_from_start)
```
- `waypoints` stores either pipe-separated text addresses or a JSON map-drawn route
- `checkpoint_logs` rows are only created when the user actually logs a time — skipping is built-in

---

### Migration System
- Every DB has its own `schema_version` table
- Migrations are **strictly additive** — only `ALTER TABLE ADD COLUMN` or new tables, never drops
- On version mismatch: backs up the DB to `old_db/[name]_v[n]_[timestamp].db` first, then migrates
- Legacy migration: if old single-file `workbuddy.db` exists in root, data is copied to all three new DBs automatically (runs once, then renames the old file)

---

## API Routes

### Pages
| Route | Description |
|---|---|
| `GET /` | Main dashboard |
| `GET /console` | Live log console |

### Weather
| Route | Description |
|---|---|
| `GET /api/weather` | OpenWeatherMap — temp + description. Cache: 4h. `?force=1` bypasses |

### Traffic
| Route | Description |
|---|---|
| `GET /api/corridors` | Returns all corridor metadata (no TomTom call) |
| `GET /api/traffic` | TomTom flow data. Cache: 4h. `?force=1` bypasses. `?ids=` filters corridors |

**Default corridors (4 active):**
- Florida's Turnpike
- Palmetto Expy (S of NW 36th St)
- SR-836 East (@ 17th Ave)
- SR-836 West (@ 107th Ave)

**Additional (toggled in Settings):**
- I-95 N/S, US-1, I-75

### MLB
| Route | Description |
|---|---|
| `GET /api/mlb/today` | Today's Marlins game (for topbar pill) |
| `GET /api/mlb/week?start=YYYY-MM-DD` | 7-day schedule for float panel |

### Holidays
| Route | Description |
|---|---|
| `GET /api/holidays?year=YYYY` | US federal holidays, fully calculated in Python (no API) |

### Routes
| Route | Description |
|---|---|
| `GET /api/routes` | All saved routes |
| `POST /api/routes` | Create route (name, origin, destination, waypoints) |
| `DELETE /api/routes/:id` | Delete route + all logs/checkpoints |
| `GET /api/routes/:id/eta` | TomTom routing ETA (geocodes addresses or uses map coordinates) |
| `GET /api/routes/:id/checkpoints` | List checkpoints in order |
| `POST /api/routes/:id/checkpoints` | Add checkpoint (name) |
| `PUT /api/checkpoints/:id` | Rename or reorder a checkpoint |
| `DELETE /api/checkpoints/:id` | Remove checkpoint |
| `GET /api/routes/:id/logs` | Trip logs with checkpoint_times attached |
| `POST /api/routes/:id/logs` | Log a trip (actual_mins required, checkpoint_times[] optional) |

### Events
| Route | Description |
|---|---|
| `GET /api/events?month=YYYY-MM` | Events for a month (fetches prev + next month too for multi-day) |
| `POST /api/events` | Create event |
| `PUT /api/events/:id` | Edit event |
| `DELETE /api/events/:id` | Delete event |

### Settings
| Route | Description |
|---|---|
| `GET /api/settings` | All key/value settings |
| `POST /api/settings` | Save one or more settings |

### Schedule
| Route | Description |
|---|---|
| `POST /api/schedule/upload` | Upload xlsx/csv file |
| `POST /api/schedule/parse` | Parse uploaded file — `{"filename":"...", "preview": true/false}` |

**Parse logic:**  
Reads sheet "2026 Home Events". Columns 9-11 = COV1/2/3 (working). Columns 12-13 = EXCEPTION1/2 (NOT working — takes priority). Only imports future dates. Skips past events.

### Utilities
| Route | Description |
|---|---|
| `GET /api/route-segment?from=lat,lng&to=lat,lng` | TomTom road geometry for map builder |
| `GET /api/geocode?q=address` | Nominatim geocode proxy |
| `GET /api/admin/status` | Uptime + active connections (last 30 min) |
| `GET /api/console/stream` | SSE stream of live log |
| `POST /api/console/log` | Push a message to the console (used by tray menu) |

---

## Frontend Features

### Dashboard Layout (3-column grid)
```
[Calendar — flex-height, matches tallest column]  [Routes]  [Traffic]
```

### Calendar
- **Views:** Week (default), Day
- **Events:** Clickable tall chips (62px) — time on first line, title on second
- **Multi-day events:** Chip spans across columns, rounded start/end
- **Regular hours:** Virtual chips (indigo, dashed border) generated from settings — no DB rows
- **Holidays:** US federal holidays shown as amber day tint + italic bold day number
- **Add event:** Click any day cell → modal with type, single/multi-day toggle, time, notes
- **Edit/delete:** Click any chip → edit modal

### Routes (Carousel)
- 3 routes per page, prev/next navigation
- **Map builder:** Leaflet + OpenStreetMap, click to drop turn points, right-click to set End. TomTom snaps segments to real streets. Markers are draggable.
- **ETA:** TomTom routing with live traffic
- **Checkpoints:** Named stops per route, logged optionally per trip
- **Log Route:** Actual time + optional per-checkpoint times
- **History:** Table with segment breakdown when checkpoint times exist

### Traffic Panel
- Per-corridor status: colored dot indicator, speed vs free-flow, LIGHT/MODERATE/HEAVY badge
- User toggles corridors in Settings (⚙) — persisted to localStorage
- Auto-refresh: 7:00 AM daily. Manual ↺ button force-refreshes (bypasses 4h cache)

### Work Schedule Config (`📅 Work Schedule` button)
Full-size config modal with 4 sections:
1. **Regular Hours** — ON/OFF toggle, time pickers, day chip selectors (Mon–Fri default)
2. **Uploads Override** — when ON, game days from Excel replace regular hours for that day
3. **Time Format** — `12h AM/PM` or `24h` toggle, applies everywhere
4. **Import Schedule** — upload + preview + import with exception logic

### MLB Schedule
- Topbar pill shows Marlins game time
- `⚾ Schedule` button opens draggable 7-day float panel
- Marlins games pinned to top, highlighted

### System Tray (Windows)
Right-click menu:
- **Open Dashboard** (default double-click action)
- **Open Console**
- **List Connections** → logs to console, opens it
- **Restart Server** — stops Flask thread, starts new one, icon stays
- **Stop Server** — removes icon, exits

Custom icon: drop `tray_icon.png` (any size PNG) in the workbuddy folder. Falls back to a generated teal circle with ⚾.

### Live Console (`/console`)
- SSE stream — last 60 lines on connect, then live
- Color-coded by type: Server/Weather/Traffic/Route/Schedule/Event/Migrate/Error
- Filter buttons per category
- Auto-scroll toggle

---

## Settings Reference

| Key | Default | Description |
|---|---|---|
| `watched_person` | `Joseph` | Name matched in schedule COV columns |
| `regular_hours_enabled` | `false` | Show virtual work hour chips on calendar |
| `regular_hours_start` | `09:00` | Start time for regular hours |
| `regular_hours_end` | `17:00` | End time for regular hours |
| `regular_hours_days` | `1,2,3,4,5` | ISO weekdays (1=Mon, 7=Sun) |
| `uploads_override` | `false` | Game days replace regular hours chips |
| `time_format` | `12h` | `12h` or `24h` — applies everywhere |

---

## Themes (5)
Toggled via dot picker in topbar. Persisted to localStorage.
- **Dark** — near-black, light gray accent
- **Dark Blue** — navy, Marlins teal accent (default)
- **Teal** — classic Florida Marlins aqua on very dark background
- **Vice** — Miami Vice: hot pink + electric cyan on near-black
- **Light** — white/gray with blue accent

Each theme defines its own CSS variables including `--holiday-bg`, `--holiday-text`, `--reg-hours`, `--reg-hours-dim`.

---

## API Keys (.env)

```env
OPENWEATHER_API_KEY=...    # openweathermap.org/api — free tier
TOMTOM_API_KEY=...         # developer.tomtom.com — free tier (2,500 req/day)
                           # Enable: Traffic API, Traffic Flow API,
                           #         Routing API, Geocoding API,
                           #         Traffic Incidents API
SECRET_KEY=...             # any long random string
LOCATION_LAT=25.7617
LOCATION_LON=-80.1918
LOCATION_NAME=Miami, FL
PORT=5000                  # optional, defaults to 5000
```

---

## TomTom Call Budget

| Action | Calls | Frequency |
|---|---|---|
| Traffic (4 corridors) | 4 | Once at 7 AM, or manual ↺ |
| Route ETA | 1 geocode + 1 route | On demand |
| Route segment (map builder) | 1 per segment drawn | On demand |
| Total daily (idle) | 4 | Well within 2,500 free |

---

## Scheduled Auto-Refreshes
- **Weather:** 7:00 AM daily (OpenWeatherMap). Manual ↺ bypasses 4h cache.
- **Traffic:** 7:00 AM daily (TomTom). Manual ↺ bypasses 4h cache.
- **MLB today pill:** Every 15 minutes (MLB Stats API — free, no key).
- **Midnight reload:** Page auto-reloads at 00:00:05 so calendar flips to new day.

---

## Confirmed Future Plans

### In-progress / Next build
1. **Regular hours click-to-edit** — per-day overrides table in calendar DB (`regular_hours_overrides`). Click any virtual chip to edit or remove that specific day without touching the global schedule.

2. **Month view** — third calendar mode. Full month grid, event chips show abbreviated.

3. **Route stats card** — sits below the routes carousel. Chart.js bar/line charts: avg commute by day of week, personal best/worst, trend over time. Calculated from existing `route_logs` data — no new schema needed.

4. **ICS calendar feed** — `GET /calendar.ics` endpoint that exports all events as a standard iCalendar file. Subscribe from Google Calendar, Outlook, iPhone, Android — any calendar app. Read-only from their side. No OAuth, no tokens, fully local. This is the preferred approach over bidirectional API sync.

5. **Event search** — substring match on title across all events. Search "Baseball" finds "Baseball Game". Results panel shows date, type, and a click-to-navigate link.

6. **Mobile layout** — single-column CSS media query. 3-day calendar view auto-activates on screens under 500px (today ± 1 day). Bottom navigation bar for MLB/Schedule buttons. 44px minimum touch targets on all inputs.

7. **Recurring events** — new `recurrences` table in calendar DB. Options: daily, weekly, biweekly, monthly. End date or "no end". Expanded virtually on the frontend (same as regular hours — no DB bloat). UI in the Add Event modal.

### Longer term
- **Google Calendar sync** (optional bidirectional push — lower priority than ICS feed)
- **"Leaving in X" countdown** — set a route + departure time, get a browser notification when it's time to leave based on current ETA
- **Server management UI** — dedicated web interface (or Electron app) to start/stop/restart WorkBuddy instances rather than using the batch file
- **Webhook/Teams bot integration** for IT ticketing alerts
- **Multi-user support** — currently single-user; `watched_person` setting is a step toward multi-user but DB structure would need user_id columns

---

## Running the Project

```bash
# First time setup
pip install -r requirements.txt

# Copy and fill in API keys
cp .env.example .env
notepad .env

# Run (preferred — system tray)
python workbuddy_tray.py

# Or via batch
workbuddy.bat

# Stop from another terminal
workbuddy.bat stop

# List connected devices
workbuddy.bat list
```

On first launch, databases are created in `databases/`. If a legacy `workbuddy.db` exists, data is migrated automatically.

---

## Known Behavior Notes
- **Schedule import:** Only future dates are imported. Past events are skipped (skipped count shown in status).
- **Exception columns:** If `watched_person` appears in `EXCEPTION1` or `EXCEPTION2`, that game row is excluded even if their name also appears in a coverage column. COV1/2/3 = working, EXCEPTION1/2 = not working.
- **Calendar height:** Matches the tallest of the Routes or Traffic columns via CSS grid `align-items: stretch`. Minimum 420px.
- **Regular hours:** Not stored in DB — rendered client-side from settings. Disabling the toggle immediately removes all chips with no data loss.
- **Map-drawn routes:** Stored as JSON in `routes.waypoints` field. TomTom routing uses the lat/lng coordinates directly for ETA without re-geocoding.
- **DB wipe procedure:** Stop server → delete the target `databases/*.db` file → restart. The migration chain recreates it at current schema version.
