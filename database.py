"""
WorkBuddy Database
==================
Three separate databases in workbuddy/databases/:

  workbuddy_calendar.db   — events
  workbuddy_routes.db     — routes, route_logs, checkpoints, checkpoint_logs
  workbuddy_settings.db   — settings (survives any other DB wipe)

Each DB has its own schema_version table and independent migration chain.
Migrations are STRICTLY ADDITIVE — they only add columns/tables, never
drop or modify existing data. Upgrading never loses anything.

On first run after switching from the old single workbuddy.db:
  - Old DB is backed up to old_db/workbuddy_legacy_[timestamp].db
  - Data is migrated into the new split DBs automatically
"""

import sqlite3
import os
import shutil
import logging
from datetime import datetime

BASE_DIR      = os.path.dirname(os.path.abspath(__file__))
DATABASES_DIR = os.path.join(BASE_DIR, "databases")
OLD_DB_DIR    = os.path.join(BASE_DIR, "old_db")
LEGACY_DB     = os.path.join(BASE_DIR, "workbuddy.db")  # old single-file DB

CALENDAR_DB  = os.path.join(DATABASES_DIR, "workbuddy_calendar.db")
ROUTES_DB    = os.path.join(DATABASES_DIR, "workbuddy_routes.db")
SETTINGS_DB  = os.path.join(DATABASES_DIR, "workbuddy_settings.db")

# Current target versions — bump when adding a new migration
CALENDAR_VERSION  = 1
ROUTES_VERSION    = 2
SETTINGS_VERSION  = 1

log = logging.getLogger("workbuddy")


# ─────────────────────────────────────────────────────────────
#  CONNECTION HELPERS
# ─────────────────────────────────────────────────────────────

def _open(path: str) -> sqlite3.Connection:
    os.makedirs(DATABASES_DIR, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def get_calendar_db()  -> sqlite3.Connection: return _open(CALENDAR_DB)
def get_routes_db()    -> sqlite3.Connection: return _open(ROUTES_DB)
def get_settings_db()  -> sqlite3.Connection: return _open(SETTINGS_DB)

# Keep one generic alias for code that only needs settings
def get_db() -> sqlite3.Connection: return get_settings_db()


# ─────────────────────────────────────────────────────────────
#  SCHEMA VERSION HELPERS
# ─────────────────────────────────────────────────────────────

def _get_version(conn) -> int:
    try:
        row = conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
        return row[0] if row else 0
    except sqlite3.OperationalError:
        return 0

def _set_version(conn, version: int):
    conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
    conn.execute("DELETE FROM schema_version")
    conn.execute("INSERT INTO schema_version (version) VALUES (?)", (version,))

def _col_exists(conn, table: str, col: str) -> bool:
    return col in [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]

def _table_exists(conn, table: str) -> bool:
    return bool(conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone())


# ─────────────────────────────────────────────────────────────
#  BACKUP
# ─────────────────────────────────────────────────────────────

def _backup(src_path: str, label: str):
    os.makedirs(OLD_DB_DIR, exist_ok=True)
    ts   = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    name = f"{label}_{ts}.db"
    dest = os.path.join(OLD_DB_DIR, name)
    shutil.copy2(src_path, dest)
    log.info(f"MIGRATE  | Backed up {os.path.basename(src_path)} → old_db/{name}")
    return dest


# ─────────────────────────────────────────────────────────────
#  CALENDAR DB MIGRATIONS
# ─────────────────────────────────────────────────────────────

def _cal_v0_to_v1(conn):
    """Baseline calendar schema."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            title       TEXT NOT NULL,
            event_date  TEXT NOT NULL,
            end_date    TEXT,
            start_time  TEXT,
            end_time    TEXT,
            event_type  TEXT DEFAULT 'personal',
            color       TEXT DEFAULT '#00A3E0',
            notes       TEXT,
            created_at  TEXT DEFAULT (datetime('now','localtime'))
        )""")
    # Additive guard: add end_date if missing (upgrading from very old schema)
    if not _col_exists(conn, "events", "end_date"):
        conn.execute("ALTER TABLE events ADD COLUMN end_date TEXT")
        conn.execute("UPDATE events SET end_date = event_date WHERE end_date IS NULL")
    log.info("MIGRATE  | calendar v0→v1 complete")

