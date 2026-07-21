# WorkBuddy — Project Summary
**Stack:** Python Flask · SQLite (3 DBs) · Vanilla JS · Leaflet.js · Waitress · pystray  
**Entry point:** `workbuddy_tray.py` (system tray) or `workbuddy.bat` (one-liner launcher)  
**Dashboard URL:** `http://localhost:5000`  
**Console URL:** `http://localhost:5000/console`

---

## What This Is

WorkBuddy is a personal Windows desktop dashboard for a single user. It runs as a background Python server, accessible from devices on the local network, and is managed via a system tray icon. The goal is a single-app daily assistant covering commute, schedule, weather, traffic, MLB games, and calendar — all local, no subscriptions, minimal external dependencies.

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
| `GET /api/mlb/today` | Today's featured team game (topbar pill), if `MLB_TRACKED_TEAM_ID` is set |
| `GET /api/mlb/week?start=YYYY-MM-DD` | 7-day MLB schedule for float panel |

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
| `POST /api/schedule/parse` | Parse uploaded spreadsheet — `{"filename":"...", "preview": true/false}` |

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
2. **Uploads Override** — when ON, imported schedule events replace regular hours for matching days
3. **Time Format** — `12h AM/PM` or `24h` toggle, applies everywhere
4. **Import Schedule** — upload, preview, and import

### MLB Schedule
- Topbar **vice** pill shows the next featured-team game (matchup + time)
- `🌴 MLB` opens draggable 7-day **Ballpark Week** panel
- Featured games (from `MLB_TRACKED_TEAM_ID`) pinned to top with a **VICE** tag

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
| `watched_person` | *(empty)* | Label used when matching rows during schedule import |
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
- **Dark Blue** — navy with teal accent (default)
- **Teal** — aqua on very dark background
- **Vice** — hot pink + electric cyan on near-black
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
LOCATION_LAT=...           # your area
LOCATION_LON=...
LOCATION_NAME=...
PORT=5000                  # optional, defaults to 5000
MLB_TRACKED_TEAM_ID=       # optional; comma-separated MLB team IDs to feature in UI
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

### Phase 2 — Mobile Client & Live Route Tracking **(priority / next build)**

> **Goal:** A dedicated **mobile user experience** (not just a narrow desktop layout) that uses the same WorkBuddy server and settings. Multi-page layout is fine. Primary win: **one-tap route logging** with optional **GPS-backed checkpoint and arrival detection**.

#### Scope overview

| Area | Desktop (current) | Mobile client (Phase 2) |
|---|---|---|
| Configuration | Full dashboard + modals | Same settings keys & APIs; mobile-friendly multi-page UI |
| Route logging | Manual “Log Route” after the fact | **Start Route** at departure; **End Route** at arrival |
| Checkpoints | Name + order only; times entered manually | Checkpoints tied to **lat/lng + geofence radius** |
| Location | N/A | Client reports position while a session is active |
| Calendar / traffic / weather | 3-column grid | Bottom-nav pages; condensed views |

Deliver as a **mobile web shell** first (`/m/` or responsive app shell) so it works on phone browsers against the existing LAN server — no app store required. Native wrapper (PWA install, optional Capacitor) can follow.

---

#### 2.1 Mobile client — UX & pages

**Entry:** `http://<server>:5000/m/` (or auto-redirect when viewport &lt; 500px).

**Suggested pages (bottom navigation):**

1. **Home** — weather pill, traffic summary, today’s calendar slice, featured MLB pill  
2. **Routes** — route list → route detail  
3. **Calendar** — 3-day default on mobile; week toggle retained  
4. **Settings** — same keys as desktop (`watched_person`, regular hours, corridors, time format, etc.)

**Route detail (mobile-first):**

```
┌─────────────────────────────┐
│  Route name                 │
│  ETA · last avg             │
│                             │
│  [  START ROUTE  ]          │  ← logs departure time immediately
│                             │
│  Live elapsed:  00:23:14    │  (visible while session active)
│                             │
│  ○ Checkpoint A      —      │
│  ● Checkpoint B   12:04     │  ← auto-filled when geofence hit
│  ○ Destination       —      │
│                             │
│  [  END ROUTE  ]            │  ← manual arrival
└─────────────────────────────┘
```

