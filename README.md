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
- Calendar (week/day) + manual events
- Spreadsheet schedule import (Excel/CSV)
- Windows system tray launcher

---

## 🔧 Stopping / Restarting

Use the system tray menu (**Stop Server** / **Restart Server**), or from a terminal:

```bash
workbuddy.bat stop
workbuddy.bat list    # connected devices (server must be running)
```

For details, see **Running the Project** in `WORKBUDDY_PROJECT.md`.