CALENDAR_MIGRATIONS = {1: _cal_v0_to_v1}


# ─────────────────────────────────────────────────────────────
#  ROUTES DB MIGRATIONS
# ─────────────────────────────────────────────────────────────

def _routes_v0_to_v1(conn):
    """Baseline routes schema including checkpoints."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS routes (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT NOT NULL,
            origin      TEXT NOT NULL,
            destination TEXT NOT NULL,
            waypoints   TEXT DEFAULT '',
            created_at  TEXT DEFAULT (datetime('now','localtime'))
        )""")
    if not _col_exists(conn, "routes", "waypoints"):
        conn.execute("ALTER TABLE routes ADD COLUMN waypoints TEXT DEFAULT ''")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS route_logs (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            route_id       INTEGER NOT NULL,
            log_date       TEXT NOT NULL,
            estimated_mins REAL,
            actual_mins    REAL NOT NULL,
            notes          TEXT,
            logged_at      TEXT DEFAULT (datetime('now','localtime')),
            FOREIGN KEY (route_id) REFERENCES routes(id) ON DELETE CASCADE
        )""")

    # Named checkpoints belonging to a route (ordered by position)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS checkpoints (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            route_id   INTEGER NOT NULL,
            name       TEXT NOT NULL,
            position   INTEGER NOT NULL DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now','localtime')),
            FOREIGN KEY (route_id) REFERENCES routes(id) ON DELETE CASCADE
        )""")

    # Per-trip checkpoint timing (fully optional — rows only inserted when user fills them in)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS checkpoint_logs (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            route_log_id        INTEGER NOT NULL,
            checkpoint_id       INTEGER NOT NULL,
            minutes_from_start  REAL NOT NULL,
            FOREIGN KEY (route_log_id)  REFERENCES route_logs(id)  ON DELETE CASCADE,
            FOREIGN KEY (checkpoint_id) REFERENCES checkpoints(id) ON DELETE CASCADE
        )""")

    log.info("MIGRATE  | routes v0→v1 complete (routes, logs, checkpoints, checkpoint_logs)")


def _routes_v1_to_v2(conn):
    """
    Phase 2 — live route sessions & geofencing support.
    Strictly additive: only ADD COLUMN + backfill existing rows.
      • routes:          destination coords + geofence radius + auto-end window
      • checkpoints:     lat/lng + geofence radius
      • route_logs:      session lifecycle (started/ended/status) + trip source
      • checkpoint_logs: passed_at timestamp for auto-logged passes
    """
    # ── routes ──────────────────────────────────────────────
    if not _col_exists(conn, "routes", "dest_lat"):
        conn.execute("ALTER TABLE routes ADD COLUMN dest_lat REAL")
    if not _col_exists(conn, "routes", "dest_lng"):
        conn.execute("ALTER TABLE routes ADD COLUMN dest_lng REAL")
    if not _col_exists(conn, "routes", "dest_radius_m"):
        conn.execute("ALTER TABLE routes ADD COLUMN dest_radius_m INTEGER DEFAULT 150")
    if not _col_exists(conn, "routes", "auto_end_minutes"):
        conn.execute("ALTER TABLE routes ADD COLUMN auto_end_minutes INTEGER DEFAULT 5")

    # ── checkpoints ─────────────────────────────────────────
    if not _col_exists(conn, "checkpoints", "lat"):
        conn.execute("ALTER TABLE checkpoints ADD COLUMN lat REAL")
    if not _col_exists(conn, "checkpoints", "lng"):
        conn.execute("ALTER TABLE checkpoints ADD COLUMN lng REAL")
    if not _col_exists(conn, "checkpoints", "radius_m"):
        conn.execute("ALTER TABLE checkpoints ADD COLUMN radius_m INTEGER DEFAULT 150")

    # ── route_logs ──────────────────────────────────────────
    if not _col_exists(conn, "route_logs", "started_at"):
        conn.execute("ALTER TABLE route_logs ADD COLUMN started_at TEXT")
    if not _col_exists(conn, "route_logs", "ended_at"):
        conn.execute("ALTER TABLE route_logs ADD COLUMN ended_at TEXT")
    if not _col_exists(conn, "route_logs", "status"):
        conn.execute("ALTER TABLE route_logs ADD COLUMN status TEXT DEFAULT 'completed'")
    if not _col_exists(conn, "route_logs", "source"):
        conn.execute("ALTER TABLE route_logs ADD COLUMN source TEXT DEFAULT 'manual'")

    # ── checkpoint_logs ─────────────────────────────────────
    if not _col_exists(conn, "checkpoint_logs", "passed_at"):
        conn.execute("ALTER TABLE checkpoint_logs ADD COLUMN passed_at TEXT")

    # ── post-migration data fix (required) ──────────────────
    # All pre-existing trips are manual, completed logs.
    conn.execute("UPDATE route_logs SET source = 'manual' WHERE source IS NULL OR source = ''")
    conn.execute("UPDATE route_logs SET status = 'completed' WHERE status IS NULL OR status = ''")

    log.info("MIGRATE  | routes v1→v2 complete (sessions, geofence, source; existing logs → manual/completed)")

ROUTES_MIGRATIONS = {1: _routes_v0_to_v1, 2: _routes_v1_to_v2}


# ─────────────────────────────────────────────────────────────
#  SETTINGS DB MIGRATIONS
# ─────────────────────────────────────────────────────────────

def _settings_v0_to_v1(conn):
    """Baseline settings schema with all defaults."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )""")
    defaults = {
        "watched_person":        "",
        "regular_hours_enabled": "false",
        "regular_hours_start":   "09:00",
        "regular_hours_end":     "17:00",
        "regular_hours_days":    "1,2,3,4,5",
        "uploads_override":      "false",
        "time_format":           "12h",
    }
    for k, v in defaults.items():
        conn.execute("INSERT OR IGNORE INTO settings (key,value) VALUES (?,?)", (k, v))
    log.info("MIGRATE  | settings v0→v1 complete")