**Touch targets:** minimum 44px on all primary actions (Start / End / nav).

**Parity rule:** anything configurable on desktop must be reachable on mobile via the same `/api/settings` and route CRUD endpoints — no separate mobile-only config store.

---

#### 2.2 Start Route / End Route — core logging flow

**Start Route (manual, instant):**

- User taps **Start Route** on a saved route.  
- Server creates an **active route session** and records `started_at` (local time).  
- No need to remember departure time — `actual_mins` is derived at end: `ended_at - started_at`.  
- Replaces the current workflow of filling in total minutes after the fact (manual log remains as fallback for past trips).

**End Route (manual, primary):**

- User taps **End Route** when they arrive.  
- Server sets `ended_at`, computes `actual_mins`, marks session `completed`.  
- Stops location polling / heartbeat for that client.

**End Route (automatic, backup):**

- If the client is within the route **destination geofence** continuously for **`auto_end_minutes`** (default **5**, overridable per route), server auto-completes the session.  
- On auto-end: status → `auto_completed`, location polling stops.  
- Manual **End Route** always available and takes precedence if tapped first.

**One active session (confirmed):**

- Only **one** `active` session may exist at a time (globally, not per-route).  
- If a session is already active, **Start Route** on any other route is **disabled** until the user **cancels** or **ends** the current one.  
- Cancelled sessions are not saved to history (or saved with `status='cancelled'` and excluded from stats — TBD at implementation).

---

#### 2.3 Checkpoints tied to location

**Schema additions (routes DB — migration v2, additive):**

```sql
-- checkpoints (add columns)
lat         REAL,
lng         REAL,
radius_m    INTEGER DEFAULT 150   -- geofence radius in meters

-- routes (add columns)
dest_lat          REAL,
dest_lng          REAL,
dest_radius_m     INTEGER DEFAULT 150,
auto_end_minutes  INTEGER DEFAULT 5   -- 0 = disable auto-end

-- route_logs (add columns)
started_at    TEXT,                  -- set on Start Route
ended_at      TEXT,                  -- set on End / auto-end
status        TEXT DEFAULT 'completed' -- active | completed | auto_completed | cancelled
source        TEXT DEFAULT 'manual'    -- manual | live_session

-- checkpoint_logs (add columns)
passed_at     TEXT                    -- wall-clock when geofence triggered (optional; minutes_from_start still stored)
```

**Checkpoint editor (desktop + mobile):**

- When adding/editing a checkpoint, pick a map point (same Leaflet stack) or “use current location” on phone.  
- Default radius 150 m; advanced toggle per checkpoint.

**Pass detection rules:**

1. Only evaluate checkpoints **in route order** (by `position`).  
2. A checkpoint is “passed” when client position is within `radius_m` — log **once** (first entry).  
3. Store both `minutes_from_start` (from session `started_at`) and `passed_at` timestamp.  
4. Skipped checkpoints stay empty (same philosophy as today).

---

#### 2.4 Location updates — architecture (discussion)

**User intent:** periodic checks (~every minute) to see if the client has passed checkpoints or arrived.

**Important constraint:** a server cannot read GPS from a phone on its own. The phone must **send** location to the server (browser Geolocation API). What we can do:

| Approach | How it works | Pros | Cons |
|---|---|---|---|
| **A. Client push (recommended)** | While session `active`, mobile client POSTs location every 60 s (configurable) | Simple, works on LAN HTTP, battery-aware | Requires tab open or PWA background limits |
| **B. Server heartbeat + client response** | Server sends SSE/WebSocket “tick”; client replies with location POST | Matches “server asks” mentally | More moving parts; still client-driven |
| **C. Native app / Capacitor** | Background geolocation plugin | Best background accuracy | Extra packaging |

**Recommended for Phase 2:** **A** — client push every **60 s** while GPS is healthy. As route history grows, checkpoint auto-detection becomes less critical over time; the **elapsed timer + Start/End** remain the primary value.

**GPS loss behavior (confirmed):**

- **Elapsed timer keeps running** — departure time is already captured; total trip time must not freeze in a tunnel.  
- **Stop sending location** to the server while signal is lost (no bogus positions).  
- **Retry every 30 s** until a fix is regained, then resume the normal 60 s interval.  
- While GPS is lost: **no new checkpoint passes**, **auto-end dwell timer paused** (cannot confirm arrival without a fix). UI shows a subtle “GPS lost — retrying…” state.  
- Manual **End Route** always works regardless of GPS.

