# WorkBuddy — Phase 2 Implementation Prompt

**Use this document as the sole spec when implementing Phase 2.**  
Reference `WORKBUDDY_PROJECT.md` for broader project context only if needed.

---

## Mission

Build a **mobile web client** (`/m/`) and **live route sessions** on the existing Flask server:

1. **Start Route** — one tap records departure time (`started_at`).
2. **End Route** — manual arrival, or auto-end after dwelling at destination geofence.
3. **GPS checkpoints** — optional geofence pass detection while session is active.
4. **Settings parity** — mobile uses same `/api/settings` and route APIs as desktop.

**Do not** break existing desktop dashboard or manual **Log Route** flow.

---

## Stack & constraints

| Item | Value |
|---|---|
| Backend | Python 3, Flask, SQLite (`database.py` migration chain) |
| Frontend | Vanilla HTML/CSS/JS — no React/Vue |
| Maps | Leaflet (already in project) |
| Server | Waitress, LAN-only (`0.0.0.0`) |
| Migrations | **Strictly additive** — `ALTER TABLE ADD COLUMN` only; backup to `old_db/` before migrate |

**Code style:** Match existing `app.py`, `static/js/app.js`, `templates/index.html` patterns. Minimal diff outside Phase 2 scope.

---

## Locked product decisions

| Topic | Rule |
|---|---|
| Active sessions | **One globally.** Cannot start route B while route A is `active`. Must cancel or end A first. |
| Location interval | **60 s** while GPS healthy. |
| GPS loss | Elapsed timer **never stops**. Stop location POSTs; retry fix **every 30 s**; pause geofence + auto-end until fix returns. Manual End always works. |
| Geofence default | **150 m** (`radius_m` per checkpoint; `dest_radius_m` on route). |
| Destination coords | **Option C** — resolve in this priority order: (1) `dest_lat/lng` on route → (2) parse `destination` as `lat,lng` → (3) map waypoints JSON `type:'end'` point → (4) geocode destination text, first result, cache on route → (5) fail Start with 400. |
| Trip `source` | Column on **`route_logs` only** (`manual` \| `live_session`). **All existing rows → `manual` on migrate.** Manual POST `/api/routes/:id/logs` unchanged. |

---

## Database — routes DB migration v1 → v2

Bump `ROUTES_VERSION` to `2` in `database.py`. Add `_routes_v1_to_v2(conn)`:

```sql
-- routes
ALTER TABLE routes ADD COLUMN dest_lat REAL;
ALTER TABLE routes ADD COLUMN dest_lng REAL;
ALTER TABLE routes ADD COLUMN dest_radius_m INTEGER DEFAULT 150;
ALTER TABLE routes ADD COLUMN auto_end_minutes INTEGER DEFAULT 5;

-- checkpoints
ALTER TABLE checkpoints ADD COLUMN lat REAL;
ALTER TABLE checkpoints ADD COLUMN lng REAL;
ALTER TABLE checkpoints ADD COLUMN radius_m INTEGER DEFAULT 150;

-- route_logs
ALTER TABLE route_logs ADD COLUMN started_at TEXT;
ALTER TABLE route_logs ADD COLUMN ended_at TEXT;
ALTER TABLE route_logs ADD COLUMN status TEXT DEFAULT 'completed';
ALTER TABLE route_logs ADD COLUMN source TEXT DEFAULT 'manual';

-- checkpoint_logs
ALTER TABLE checkpoint_logs ADD COLUMN passed_at TEXT;
```

**Post-migration data fix (required):**

```sql
UPDATE route_logs SET source = 'manual' WHERE source IS NULL OR source = '';
UPDATE route_logs SET status = 'completed' WHERE status IS NULL OR status = '';
```

**Session model:** An active drive is a `route_logs` row with `status = 'active'`, `source = 'live_session'`. No separate sessions table.

**Server state for geofencing:** Track per active session (in memory or small helper table): last known position, destination dwell start time, which checkpoints already passed. Do **not** persist a GPS breadcrumb trail.

---

## API — new endpoints

| Method | Route | Body / notes |
|---|---|---|
| `GET` | `/api/sessions/active` | Returns active session or `null`. Used to block second Start. |
| `POST` | `/api/routes/:id/sessions/start` | Resolve destination coords (priority chain). Create `route_logs` row: `status=active`, `source=live_session`, `started_at=now`, `log_date=today`. **409 if another session active.** |
| `POST` | `/api/sessions/:id/location` | `{ lat, lng, accuracy?, recorded_at? }` — run geofence engine. |
| `GET` | `/api/sessions/:id` | Status, elapsed seconds, checkpoints hit/missed, GPS ok/lost flag. |
| `POST` | `/api/sessions/:id/end` | Manual end → `status=completed`, compute `actual_mins`. |
| `POST` | `/api/sessions/:id/cancel` | `status=cancelled` — exclude from stats or delete row (pick one; document in code). |

**Geofence engine (on each location POST):**

1. If session not `active`, ignore.  
2. Next unpassed checkpoint (by `position`): if distance ≤ `radius_m`, insert `checkpoint_logs` with `minutes_from_start` and `passed_at`.  
3. Destination: if inside `dest_radius_m`, start/continue dwell timer; if dwell ≥ `auto_end_minutes` → `status=auto_completed`, set `ended_at`, compute `actual_mins`.  
4. Outside destination → reset dwell timer.