SETTINGS_MIGRATIONS = {1: _settings_v0_to_v1}


# ─────────────────────────────────────────────────────────────
#  GENERIC MIGRATION RUNNER
# ─────────────────────────────────────────────────────────────

def _run_migrations(db_path: str, label: str, migrations: dict, target_version: int):
    conn    = _open(db_path)
    current = _get_version(conn)
    if current >= target_version:
        log.info(f"DATABASE | {label} v{current} — up to date")
        conn.close()
        return
    conn.close()

    print(f"[WorkBuddy] {label}: v{current} → v{target_version} — backing up and migrating...")
    _backup(db_path, label)

    conn = _open(db_path)
    for v in range(current + 1, target_version + 1):
        if v in migrations:
            try:
                migrations[v](conn)
                _set_version(conn, v)
                conn.commit()
                print(f"[WorkBuddy] {label}: migration to v{v} complete.")
            except Exception as e:
                conn.rollback()
                conn.close()
                raise RuntimeError(f"{label} migration to v{v} failed: {e}") from e
    conn.close()
    log.info(f"DATABASE | {label} now at v{target_version}")


# ─────────────────────────────────────────────────────────────
#  LEGACY MIGRATION  (old single workbuddy.db → three new DBs)
# ─────────────────────────────────────────────────────────────