**Suggested APIs:**

| Method | Route | Purpose |
|---|---|---|
| `POST` | `/api/routes/:id/sessions/start` | Start Route → returns `session_id` |
| `POST` | `/api/sessions/:id/location` | `{ lat, lng, accuracy, recorded_at }` — client every ~60 s |
| `GET` | `/api/sessions/:id` | Session status, elapsed, checkpoints hit (for mobile UI poll/SSE) |
| `POST` | `/api/sessions/:id/end` | Manual End Route |
| `POST` | `/api/sessions/:id/cancel` | Abort without saving (optional) |

**Server-side loop (on each location POST):**

1. Ignore if session not `active`.  
2. For next unpassed checkpoint in order → if inside geofence, insert `checkpoint_logs`.  
3. If inside destination geofence → start/continue dwell timer; if dwell ≥ `auto_end_minutes` → auto-end.  
4. If outside destination → reset dwell timer.

**Privacy / battery:** location is only collected while a session is `active`; never stored as a continuous track — only checkpoint pass events and session start/end.

---

#### 2.5 Implementation phases (suggested order)

1. **Routes DB migration v2** — geofence columns + active session fields on `route_logs`.  
2. **Session APIs** — start / location / end / status (no GPS yet; manual end only).  
3. **Mobile shell** — `/m/` layout, bottom nav, Routes page with Start/End + live timer.  
4. **Checkpoint map picker** — lat/lng on checkpoints + destination on routes.  
5. **Geofence engine** — auto checkpoint + auto-end on location POST.  
6. **Settings parity** — mobile settings pages for corridors, schedule, regular hours.  
7. **PWA polish** — install prompt, keep-screen-awake hint during active session.

---

#### 2.6 Confirmed decisions

| Topic | Decision |
|---|---|
| Active sessions | **One at a time.** Second route cannot start until current session is cancelled or ended. |
| Location interval | **60 s** while GPS is healthy. |
| Geofence default | **150 m** (per-checkpoint override still supported). |
| GPS loss | Timer **keeps going**; pause location posts and geofence logic; **retry every 30 s** until fix returns, then resume 60 s cadence. Manual End Route always available. |
| Destination coords | **Option C (locked).** Priority: stored `dest_lat/lng` → parse `lat,lng` text → map waypoints end point → geocode on first Start (first result, cache) → block Start if all fail. |
| Trip `source` field | **Locked.** New `source` column on `route_logs` only (`manual` \| `live_session`). **All pre-existing rows migrated to `manual`.** Does not alter routes, checkpoints, or manual log POST behavior. |

**Implementation handoff:** see `PHASE2_IMPLEMENTATION.md` for a self-contained build prompt (optimized for a separate coding agent).

---

#### 2.7 Destination coordinates — **LOCKED: Option C**

Auto-end and checkpoint geofences need **numeric lat/lng**. Resolution order when Start Route is tapped (**first match wins**):

1. **`routes.dest_lat` / `routes.dest_lng`** — explicit pin saved in editor (highest priority).  
2. **Parse `routes.destination`** — if value matches `lat,lng` (map-drawn routes).  
3. **Map waypoints JSON** — if `waypoints` is `{type:'map', points:[...]}`, use the point with `type:'end'`.  
4. **Geocode `routes.destination` text** — Nominatim via existing `/api/geocode` logic; **use first result**, persist to `dest_lat/lng` on the route row for future trips.  
5. **Fail** — return 400, show “Set destination on map”; do not start session.

Same priority chain applies to **checkpoint** coords in the editor (stored `lat/lng` beats geocode).

**UX notes:**

- Map-built routes: Start works immediately (steps 2–3).  
- Address-only routes: first Start may pause ~1 s for step 4, then cached.  
- Desktop + mobile editors expose destination pin and per-checkpoint pins.

---

#### 2.8 Trip `source` on `route_logs` — **LOCKED**

> **Not** on the `routes` table — `source` lives on each **`route_logs` row** (one trip / one history entry). The `routes` table is unchanged except for new optional geofence columns.

`source` records *how that trip was logged*. It does not replace or overwrite `actual_mins`, `log_date`, `checkpoint_logs`, or any existing field.