**Haversine** distance in Python (no new deps).

**Extend existing endpoints:**

- `PUT /api/routes/:id` — allow updating `dest_lat`, `dest_lng`, `dest_radius_m`, `auto_end_minutes` (add if missing).  
- `PUT /api/checkpoints/:id` — allow `lat`, `lng`, `radius_m`.  
- `POST /api/routes/:id/logs` — always set `source='manual'`; never create `active` session.

---

## Mobile client — `/m/`

**Files to add:**

```
templates/mobile/
  base.html       # shell, bottom nav, shared CSS links
  home.html
  routes.html
  route_detail.html
  calendar.html
  settings.html
static/
  css/mobile.css
  js/mobile.js
```

**Routes in `app.py`:**

- `GET /m/` → home  
- `GET /m/routes`, `GET /m/routes/<id>`, etc.  
- Or single-page shell with JS routing — either is fine if URLs are bookmarkable.

**Bottom nav:** Home | Routes | Calendar | Settings

**Route detail page:**

- Show route name, ETA (existing `/api/routes/:id/eta`).  
- **START ROUTE** (large button) — disabled if another session active.  
- Live elapsed timer (client-side from `started_at`, keeps ticking during GPS loss).  
- Checkpoint list with pass times when server reports them.  
- **END ROUTE** button.  
- Banner: “GPS lost — retrying…” when client cannot get fix.

**Location client loop (`mobile.js`):**

```text
on session active:
  every 60s: navigator.geolocation.getCurrentPosition → POST /api/sessions/:id/location
  on error: show GPS lost; retry every 30s until success; timer UI unaffected
on session end/cancel: clear intervals
```

Request geolocation permission on first Start.

**Settings:** Reuse `/api/settings` GET/POST — mobile-friendly forms for same keys as desktop.

---

## Desktop updates (minimal)

1. **Checkpoint editor** — map pin or lat/lng fields for each checkpoint.  
2. **Route editor** — destination pin → saves `dest_lat/lng`.  
3. **History table** — optional small badge: Manual / Live (read `source` field).  
4. Do **not** redesign desktop layout.

---

## Implementation order

1. `database.py` — `_routes_v1_to_v2`, bump version, test migration on copy of DB.  
2. Session APIs — start / end / cancel / active / status (no GPS logic yet).  
3. Destination coord resolver — shared helper used by start endpoint.  
4. Mobile shell — `/m/` with Start/End + timer (manual end only).  
5. Geofence engine — wire location POST.  
6. Map pin editors — desktop + mobile checkpoint/destination.  
7. Mobile settings + calendar condensed views.  
8. PWA meta tags + “keep screen awake” hint during active session (optional `navigator.wakeLock`).

---

## Acceptance criteria

- [x] Existing `route_logs` rows all have `source='manual'` after migration.  
- [x] Manual Log Route on desktop still works; creates `source=manual` only.  
- [x] Cannot start two sessions; 409 on second Start.  
- [x] Start Route records `started_at`; End computes `actual_mins` without user typing minutes.  
- [x] Map-drawn route starts without geocode.  
- [x] Text destination geocodes once, caches `dest_lat/lng`.  
- [x] Checkpoint auto-log fires once per checkpoint when inside 150 m.  
- [x] Auto-end after 5 min (configurable) inside destination geofence.  
- [x] GPS loss: timer runs, geofence paused, 30 s retry, manual End works.  
- [x] No continuous GPS track stored in DB.  
- [x] Mobile pages usable at 375px width; buttons ≥ 44px.

---

## Out of scope (Phase 2)

- Native iOS/Android app / Capacitor background GPS  
- Route stats charts (Phase 3 item)  
- ICS feed, month view, recurring events  
- Login, rate limiting, session expiry — **Phase 2.5** (see `PHASE2.5_REMOTE_ACCESS.md`; build after mobile web app is complete)  
- Multi-user / OAuth accounts  
- Changes to calendar/traffic/weather logic beyond mobile display  

---

## After Phase 2 — remote access (Phase 2.5)

When the mobile web app is finished, implement **login + rate limiting + 4-hour auto sign-out** so the server can be reached safely from outside the LAN. Full spec: **`PHASE2.5_REMOTE_ACCESS.md`**.

---

## Key existing files

| File | Role |
|---|---|
| `app.py` | All API routes; add session + mobile page routes here |
| `database.py` | Migrations; `get_routes_db()` |
| `static/js/app.js` | Desktop route/checkpoint/log UI — extend, don’t rewrite |
| `templates/index.html` | Desktop dashboard |
| `WORKBUDDY_PROJECT.md` | Full product spec § Phase 2 |

---

## Suggested first prompt to coding agent

```text
Implement WorkBuddy Phase 2 per PHASE2_IMPLEMENTATION.md.

Start with step 1 only: routes DB migration v1→v2 and verify existing
route_logs rows are migrated to source='manual' with status='completed'.
Do not implement mobile UI yet. Add tests or a small migration smoke
script if helpful. Match existing database.py migration patterns.
```

Subsequent prompts: one implementation-order step at a time.
