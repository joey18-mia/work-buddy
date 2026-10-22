/* ═══════════════════════════════════════════════════════════
   WorkBuddy Mobile client
   Phase 2 · step 4 — shell + Start/End + client-side timer.
   GPS location POST loop is added in step 5 (geofence engine).
   ═══════════════════════════════════════════════════════════ */

const M = { fmt: "12h", timerInt: null, pollInt: null, gpsTimer: null, wakeLock: null };

/* Keep the screen awake during an active drive (best-effort). */
async function acquireWakeLock() {
  if (!("wakeLock" in navigator) || M.wakeLock) return;
  try {
    M.wakeLock = await navigator.wakeLock.request("screen");
    M.wakeLock.addEventListener("release", () => { M.wakeLock = null; });
  } catch (e) { /* denied / unsupported — timer still runs */ }
}

async function releaseWakeLock() {
  try { if (M.wakeLock) await M.wakeLock.release(); } catch (e) { /* ignore */ }
  M.wakeLock = null;
}

// Re-acquire the lock when returning to the tab if a drive is still active.
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible" && RD && RD.session) acquireWakeLock();
});

async function api(url, method = "GET", body) {
  const opt = { method, headers: {}, credentials: "same-origin" };
  if (body !== undefined) {
    opt.headers["Content-Type"] = "application/json";
    opt.body = JSON.stringify(body);
  }
  try {
    const r = await fetch(url, opt);
    let data = null;
    try { data = await r.json(); } catch (e) { /* no body */ }
    // Phase 2.5 — auth cookie expired mid-drive or mid-browse
    if (r.status === 401) {
      const msg = (data && data.message) || "Session expired — log in again";
      const banner = document.getElementById("m-gps-banner");
      if (banner) {
        banner.textContent = msg;
        banner.classList.remove("hidden");
      }
      const next = encodeURIComponent(location.pathname + location.search);
      setTimeout(() => { location.href = "/login?next=" + next; }, 600);
    }
    return { ok: r.ok, status: r.status, data };
  } catch (e) {
    return { ok: false, status: 0, data: null };
  }
}

function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function applyTheme() {
  const t = localStorage.getItem("wb-theme") || "dark-blue";
  document.documentElement.setAttribute("data-theme", t);
}

/* Parse the server's "YYYY-MM-DD HH:MM:SS" (local time) into epoch ms. */
function parseServerTime(ts) {
  if (!ts) return Date.now();
  const p = ts.replace("T", " ").split(/[-: ]/).map(Number);
  return new Date(p[0], (p[1] || 1) - 1, p[2] || 1, p[3] || 0, p[4] || 0, p[5] || 0).getTime();
}

function fmtHMS(totalSec) {
  const h = Math.floor(totalSec / 3600);
  const m = Math.floor((totalSec % 3600) / 60);
  const s = totalSec % 60;
  const mm = String(m).padStart(2, "0");
  const ss = String(s).padStart(2, "0");
  return h > 0 ? `${h}:${mm}:${ss}` : `${mm}:${ss}`;
}

/* ── Date header (home page) ──
   No live time clock on mobile — iOS already shows the time in its status
   bar, so we only render the current date where a page asks for it. */
function showDate() {
  const de = document.getElementById("m-date");
  if (!de) return;
  de.textContent = new Date().toLocaleDateString(undefined,
    { weekday: "long", month: "long", day: "numeric" });
}

/* ── Active-session banner (home + routes) ── */
async function loadActiveBanner() {
  const banner = document.getElementById("m-active-banner");
  if (!banner) return;
  const { data } = await api("/api/sessions/active");
  if (data && data.id) {
    banner.classList.remove("hidden");
    banner.href = "/m/routes/" + data.route_id;
    const sub = document.getElementById("m-active-route");
    if (sub) sub.textContent = data.route_name || "Route #" + data.route_id;
  } else {
    banner.classList.add("hidden");
  }
}

