# ⚾ WorkBuddy

Your personal Miami ops dashboard — weather, traffic, routes, MLB, and calendar.

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
OPENWEATHER_API_KEY=abc123yourkeyhere
TOMTOM_API_KEY=xyz789yourkeyhere
SECRET_KEY=some-long-random-string-you-make-up
```

> ⚠️ `.env` is in `.gitignore` — it will never be committed or shared.

### 3. Run the app
```bash
python app.py
```

### 4. Open your browser
```
http://localhost:5000
```

---

## 🔑 API Keys

| Service | Free Tier | Where to Get |
|---|---|---|
| OpenWeatherMap | ✅ Yes | https://openweathermap.org/api |
| TomTom | ✅ Yes (2,500 req/day) | https://developer.tomtom.com/ |
| MLB Stats API | ✅ No key needed | Built in |

---

## 📁 Project Structure

```
workbuddy/
├── .env.example       ← Copy this to .env and fill in keys
├── .env               ← YOUR KEYS (never share this)
├── .gitignore         ← Keeps .env out of git
├── app.py             ← Flask backend + all API routes
├── database.py        ← SQLite setup
├── requirements.txt
├── workbuddy.db       ← Auto-created on first run
├── templates/
│   └── index.html
└── static/
    ├── css/style.css
    └── js/app.js
```

---

## 🗄️ Database Tables

| Table | Purpose |
|---|---|
| `routes` | Your saved A→B routes |
| `route_logs` | Historical trip times per route |
| `events` | Calendar events + work shifts |

---

## 📋 Features by Phase

### ✅ Phase 1 (current)
- Real-time Miami weather (OpenWeatherMap)
- Live traffic for 6 Miami-Dade corridors (TomTom)
- MLB schedule — Marlins game pinned to top
- Routes CRUD + TomTom ETA + route logging + history
- Calendar (week/day toggle) + manual events
- Schedule import framework (Excel/CSV)

### 🔜 Phase 2 (next)
- Excel/CSV work schedule parser (needs sample file)
- Chart.js visualizations for route history
- Weather alerts panel

---

## 🔧 Stopping / Restarting

```bash
# Stop:  Ctrl+C in terminal
# Restart:
python app.py
```
