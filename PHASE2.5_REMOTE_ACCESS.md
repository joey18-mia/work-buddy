# WorkBuddy — Phase 2.5: Remote Access & Login

**When to build:** After Phase 2 mobile web app is **complete** (all items in `PHASE2_IMPLEMENTATION.md` acceptance criteria).  
**Why:** Enable safe access to `/m/` and the API from outside the home LAN (e.g. during a commute) without exposing an open dashboard.

This is **not** multi-user auth — single password, single household.

---

## Goals

1. **Login** — password gate before any page or API.  
2. **Rate limiting** — slow brute-force on `/login` (and optionally all routes).  
3. **Session expiry** — auto sign-out after **4 hours** of login.  
4. Support remote use case: phone on LTE → home server (port forward, tunnel, or VPN).

---

## Non-goals (this phase)

- OAuth / Google / Apple sign-in  
- Multiple user accounts or roles  
- Per-device API tokens UI  
- Replacing HTTPS / VPN recommendations (see below)

---

## Configuration (`.env`)

```env
# Phase 2.5 — remote access (all optional until this phase ships)
WORKBUDDY_AUTH_ENABLED=true          # false = current behavior (LAN trust model)
WORKBUDDY_PASSWORD_HASH=...          # werkzeug hash; generate once at setup
SESSION_LIFETIME_HOURS=4             # default 4; auto sign-out after idle or max age

# Optional: stricter login throttling
LOGIN_RATE_LIMIT=5                   # max attempts
LOGIN_RATE_WINDOW_SEC=300            # per IP, per window (e.g. 5 per 5 min)
```

**Password setup:** CLI helper or one-time setup page that prints `generate_password_hash(...)` for `.env`. Never commit `WORKBUDDY_PASSWORD` plaintext.

When `WORKBUDDY_AUTH_ENABLED=false`, skip all auth checks (home LAN dev).

---

## Auth model

| Piece | Choice |
|---|---|
| Mechanism | Flask **signed session cookie** (uses existing `SECRET_KEY`) |
| Identity | Single shared password — no username field |
| Storage | No new DB table required; session only |
| Password verify | `werkzeug.security.check_password_hash` |

**Session payload:**

```python
session["authenticated"] = True
session["auth_at"] = time.time()   # login timestamp for 4h cap
session["last_active"] = time.time()  # optional: refresh on each request
```

**Expiry rule (locked):** Sign out when **either**:

- `now - auth_at >= SESSION_LIFETIME_HOURS` (4h max session), or  
- User hits **Logout**

Optional: also expire after 4h **idle** (no requests) — same window, simpler UX to use max age only unless idle is added later.

---

## Routes to protect

When `WORKBUDDY_AUTH_ENABLED=true`, require valid session on:

| Path | Notes |
|---|---|
| `/`, `/console`, `/m/*` | All HTML pages |
| `/api/*` | **All** JSON APIs, including `/api/sessions/*` and location POSTs |
| `/static/*` | **Public** — CSS/JS needed to render login page |

**Public (unauthenticated):**

| Path | Notes |
|---|---|
| `GET/POST /login` | Login form + submit |
| `POST /logout` | Clear session |
| `/static/*` | Assets |

Return **401 JSON** for unauthenticated API calls; **302 → /login** for HTML.

---

## Login UI

- `templates/login.html` — mobile-first, matches `/m/` visual style.  
- Fields: password only (+ show/hide toggle).  
- Error: generic “Invalid password” (no hint whether user exists).  
- After login: redirect to `next` query param or `/m/` if mobile User-Agent else `/`.  
- Desktop dashboard and mobile share the same session.

---

## Rate limiting

**Primary target:** `POST /login`

| Rule | Default |
|---|---|
| Max failed attempts per IP | 5 |
| Window | 5 minutes |
| On exceed | 429 + “Too many attempts. Try again later.” |
| Storage | In-memory dict `{ ip: [timestamps] }` with periodic cleanup — sufficient for single-user home server |

**Optional:** Global soft limit on `/api/*` (e.g. 120/min per IP) to reduce abuse if port is forwarded. Not required for v1.

**Implementation:** Small helper in `app.py` or `auth.py` — no Redis dependency.

---

## `@app.before_request` guard (sketch)

```python
AUTH_EXEMPT = {"/login", "/logout", "/static"}

@app.before_request
def require_auth():
    if not AUTH_ENABLED:
        return
    if request.path.startswith("/static"):
        return
    if request.path in AUTH_EXEMPT:
        return
    if not _session_valid():
        if request.path.startswith("/api/"):
            return jsonify({"error": "unauthorized"}), 401
        return redirect(url_for("login", next=request.path))
    session["last_active"] = time.time()
```

`_session_valid()` checks `authenticated` and 4h cap.

---

## HTTPS & remote access (operational notes)

Login **does not** remove the need for transport security on the public internet.

| Setup | GPS on phone | Login helps |
|---|---|---|
| LAN Wi‑Fi only | HTTP usually fine | Optional |
| Port forward + HTTP | Geolocation may block | Partial |
| **HTTPS** (Caddy, Cloudflare Tunnel) | Reliable | Yes |
| **Tailscale** (no public port) | Reliable on tailnet IP | Yes |

Document in README when Phase 2.5 ships; not implemented in code.

---

## Implementation order

1. `auth.py` or module section in `app.py` — hash check, session helpers, expiry.  
2. `login.html` + `/login`, `/logout` routes.  
3. `before_request` guard + API 401 behavior.  
4. Rate limiter on `POST /login`.  
5. `.env.example` entries + setup note in README.  
6. Manual test: desktop login, mobile `/m/` login, session expires after 4h, brute-force returns 429.

---

## Acceptance criteria

- [x] With `WORKBUDDY_AUTH_ENABLED=true`, unauthenticated `/` and `/api/routes` return redirect / 401.  
- [x] Correct password → session cookie → full app access (desktop + `/m/`).  
- [x] Wrong password → error, no session.  
- [x] After 4 hours from login, next request forces re-login.  
- [x] Logout clears session immediately.  
- [x] 5+ failed logins from same IP within 5 min → 429.  
- [x] `WORKBUDDY_AUTH_ENABLED=false` → zero behavior change from pre-2.5.  
- [x] Active **route session** (GPS): if auth expires mid-drive, location POSTs get 401 — mobile should show “Session expired — log in again” (mobile.js follow-up).

---

## Mobile mid-commute edge case

If the **login** session expires during an active **route** session:

- Server still has `route_logs.status='active'` until user re-auths and ends route, or auto-end fires server-side without client GPS.  
- **v1:** Acceptable — 4h exceeds any commute; document.  
- **v2 (optional):** Exempt `/api/sessions/:id/location` when session cookie expired but valid `route_session_id` + one-time token issued at Start Route — only if this becomes a real problem.

---

## Suggested prompt (after Phase 2 done)

```text
Implement WorkBuddy Phase 2.5 per PHASE2.5_REMOTE_ACCESS.md.

Single-password Flask session auth, 4-hour expiry, rate-limited login,
protect all pages and /api/* routes. AUTH_ENABLED=false preserves
today's open LAN behavior. Mobile-friendly login page matching /m/ style.
```