| `source` | Created by | Existing data impact |
|---|---|---|
| `manual` | Desktop **Log Route** modal (current behavior) | None — same POST `/api/routes/:id/logs` flow |
| `live_session` | Mobile **Start Route** → End / auto-end | New flow only; writes `started_at`, `ended_at` |

**Migration requirement (routes DB v2):**

```sql
ALTER TABLE route_logs ADD COLUMN source TEXT DEFAULT 'manual';
-- then for every row that predates Phase 2:
UPDATE route_logs SET source = 'manual' WHERE source IS NULL OR source = '';
UPDATE route_logs SET status = 'completed' WHERE status IS NULL OR status = '';
```

- All historical rows → `source = 'manual'`, `status = 'completed'`.  
- Manual logging after Phase 2 still writes `source = 'manual'`.  
- Only Start Route creates `source = 'live_session'`.  
- Stats/UI may filter on `source` later; **ignoring it must behave exactly as today.**

**Example after Phase 2:**

```
route_logs
┌────┬──────────┬─────────────┬────────────┬────────────┬─────────────┬──────────────┐
│ id │ route_id │ source      │ status     │ started_at │ ended_at    │ actual_mins  │
├────┼──────────┼─────────────┼────────────┼────────────┼─────────────┼──────────────┤
│ 12 │ 3        │ manual      │ completed  │ NULL       │ NULL        │ 47           │  ← old habit
│ 13 │ 3        │ live_session│ completed  │ 07:12:00   │ 07:58:00    │ 46           │  ← Start/End
│ 14 │ 3        │ live_session│ active     │ 08:05:00   │ NULL        │ NULL         │  ← driving now
└────┴──────────┴─────────────┴────────────┴────────────┴─────────────┴──────────────┘
```

Manual logging **remains available** on desktop for forgotten trips or editing past commutes; it never creates an `active` session.

---

### Deferred (was “mobile layout” in Phase 1 list)

Item **#6 Mobile layout** (single-column CSS on existing `index.html`) is **superseded** by the dedicated mobile client above. Desktop dashboard stays as-is; no need to fully cram 3-column UI into one page on small screens.

### In-progress / Next build (after Phase 2)

1. **Regular hours click-to-edit** — per-day overrides table in calendar DB (`regular_hours_overrides`). Click any virtual chip to edit or remove that specific day without touching the global schedule.

2. **Month view** — third calendar mode. Full month grid, event chips show abbreviated.

3. **Route stats card** — sits below the routes carousel. Chart.js bar/line charts: avg commute by day of week, personal best/worst, trend over time. Calculated from existing `route_logs` data — no new schema needed.

4. **ICS calendar feed** — `GET /calendar.ics` endpoint that exports all events as a standard iCalendar file. Subscribe from Google Calendar, Outlook, iPhone, Android — any calendar app. Read-only from their side. No OAuth, no tokens, fully local. This is the preferred approach over bidirectional API sync.

5. **Event search** — substring match on title across all events. Search "Baseball" finds "Baseball Game". Results panel shows date, type, and a click-to-navigate link.

6. **Recurring events** — new `recurrences` table in calendar DB. Options: daily, weekly, biweekly, monthly. End date or "no end". Expanded virtually on the frontend (same as regular hours — no DB bloat). UI in the Add Event modal.

### Longer term
- **Google Calendar sync** (optional bidirectional push — lower priority than ICS feed)
- **"Leaving in X" countdown** — set a route + departure time, get a browser notification when it's time to leave based on current ETA (complements Phase 2 Start Route)
- **Server management UI** — dedicated web interface (or Electron app) to start/stop/restart WorkBuddy instances rather than using the batch file
- **Webhook / chat bot integration** for external alerts
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
- **Calendar height:** Matches the tallest of the Routes or Traffic columns via CSS grid `align-items: stretch`. Minimum 420px.
- **Regular hours:** Not stored in DB — rendered client-side from settings. Disabling the toggle immediately removes all chips with no data loss.
- **Map-drawn routes:** Stored as JSON in `routes.waypoints` field. TomTom routing uses the lat/lng coordinates directly for ETA without re-geocoding.
- **DB wipe procedure:** Stop server → delete the target `databases/*.db` file → restart. The migration chain recreates it at current schema version.
