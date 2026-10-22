"""
WorkBuddy Phase 2.5 — single-password Flask session auth.

When WORKBUDDY_AUTH_ENABLED=false (default), all helpers no-op so LAN
behavior is unchanged. No multi-user / OAuth — one household password.
"""
from __future__ import annotations

import os
import time
import threading
from typing import Optional

from flask import session
from werkzeug.security import check_password_hash, generate_password_hash


def _env_bool(key: str, default: bool = False) -> bool:
    v = os.getenv(key, "").strip().lower()
    if not v:
        return default
    return v in ("1", "true", "yes", "on")


def _env_int(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, str(default)).strip() or default)
    except ValueError:
        return default


AUTH_ENABLED = _env_bool("WORKBUDDY_AUTH_ENABLED", False)
PASSWORD_HASH = os.getenv("WORKBUDDY_PASSWORD_HASH", "").strip()
SESSION_LIFETIME_HOURS = _env_int("SESSION_LIFETIME_HOURS", 4)
LOGIN_RATE_LIMIT = _env_int("LOGIN_RATE_LIMIT", 5)
LOGIN_RATE_WINDOW_SEC = _env_int("LOGIN_RATE_WINDOW_SEC", 300)

# In-memory failed-login timestamps per IP. Fine for a single-user home server.
_login_fails: dict[str, list[float]] = {}
_login_lock = threading.Lock()


def hash_password(plain: str) -> str:
    return generate_password_hash(plain)


def verify_password(plain: str) -> bool:
    if not PASSWORD_HASH or not plain:
        return False
    try:
        return check_password_hash(PASSWORD_HASH, plain)
    except Exception:
        return False


def mark_authenticated() -> None:
    now = time.time()
    session.clear()
    session["authenticated"] = True
    session["auth_at"] = now
    session["last_active"] = now
    session.permanent = True


def clear_auth() -> None:
    session.clear()


def touch_active() -> None:
    if session.get("authenticated"):
        session["last_active"] = time.time()


def session_valid() -> bool:
    """True when cookie marks login and max age has not elapsed."""
    if not session.get("authenticated"):
        return False
    auth_at = session.get("auth_at")
    if auth_at is None:
        return False
    try:
        age = time.time() - float(auth_at)
    except (TypeError, ValueError):
        return False
    if age >= SESSION_LIFETIME_HOURS * 3600:
        return False
    return True


def login_rate_limited(ip: Optional[str]) -> bool:
    """Return True if this IP has too many recent failed attempts."""
    if not ip:
        return False
    now = time.time()
    cutoff = now - LOGIN_RATE_WINDOW_SEC
    with _login_lock:
        stamps = [t for t in _login_fails.get(ip, []) if t >= cutoff]
        _login_fails[ip] = stamps
        return len(stamps) >= LOGIN_RATE_LIMIT


def record_login_failure(ip: Optional[str]) -> None:
    if not ip:
        return
    now = time.time()
    cutoff = now - LOGIN_RATE_WINDOW_SEC
    with _login_lock:
        stamps = [t for t in _login_fails.get(ip, []) if t >= cutoff]
        stamps.append(now)
        _login_fails[ip] = stamps


def clear_login_failures(ip: Optional[str]) -> None:
    if not ip:
        return
    with _login_lock:
        _login_fails.pop(ip, None)


def is_auth_exempt(path: str) -> bool:
    if path.startswith("/static"):
        return True
    if path in ("/login", "/logout", "/favicon.ico"):
        return True
    return False