/* ── Weather (home) ── */
async function loadWeather() {
  const t = document.getElementById("m-weather-temp");
  const d = document.getElementById("m-weather-desc");
  if (!t) return;
  const { ok, data } = await api("/api/weather");
  if (ok && data && data.temp !== undefined) {
    t.textContent = data.temp + "°";
    d.textContent = data.description || "";
  } else {
    t.textContent = "—";
    d.textContent = "Weather unavailable";
  }
}

/* ── Routes list ── */
async function loadRoutesList() {
  const el = document.getElementById("m-routes-list");
  if (!el) return;
  const { data } = await api("/api/routes");
  if (!data || !data.length) {
    el.innerHTML = '<div class="m-empty">No routes yet. Create one on the desktop dashboard.</div>';
    return;
  }
  el.innerHTML = data.map((r) => `
    <a class="m-list-item" href="/m/routes/${r.id}">
      <div class="m-list-main">
        <div class="m-list-title">${esc(r.name)}</div>
        <div class="m-list-sub">${esc(r.origin)} → ${esc(r.destination)}</div>
      </div>
      <span class="m-chev">›</span>
    </a>`).join("");
}

/* ── Route detail ── */
const RD = { routeId: null, route: null, checkpoints: [], session: null };

async function initRouteDetail() {
  RD.routeId = parseInt(document.body.dataset.routeId, 10);
  const { data: routes } = await api("/api/routes");
  RD.route = (routes || []).find((r) => r.id === RD.routeId);
  if (RD.route) {
    document.getElementById("m-route-name").textContent = RD.route.name;
    document.getElementById("m-route-od").textContent =
      RD.route.origin + " → " + RD.route.destination;
  }
  updateDestStatus();
  await loadCheckpoints();
  await refreshSessionState();
  loadEta();
}

async function loadCheckpoints() {
  const { data } = await api(`/api/routes/${RD.routeId}/checkpoints`);
  RD.checkpoints = data || [];
  renderCheckpoints();
  updateGpsReady();
}

function cpHasPin(c) {
  return Number.isFinite(c.lat) && Number.isFinite(c.lng);
}

function destHasPin() {
  return RD.route && Number.isFinite(RD.route.dest_lat) && Number.isFinite(RD.route.dest_lng);
}

function updateGpsReady() {
  const sub = document.getElementById("m-gps-ready-sub");
  const list = document.getElementById("m-gps-ready-list");
  const card = document.getElementById("m-gps-ready-card");
  if (!sub || !list) return;

  const pinned = RD.checkpoints.filter(cpHasPin).length;
  const total = RD.checkpoints.length;
  const destOk = destHasPin();
  const ready = destOk && (total === 0 || pinned === total);

  if (card) card.classList.toggle("m-ready-ok", ready);
  if (card) card.classList.toggle("m-ready-warn", !ready);

  if (total === 0 && destOk) {
    sub.textContent = "Destination pinned. No checkpoints — timer + auto-end only.";
  } else if (ready) {
    sub.textContent = total
      ? `Ready — destination + ${pinned}/${total} checkpoints pinned.`
      : "Ready — destination pinned.";
  } else {
    const missing = [];
    if (!destOk) missing.push("destination");
    if (total && pinned < total) missing.push(`${total - pinned} checkpoint pin(s)`);
    sub.textContent = "Incomplete — still need: " + missing.join(", ") + ".";
  }

  const items = [];
  items.push(`<li class="${destOk ? "ok" : "miss"}">${destOk ? "✓" : "○"} Destination geofence</li>`);
  RD.checkpoints.forEach((c) => {
    const ok = cpHasPin(c);
    items.push(`<li class="${ok ? "ok" : "miss"}">${ok ? "✓" : "○"} ${esc(c.name)}</li>`);
  });
  if (!total) items.push(`<li class="ok">✓ No checkpoints to pin</li>`);
  list.innerHTML = items.join("");
}