def _migrate_legacy():
    """
    If workbuddy.db exists in the root (old single-DB layout), back it up
    and copy its data into the new split DBs.  Runs once, then the legacy
    DB is renamed so this never runs again.
    """
    if not os.path.exists(LEGACY_DB):
        return

    print("[WorkBuddy] Detected legacy workbuddy.db — migrating to split databases...")
    log.info("MIGRATE  | Legacy DB detected — starting one-time migration")
    _backup(LEGACY_DB, "workbuddy_legacy")

    try:
        old = sqlite3.connect(LEGACY_DB)
        old.row_factory = sqlite3.Row

        # ── Events → calendar DB ──────────────────────────────
        cal = get_calendar_db()
        try:
            rows = old.execute("SELECT * FROM events").fetchall()
            for r in rows:
                cal.execute("""
                    INSERT OR IGNORE INTO events
                    (id,title,event_date,end_date,start_time,end_time,event_type,color,notes,created_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (r["id"], r["title"], r["event_date"],
                     r["end_date"] if "end_date" in r.keys() else r["event_date"],
                     r["start_time"], r["end_time"], r["event_type"],
                     r["color"], r["notes"],
                     r["created_at"] if "created_at" in r.keys() else None))
            cal.commit()
            log.info(f"MIGRATE  | Copied {len(rows)} events from legacy DB")
        except Exception as e:
            log.warning(f"MIGRATE  | Events copy skipped: {e}")
        finally:
            cal.close()

        # ── Routes + logs → routes DB ─────────────────────────
        rts = get_routes_db()
        try:
            routes = old.execute("SELECT * FROM routes").fetchall()
            for r in routes:
                rts.execute("""
                    INSERT OR IGNORE INTO routes (id,name,origin,destination,waypoints,created_at)
                    VALUES (?,?,?,?,?,?)""",
                    (r["id"], r["name"], r["origin"], r["destination"],
                     r["waypoints"] if "waypoints" in r.keys() else "",
                     r["created_at"] if "created_at" in r.keys() else None))
            logs = old.execute("SELECT * FROM route_logs").fetchall()
            for r in logs:
                rts.execute("""
                    INSERT OR IGNORE INTO route_logs
                    (id,route_id,log_date,estimated_mins,actual_mins,notes,logged_at)
                    VALUES (?,?,?,?,?,?,?)""",
                    (r["id"], r["route_id"], r["log_date"],
                     r["estimated_mins"], r["actual_mins"], r["notes"],
                     r["logged_at"] if "logged_at" in r.keys() else None))
            rts.commit()
            log.info(f"MIGRATE  | Copied {len(routes)} routes, {len(logs)} logs from legacy DB")
        except Exception as e:
            log.warning(f"MIGRATE  | Routes copy skipped: {e}")
        finally:
            rts.close()

        # ── Settings → settings DB ────────────────────────────
        stg = get_settings_db()
        try:
            rows = old.execute("SELECT key,value FROM settings").fetchall()
            for r in rows:
                stg.execute(
                    "INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)",
                    (r["key"], r["value"]))
            stg.commit()
            log.info(f"MIGRATE  | Copied {len(rows)} settings from legacy DB")
        except Exception as e:
            log.warning(f"MIGRATE  | Settings copy skipped: {e}")
        finally:
            stg.close()

        old.close()

        # Rename legacy DB so this block never runs again
        done_path = LEGACY_DB.replace(".db", "_migrated.db")
        os.rename(LEGACY_DB, done_path)
        print(f"[WorkBuddy] Legacy migration complete. Old DB renamed to {os.path.basename(done_path)}")
        log.info(f"MIGRATE  | Legacy DB renamed to {os.path.basename(done_path)}")

    except Exception as e:
        log.error(f"MIGRATE  | Legacy migration failed: {e}")
        print(f"[WorkBuddy] WARNING: Legacy migration failed: {e}")


# ─────────────────────────────────────────────────────────────
#  MAIN ENTRY POINT
# ─────────────────────────────────────────────────────────────

def init_db():
    """Called once at startup. Initialises all three DBs and migrates if needed."""
    os.makedirs(DATABASES_DIR, exist_ok=True)
    os.makedirs(OLD_DB_DIR,    exist_ok=True)

    # Run each DB's migration chain first (creates tables if brand-new)
    _run_migrations(SETTINGS_DB, "workbuddy_settings", SETTINGS_MIGRATIONS, SETTINGS_VERSION)
    _run_migrations(CALENDAR_DB, "workbuddy_calendar", CALENDAR_MIGRATIONS, CALENDAR_VERSION)
    _run_migrations(ROUTES_DB,   "workbuddy_routes",   ROUTES_MIGRATIONS,   ROUTES_VERSION)

    # Then migrate legacy data if old single DB exists
    _migrate_legacy()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s")
    init_db()
    print("Done.")
