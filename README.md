# 🌴 WorkBuddy

A local ops dashboard — weather, traffic, routes, MLB schedule, and calendar.

---

## 🚀 Quick Start

### 1. Install Python dependencies
```bash
pip install -r requirements.txt
```

### 2. Set up your API keys (secure — never committed to git)
```bash
# Copy the template
cp .env.example .env

# Open .env in any text editor and fill in your keys
notepad .env        # Windows
```

Your `.env` file should look like:
```
OPENWEATHER_API_KEY=your_key_here
TOMTOM_API_KEY=your_key_here
SECRET_KEY=some-long-random-string-you-make-up
```

> ⚠️ `.env` is in `.gitignore` — it will never be committed or shared.

### 3. Run the app
```bash
python workbuddy_tray.py
```

Or double-click `workbuddy.bat`.

### 4. Open your browser
```
http://localhost:5000
```

Console (live logs): `http://localhost:5000/console`

---

## 🔑 API Keys

| Service | Free Tier | Where to Get |
|---|---|---|
| OpenWeatherMap | ✅ Yes | https://openweathermap.org/api |
| TomTom | ✅ Yes (2,500 req/day) | https://developer.tomtom.com/ |
| MLB Stats API | ✅ No key needed | Built in |

---

## 📁 Project Structure

See `WORKBUDDY_PROJECT.md` for full architecture, API routes, and database layout.

---

## 📋 Features

- Weather (OpenWeatherMap)
- Live traffic for configurable highway corridors (TomTom)
- MLB schedule with highlighted team games
- Routes CRUD + TomTom ETA + route logging + history
- Live route sessions + mobile web client (`/m/`)
- Calendar (week/day) + manual events
- Spreadsheet schedule import (Excel/CSV)
- Optional password gate for remote access (Phase 2.5)
- Windows system tray launcher

---

## 🔐 Remote access (optional)

By default the dashboard trusts your LAN (`WORKBUDDY_AUTH_ENABLED=false`).

To expose it outside home Wi‑Fi (port forward, Cloudflare Tunnel, Tailscale, etc.):

1. Generate a password hash (never store plaintext in `.env`):
   ```bash
   python workbuddy_admin.py hash-password
   ```
2. Put the printed `WORKBUDDY_PASSWORD_HASH=...` in `.env` and set:
   ```
   WORKBUDDY_AUTH_ENABLED=true
   SESSION_LIFETIME_HOURS=4
   ```
3. Restart WorkBuddy. Visit `/login` — sessions expire after 4 hours; failed logins are rate-limited.

**Transport:** Login is not a substitute for HTTPS. Prefer a tunnel/VPN (Tailscale, Cloudflare Tunnel, Caddy) over raw HTTP port-forwarding — browsers often block geolocation on insecure origins.

### Tailscale + Safari GPS (recommended for commute)

Safari on iPhone often **will not show a location prompt** on plain `http://100.x.x.x:5000`. Use Tailscale HTTPS Serve on the PC:

```bash
# On the WorkBuddy PC (Tailscale installed, WorkBuddy running on :5000)
tailscale serve --bg 5000
```

Then on the phone open the HTTPS MagicDNS URL Tailscale prints, e.g.:

`https://your-pc.tailnet-name.ts.net/m/`

(Keep Tailscale connected on the phone.) After that, **START ROUTE** or **📍 Me** should prompt for Location.

See `PHASE2.5_REMOTE_ACCESS.md` for the full auth spec.

---

## 🔧 Stopping / Restarting

Use the system tray menu (**Stop Server** / **Restart Server**), or from a terminal:

```bash
workbuddy.bat stop
workbuddy.bat list    # connected devices (server must be running)
```

For details, see **Running the Project** in `WORKBUDDY_PROJECT.md`.