function renderCheckpoints(passMap) {
  const el = document.getElementById("m-checkpoints");
  if (!el) return;
  if (!RD.checkpoints.length) {
    el.innerHTML = '<div class="m-empty">No checkpoints — add them on desktop, then pin here</div>';
    updateGpsReady();
    return;
  }
  el.innerHTML = RD.checkpoints.map((c) => {
    const hit = passMap && passMap[c.id];
    const sub = hit
      ? "Passed at " + hit.minutes_from_start + " min"
      : (RD.session ? "Waiting for geofence…" : "Not yet reached");
    const hasGeo = cpHasPin(c);
    const geo = hasGeo
      ? `📍 ${c.lat.toFixed(4)},${c.lng.toFixed(4)} · ${c.radius_m == null ? 150 : c.radius_m}m`
      : "No pin — won't auto-mark";
    return `
      <div class="m-list-item ${hit ? "passed" : ""}">
        <div class="m-cp-pos">${c.position}</div>
        <div class="m-list-main">
          <div class="m-list-title">${esc(c.name)}</div>
          <div class="m-list-sub">${sub}</div>
          <div class="m-cp-geo ${hasGeo ? "has-geo" : ""}">${geo}</div>
        </div>
        ${hit ? '<span class="m-check">✓</span>'
              : `<button class="m-cp-pinbtn ${hasGeo ? "has-geo" : ""}" onclick="editCheckpointPin(${c.id}, '${esc(c.name).replace(/'/g, "\\'")}')">📍 Pin</button>`}
      </div>`;
  }).join("");
  updateGpsReady();
}

function updateDestStatus() {
  const el = document.getElementById("m-dest-status");
  if (!el || !RD.route) return;
  if (destHasPin()) {
    const rad = RD.route.dest_radius_m == null ? 150 : RD.route.dest_radius_m;
    const ae = RD.route.auto_end_minutes == null ? 5 : RD.route.auto_end_minutes;
    el.textContent = `📍 ${RD.route.dest_lat.toFixed(4)},${RD.route.dest_lng.toFixed(4)} · ${rad}m · auto-end ${ae}m`;
    el.classList.add("has-geo");
  } else {
    el.textContent = "Not set — will try address / map waypoints at start";
    el.classList.remove("has-geo");
  }
  updateGpsReady();
}

function flashPass(name, mins) {
  const msg = document.getElementById("m-start-msg");
  if (!msg) return;
  msg.textContent = "✓ Passed " + name + " at " + mins + " min";
  msg.classList.add("m-msg-ok");
  clearTimeout(flashPass._t);
  flashPass._t = setTimeout(() => msg.classList.remove("m-msg-ok"), 4000);
}

async function loadEta() {
  const el = document.getElementById("m-eta");
  if (!el) return;
  const { ok, data } = await api(`/api/routes/${RD.routeId}/eta`);
  el.textContent = ok && data && data.travel_mins !== undefined
    ? data.travel_mins + " min"
    : "—";
}

async function refreshSessionState() {
  const startBtn = document.getElementById("m-start-btn");
  const endBtn = document.getElementById("m-end-btn");
  const timerCard = document.getElementById("m-timer-card");
  const msg = document.getElementById("m-start-msg");
  const { data: active } = await api("/api/sessions/active");

  if (active && active.id && active.route_id === RD.routeId) {
    // This route is the live one.
    RD.session = active;
    startBtn.classList.add("hidden");
    endBtn.classList.remove("hidden");
    timerCard.classList.remove("hidden");
    startTimer(active.started_at);
    startPolling(active.id);
    startGpsLoop(active.id);
    acquireWakeLock();
  } else if (active && active.id) {
    // A different route is active → block Start.
    RD.session = null;
    stopTimer();
    stopPolling();
    stopGpsLoop();
    releaseWakeLock();
    startBtn.classList.remove("hidden");
    startBtn.disabled = true;
    startBtn.textContent = "ANOTHER ROUTE ACTIVE";
    endBtn.classList.add("hidden");
    timerCard.classList.add("hidden");
    if (msg) msg.textContent = "Finish the active route before starting this one.";
  } else {
    // Idle.
    RD.session = null;
    stopTimer();
    stopPolling();
    stopGpsLoop();
    releaseWakeLock();
    startBtn.classList.remove("hidden");
    startBtn.disabled = false;
    startBtn.textContent = "START ROUTE";
    endBtn.classList.add("hidden");
    timerCard.classList.add("hidden");
    if (msg) msg.textContent = "";
  }
}

/* GPS banner + location loop.
   Success → next fix in 60s. Failure → GPS-lost banner, retry in 30s.
   The elapsed timer is independent and keeps running regardless. */
function setGpsBanner(lost, detail) {
  const b = document.getElementById("m-gps-banner");
  if (!b) return;
  b.classList.toggle("hidden", !lost);
  if (lost) {
    b.textContent = detail
      ? ("📡 " + detail)
      : "📡 GPS lost — retrying…";
  }
}

function gpsInsecureHint() {
  // Safari (and Chrome) require a secure context for the permission prompt.
  // Plain http://100.x Tailscale IPs are often NOT secure → no prompt, silent fail.
  if (typeof window.isSecureContext === "boolean" && !window.isSecureContext) {
    return "Location blocked: open WorkBuddy over HTTPS (Tailscale Serve) or home Wi‑Fi localhost — plain http:// IPs won't prompt in Safari.";
  }
  return null;
}

function startGpsLoop(sessionId) {
  stopGpsLoop();
  const insecure = gpsInsecureHint();
  if (insecure) {
    setGpsBanner(true, insecure);
    const msg = document.getElementById("m-start-msg");
    if (msg) msg.textContent = insecure;
    // Still try — some browsers allow LAN HTTP — but surface why prompt may never appear.
  }
  getFix(sessionId);
}

function stopGpsLoop() {
  clearTimeout(M.gpsTimer);
  M.gpsTimer = null;
  setGpsBanner(false);
}

function geoErrorText(err) {
  if (!err) return "GPS failed";
  // 1 PERMISSION_DENIED, 2 POSITION_UNAVAILABLE, 3 TIMEOUT
  if (err.code === 1) {
    const insecure = gpsInsecureHint();
    return insecure || "Location permission denied — check Safari site settings for this address.";
  }
  if (err.code === 2) return "GPS unavailable — try outdoors / wait for a fix.";
  if (err.code === 3) return "GPS timed out — retrying…";
  return err.message || "GPS failed";
}

function getFix(sessionId) {
  if (!navigator.geolocation) {
    setGpsBanner(true, "Geolocation API missing in this browser");
    M.gpsTimer = setTimeout(() => getFix(sessionId), 30000);
    return;
  }
  navigator.geolocation.getCurrentPosition(
    (pos) => { onFix(sessionId, pos); M.gpsTimer = setTimeout(() => getFix(sessionId), 60000); },
    (err) => {
      setGpsBanner(true, geoErrorText(err));
      M.gpsTimer = setTimeout(() => getFix(sessionId), 30000);
    },
    { enableHighAccuracy: true, timeout: 20000, maximumAge: 0 }
  );
}

async function onFix(sessionId, pos) {
  setGpsBanner(false);
  // Show the current location in the timer card, refreshed on each fix
  // (i.e. at the same cadence we POST it to the server).
  const locEl = document.getElementById("m-timer-loc");
  if (locEl) {
    const acc = Number.isFinite(pos.coords.accuracy) ? ` · ±${Math.round(pos.coords.accuracy)}m` : "";
    locEl.textContent = `📍 ${pos.coords.latitude.toFixed(5)}, ${pos.coords.longitude.toFixed(5)}${acc}`;
  }
  const { ok, data } = await api("/api/sessions/" + sessionId + "/location", "POST", {
    lat: pos.coords.latitude,
    lng: pos.coords.longitude,
    accuracy: pos.coords.accuracy,
  });
  if (!ok || !data) return;
  if (data.status && data.status !== "active") {
    await handleSessionClosed("Route auto-ended at the destination.");
    return;
  }
  // Reflect any checkpoint pass immediately (poll would also catch it).
  if (data.events && data.events.checkpoints_passed && data.events.checkpoints_passed.length) {
    data.events.checkpoints_passed.forEach((c) => flashPass(c.name, c.minutes_from_start));
    const { data: s } = await api("/api/sessions/" + sessionId);
    if (s && s.checkpoints) {
      const passMap = {};
      s.checkpoints.forEach((c) => { if (c.passed) passMap[c.id] = { minutes_from_start: c.minutes_from_start }; });
      renderCheckpoints(passMap);
    }
  }
}

async function handleSessionClosed(message) {
  stopTimer();
  stopPolling();
  stopGpsLoop();
  const msg = document.getElementById("m-start-msg");
  if (msg && message) msg.textContent = message;
  await refreshSessionState();
}

function startTimer(startedAt) {
  const el = document.getElementById("m-timer");
  const startedEl = document.getElementById("m-timer-started");
  const locEl = document.getElementById("m-timer-loc");
  if (locEl) locEl.textContent = "📍 Locating…";
  const startMs = parseServerTime(startedAt);
  if (startedEl) {
    startedEl.textContent = "Started " + new Date(startMs).toLocaleTimeString();
  }
  clearInterval(M.timerInt);
  function tick() {
    el.textContent = fmtHMS(Math.max(0, Math.floor((Date.now() - startMs) / 1000)));
  }
  tick();
  M.timerInt = setInterval(tick, 1000);
}

function stopTimer() {
  clearInterval(M.timerInt);
  M.timerInt = null;
}

/* Poll session status so checkpoint passes / auto-end show up (step 5). */
function startPolling(sessionId) {
  clearInterval(M.pollInt);
  async function poll() {
    const { ok, data } = await api("/api/sessions/" + sessionId);
    if (!ok || !data) return;
    if (data.status !== "active") {
      await handleSessionClosed("Route auto-ended at the destination.");
      return;
    }
    const passMap = {};
    (data.checkpoints || []).forEach((c) => {
      if (c.passed) passMap[c.id] = { minutes_from_start: c.minutes_from_start };
    });
    renderCheckpoints(passMap);
    setGpsBanner(data.gps_ok === false);
  }
  poll();
  M.pollInt = setInterval(poll, 15000);
}

function stopPolling() {
  clearInterval(M.pollInt);
  M.pollInt = null;
}

async function startRoute() {
  const btn = document.getElementById("m-start-btn");
  const msg = document.getElementById("m-start-msg");

  // Soft gate: warn if geofences aren't ready, but still allow Start.
  const unpinned = RD.checkpoints.filter((c) => !cpHasPin(c));
  if (!destHasPin() || unpinned.length) {
    const lines = [];
    if (!destHasPin()) lines.push("Destination pin not set (will try address/map waypoints).");
    if (unpinned.length) {
      lines.push(unpinned.length + " checkpoint(s) without pins won't auto-mark:");
      unpinned.forEach((c) => lines.push("  • " + c.name));
    }
    if (!confirm("GPS setup incomplete:\n\n" + lines.join("\n") + "\n\nStart anyway?")) return;
  }

  if (!navigator.geolocation) {
    if (!confirm("This device has no geolocation. Timer will still run, but checkpoints won't auto-mark. Start anyway?")) return;
  }

  btn.disabled = true;
  if (msg) { msg.textContent = ""; msg.classList.remove("m-msg-ok"); }

  const { ok, status, data } = await api(`/api/routes/${RD.routeId}/sessions/start`, "POST", {});
  if (ok) {
    // refreshSessionState() starts the timer, poller, and GPS loop — the
    // first geolocation call prompts the user for permission.
    await refreshSessionState();
  } else {
    if (msg) msg.textContent = (data && data.error) || `Could not start (${status}).`;
    btn.disabled = false;
  }
}

async function endRoute() {
  if (!RD.session) return;
  const btn = document.getElementById("m-end-btn");
  const msg = document.getElementById("m-start-msg");
  btn.disabled = true;
  const { ok, data } = await api("/api/sessions/" + RD.session.id + "/end", "POST", {});
  stopTimer();
  stopPolling();
  stopGpsLoop();
  if (ok && data) {
    if (msg) msg.textContent = "Trip logged: " + data.actual_mins + " min";
  } else if (msg) {
    msg.textContent = "Could not end the route.";
  }
  await refreshSessionState();
  btn.disabled = false;
}

/* ── Pin picker (destination + checkpoint geofence) ── */
const MPIN = { map: null, marker: null, circle: null, lat: null, lng: null, onSave: null };

function makeMPinIcon() {
  // Custom teardrop pin — Leaflet's default marker images don't load from the CDN CSS.
  return L.divIcon({
    className: "pin-marker-icon",
    html: '<div style="width:22px;height:22px;border-radius:50% 50% 50% 0;transform:rotate(-45deg);background:#ef4444;border:3px solid #fff;box-shadow:0 2px 8px rgba(0,0,0,.55)"></div>',
    iconSize: [22, 22], iconAnchor: [11, 22],
  });
}

function openMPin(opts) {
  MPIN.lat = Number.isFinite(opts.lat) ? opts.lat : null;
  MPIN.lng = Number.isFinite(opts.lng) ? opts.lng : null;
  MPIN.onSave = opts.onSave;
  document.getElementById("m-pin-title").textContent = opts.title || "Set location";
  document.getElementById("m-pin-radius").value = opts.radius == null ? 150 : opts.radius;
  document.getElementById("m-pin-search").value = "";
  const aeWrap = document.getElementById("m-pin-autoend-wrap");
  aeWrap.style.display = opts.showAutoEnd ? "" : "none";
  if (opts.showAutoEnd) document.getElementById("m-pin-autoend").value = opts.autoEnd == null ? 5 : opts.autoEnd;
  // Clear any marker/circle left from a previous open.
  if (MPIN.marker && MPIN.map) { MPIN.map.removeLayer(MPIN.marker); MPIN.marker = null; }
  if (MPIN.circle && MPIN.map) { MPIN.map.removeLayer(MPIN.circle); MPIN.circle = null; }
  document.getElementById("m-pin-overlay").classList.remove("hidden");
  updateMPinCoords();
  setTimeout(initMPinMap, 120);
}

function closePin() {
  document.getElementById("m-pin-overlay").classList.add("hidden");
}

function initMPinMap() {
  const hasPt = Number.isFinite(MPIN.lat) && Number.isFinite(MPIN.lng);
  const center = hasPt ? [MPIN.lat, MPIN.lng] : [39.8283, -98.5795];
  const zoom = hasPt ? 15 : 4;
  if (!MPIN.map) {
    MPIN.map = L.map("m-pin-map", { zoomControl: true }).setView(center, zoom);
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
      { attribution: "© OpenStreetMap contributors", maxZoom: 19 }).addTo(MPIN.map);
    MPIN.map.on("click", (e) => setMPinPoint(e.latlng.lat, e.latlng.lng));
  } else {
    MPIN.map.setView(center, zoom);
    MPIN.map.invalidateSize();
  }
  if (hasPt) setMPinPoint(MPIN.lat, MPIN.lng);
}

function setMPinPoint(lat, lng) {
  MPIN.lat = lat;
  MPIN.lng = lng;
  const radius = parseFloat(document.getElementById("m-pin-radius").value) || 150;
  if (!MPIN.marker) {
    MPIN.marker = L.marker([lat, lng], { icon: makeMPinIcon(), draggable: true }).addTo(MPIN.map);
    MPIN.marker.on("dragend", () => { const p = MPIN.marker.getLatLng(); setMPinPoint(p.lat, p.lng); });
  } else {
    MPIN.marker.setLatLng([lat, lng]);
  }
  if (!MPIN.circle) {
    MPIN.circle = L.circle([lat, lng], { radius, color: "#00a3e0", weight: 2, fillColor: "#00a3e0", fillOpacity: 0.2 }).addTo(MPIN.map);
  } else {
    MPIN.circle.setLatLng([lat, lng]);
    MPIN.circle.setRadius(radius);
  }
  updateMPinCoords();
}

function onMPinRadiusInput() {
  if (MPIN.circle && Number.isFinite(MPIN.lat)) {
    MPIN.circle.setRadius(parseFloat(document.getElementById("m-pin-radius").value) || 150);
  }
}

function updateMPinCoords() {
  const el = document.getElementById("m-pin-coords");
  if (!el) return;
  el.textContent = (Number.isFinite(MPIN.lat) && Number.isFinite(MPIN.lng))
    ? `📍 Pin set at ${MPIN.lat.toFixed(6)}, ${MPIN.lng.toFixed(6)}`
    : "Tap the map to place the pin";
}

async function mPinSearch() {
  const q = document.getElementById("m-pin-search").value.trim();
  if (!q || !MPIN.map) return;
  const { ok, data } = await api("/api/geocode?q=" + encodeURIComponent(q));
  if (ok && data && data.lat !== undefined) {
    setMPinPoint(data.lat, data.lng);
    MPIN.map.setView([data.lat, data.lng], 15);
  }
}

function mPinUseLocation() {
  const el = document.getElementById("m-pin-coords");
  const insecure = gpsInsecureHint();
  if (insecure) {
    if (el) el.textContent = insecure;
    return;
  }
  if (!navigator.geolocation) {
    if (el) el.textContent = "Geolocation not available on this device";
    return;
  }
  if (el) el.textContent = "Getting GPS fix…";
  navigator.geolocation.getCurrentPosition(
    (pos) => {
      const lat = pos.coords.latitude;
      const lng = pos.coords.longitude;
      setMPinPoint(lat, lng);
      if (MPIN.map) MPIN.map.setView([lat, lng], 16);
      const acc = Number.isFinite(pos.coords.accuracy) ? ` · ±${Math.round(pos.coords.accuracy)}m` : "";
      if (el) el.textContent = `📍 Current location ${lat.toFixed(6)}, ${lng.toFixed(6)}${acc}`;
    },
    (err) => {
      if (el) el.textContent = geoErrorText(err);
    },
    { enableHighAccuracy: true, timeout: 15000, maximumAge: 5000 }
  );
}

async function mSavePin() {
  if (!Number.isFinite(MPIN.lat) || !Number.isFinite(MPIN.lng)) {
    const el = document.getElementById("m-pin-coords");
    if (el) el.textContent = "Place a pin first (map tap or 📍 Me)";
    return;
  }
  const radius = parseInt(document.getElementById("m-pin-radius").value) || 150;
  const aeWrap = document.getElementById("m-pin-autoend-wrap");
  const autoEnd = aeWrap.style.display !== "none"
    ? (parseInt(document.getElementById("m-pin-autoend").value) || 0)
    : null;
  await MPIN.onSave(MPIN.lat, MPIN.lng, radius, autoEnd);
  closePin();
}

function editDestination() {
  if (!RD.route) return;
  openMPin({
    title: "Destination — " + RD.route.name,
    lat: RD.route.dest_lat, lng: RD.route.dest_lng,
    radius: RD.route.dest_radius_m, showAutoEnd: true, autoEnd: RD.route.auto_end_minutes,
    onSave: async (lat, lng, radius, autoEnd) => {
      const { ok, data } = await api("/api/routes/" + RD.routeId, "PUT", {
        dest_lat: lat, dest_lng: lng, dest_radius_m: radius, auto_end_minutes: autoEnd,
      });
      if (ok && data) { RD.route = data; updateDestStatus(); }
    },
  });
}

function editCheckpointPin(cpId, cpName) {
  const cp = RD.checkpoints.find((c) => c.id === cpId) || {};
  openMPin({
    title: "Checkpoint — " + cpName,
    lat: cp.lat, lng: cp.lng, radius: cp.radius_m, showAutoEnd: false,
    onSave: async (lat, lng, radius) => {
      await api("/api/checkpoints/" + cpId, "PUT", { lat, lng, radius_m: radius });
      await loadCheckpoints();
    },
  });
}

/* ── Calendar (condensed upcoming list) ── */
async function loadEvents() {
  const el = document.getElementById("m-events");
  if (!el) return;
  const { data } = await api("/api/events");
  const today = new Date().toISOString().slice(0, 10);
  const upcoming = (data || [])
    .filter((e) => (e.end_date || e.event_date) >= today)
    .sort((a, b) => (a.event_date + (a.start_time || "")).localeCompare(b.event_date + (b.start_time || "")))
    .slice(0, 80);
  if (!upcoming.length) {
    el.innerHTML = '<div class="m-empty">No upcoming events</div>';
    return;
  }
  // Group by day → condensed date headers + event rows.
  const groups = {};
  upcoming.forEach((e) => { (groups[e.event_date] = groups[e.event_date] || []).push(e); });
  el.innerHTML = Object.keys(groups).sort().map((d) => `
    <div class="m-day-group">
      <div class="m-day-head">${dayLabel(d)}</div>
      ${groups[d].map((e) => `
        <div class="m-list-item" style="border-left:3px solid ${esc(e.color || "#00a3e0")}">
          <div class="m-list-main">
            <div class="m-list-title">${esc(e.title)}</div>
            <div class="m-list-sub">${e.start_time ? mFmtTime(e.start_time) : "All day"}${e.notes ? " · " + esc(e.notes) : ""}</div>
          </div>
        </div>`).join("")}
    </div>`).join("");
}

function dayLabel(dateStr) {
  const today = new Date(); today.setHours(0, 0, 0, 0);
  const p = dateStr.split("-").map(Number);
  const d = new Date(p[0], p[1] - 1, p[2]);
  const diff = Math.round((d - today) / 86400000);
  const rel = diff === 0 ? "Today" : diff === 1 ? "Tomorrow" : "";
  const nice = d.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
  return rel ? `${rel} · ${nice}` : nice;
}

function mFmtTime(hhmm) {
  if (M.fmt === "24h" || !hhmm) return hhmm;
  const [h, m] = hhmm.split(":").map(Number);
  if (isNaN(h)) return hhmm;
  const ap = h >= 12 ? "PM" : "AM";
  const hh = h % 12 || 12;
  return `${hh}:${String(m).padStart(2, "0")} ${ap}`;
}

/* ── Settings ── */
let MSET = {};

async function loadMobileSettings() {
  const { data } = await api("/api/settings");
  MSET = data || {};
  const w = document.getElementById("set-watched");
  if (w) w.value = MSET.watched_person || "";
  const s = document.getElementById("set-start");
  if (s) s.value = MSET.regular_hours_start || "09:00";
  const e = document.getElementById("set-end");
  if (e) e.value = MSET.regular_hours_end || "17:00";
  setSwitch("regular_hours_enabled", MSET.regular_hours_enabled === "true");
  setSwitch("uploads_override", MSET.uploads_override === "true");
  M.fmt = MSET.time_format || "12h";
  markFormat();
}

function setSwitch(key, on) {
  const el = document.getElementById("sw-" + key);
  if (el) el.classList.toggle("on", !!on);
}

function mToggle(key) {
  const el = document.getElementById("sw-" + key);
  if (!el) return;
  const on = !el.classList.contains("on");
  el.classList.toggle("on", on);
  MSET[key] = on ? "true" : "false";
}

function markFormat() {
  document.querySelectorAll(".m-seg-btn").forEach((b) =>
    b.classList.toggle("active", b.dataset.fmt === M.fmt));
}

function mSetFormat(f) {
  M.fmt = f;
  MSET.time_format = f;
  markFormat();
}

async function saveMobileSettings() {
  MSET.watched_person = document.getElementById("set-watched").value;
  MSET.regular_hours_start = document.getElementById("set-start").value;
  MSET.regular_hours_end = document.getElementById("set-end").value;
  const msg = document.getElementById("m-settings-msg");
  const { ok } = await api("/api/settings", "POST", MSET);
  if (msg) msg.textContent = ok ? "Saved ✓" : "Save failed";
}

/* ── Boot ── */
document.addEventListener("DOMContentLoaded", async () => {
  applyTheme();
  // Pull time format up front so event times match the desktop preference.
  const { data } = await api("/api/settings");
  if (data && data.time_format) M.fmt = data.time_format;
  showDate();

  switch (document.body.dataset.page) {
    case "home":
      loadActiveBanner();
      loadWeather();
      break;
    case "routes":
      loadActiveBanner();
      loadRoutesList();
      break;
    case "route_detail":
      initRouteDetail();
      break;
    case "calendar":
      loadEvents();
      break;
    case "settings":
      loadMobileSettings();
      break;
  }
});
