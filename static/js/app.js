/* ═══════════════════════════════════════════
   WorkBuddy JS  v6
   ═══════════════════════════════════════════ */

const ROUTES_PER_PAGE = 3;

const state = {
  calView:        'week',
  calDate:        new Date(),
  routes:         [],
  routePage:      0,
  events:         [],
  holidays:       {},   // { "YYYY-MM-DD": "Holiday Name" }
  corridors:      [],
  settings:       {},
  activeLogRoute: null,
  activeLogEta:   null,
  activeCheckpointRoute: null,
  mlbWeekStart:   null,
  uploadedFile:   null,
  map:            null,
  mapInited:      false,
  mapPoints:      [],
  mapMarkers:     [],
  mapSegments:    [],
  mapHasEnd:      false,
};

// ─────────────────────────────────────────────
//  BOOT
// ─────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  initTheme();
  startClock();
  scheduleMidnightRefresh();
  loadSettings().then(() => {
    renderCalendar();
    loadEventsForView();
  });
  loadWeather();
  loadTraffic();
  loadMLBToday();
  loadRoutes();
  loadCorridors();
  loadHolidays(new Date().getFullYear());
  loadHolidays(new Date().getFullYear() + 1);  // prefetch next year
  setupUploadZone();
  setupModalOverlayClose();
  setInterval(loadMLBToday, 15 * 60 * 1000);
  scheduleDailyWeatherRefresh();
  scheduleDailyTrafficRefresh();
});

function scheduleMidnightRefresh() {
  const now      = new Date();
  const midnight = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1, 0, 0, 5);
  setTimeout(() => location.reload(), midnight - now);
}

// ─────────────────────────────────────────────
//  CLOCK
// ─────────────────────────────────────────────
function startClock() {
  const tick = () => {
    const now = new Date();
    document.getElementById('clock').textContent =
      now.toLocaleTimeString('en-US', { hour:'2-digit', minute:'2-digit', second:'2-digit' });
    document.getElementById('dateline').textContent =
      now.toLocaleDateString('en-US', { weekday:'long', month:'long', day:'numeric', year:'numeric' });
  };
  tick();
  setInterval(tick, 1000);
}

// ─────────────────────────────────────────────
//  THEME
// ─────────────────────────────────────────────
function initTheme() {
  const saved = localStorage.getItem('wb-theme') || 'dark-blue';
  applyTheme(saved);
  document.querySelectorAll('.theme-dot').forEach(btn =>
    btn.addEventListener('click', () => applyTheme(btn.dataset.theme))
  );
}

function applyTheme(theme) {
  document.documentElement.setAttribute('data-theme', theme);
  localStorage.setItem('wb-theme', theme);
  document.querySelectorAll('.theme-dot').forEach(b => b.classList.toggle('active', b.dataset.theme === theme));
  if (state.map) state.map.invalidateSize();
}

// ─────────────────────────────────────────────
//  TIME FORMATTING
// ─────────────────────────────────────────────
function fmtTime(hhmm) {
  if (!hhmm) return '';
  if ((state.settings.time_format || '12h') === '24h') return hhmm;
  const [h, m] = hhmm.split(':').map(Number);
  const period = h >= 12 ? 'PM' : 'AM';
  const h12    = h % 12 || 12;
  return `${h12}:${String(m).padStart(2,'0')} ${period}`;
}

// ─────────────────────────────────────────────
//  SETTINGS
// ─────────────────────────────────────────────
async function loadSettings() {
  try {
    state.settings = await fetchJSON('/api/settings');
    applySettingsToUI();
  } catch { /* silent */ }
}

function applySettingsToUI() {
  const s = state.settings;

  // Regular hours toggle
  const rhToggle = document.getElementById('reg-hours-toggle');
  const rhLabel  = document.getElementById('reg-hours-label');
  const rhEnabled = s.regular_hours_enabled === 'true';
  if (rhToggle) { rhToggle.classList.toggle('on', rhEnabled); }
  if (rhLabel)  { rhLabel.textContent = rhEnabled ? 'On' : 'Off'; }

  // Uploads override toggle
  const ovToggle = document.getElementById('override-toggle');
  const ovLabel  = document.getElementById('override-label');
  const ovEnabled = s.uploads_override === 'true';
  if (ovToggle) { ovToggle.classList.toggle('on', ovEnabled); }
  if (ovLabel)  { ovLabel.textContent = ovEnabled ? 'On' : 'Off'; }

  // Hours inputs
  const rs = document.getElementById('reg-start');
  const re = document.getElementById('reg-end');
  if (rs) rs.value = s.regular_hours_start || '09:00';
  if (re) re.value = s.regular_hours_end   || '17:00';

  // Days chips
  const activeDays = (s.regular_hours_days || '1,2,3,4,5').split(',').map(Number);
  document.querySelectorAll('.day-chip').forEach(chip => {
    const dow = parseInt(chip.dataset.dow);
    chip.classList.toggle('active', activeDays.includes(dow));
  });

  // Time format buttons
  const fmt = s.time_format || '12h';
  const b12 = document.getElementById('fmt-12h');
  const b24 = document.getElementById('fmt-24h');
  if (b12) b12.classList.toggle('active', fmt === '12h');
  if (b24) b24.classList.toggle('active', fmt === '24h');

  // Watched person
  const wp = document.getElementById('watched-person-input');
  if (wp) wp.value = s.watched_person || '';
}

async function toggleSetting(key, trackEl) {
  const current = state.settings[key] === 'true';
  const newVal  = (!current).toString();
  state.settings[key] = newVal;
  trackEl.classList.toggle('on', newVal === 'true');
  // Update label
  const labelMap = { regular_hours_enabled: 'reg-hours-label', uploads_override: 'override-label' };
  const labelEl  = document.getElementById(labelMap[key]);
  if (labelEl) labelEl.textContent = newVal === 'true' ? 'On' : 'Off';
  await fetchJSON('/api/settings', 'POST', { [key]: newVal });
  renderCalendar();
  populateCalendarEvents(state.events);
}

async function saveHours() {
  const start = document.getElementById('reg-start').value;
  const end   = document.getElementById('reg-end').value;
  state.settings.regular_hours_start = start;
  state.settings.regular_hours_end   = end;
  await fetchJSON('/api/settings', 'POST', { regular_hours_start: start, regular_hours_end: end });
  populateCalendarEvents(state.events);
}

async function toggleDay(dow, chip) {
  const activeDays = (state.settings.regular_hours_days || '1,2,3,4,5').split(',').map(Number);
  const idx = activeDays.indexOf(dow);
  if (idx === -1) activeDays.push(dow); else activeDays.splice(idx, 1);
  activeDays.sort((a,b) => a - b);
  chip.classList.toggle('active', activeDays.includes(dow));
  const val = activeDays.join(',');
  state.settings.regular_hours_days = val;
  await fetchJSON('/api/settings', 'POST', { regular_hours_days: val });
  populateCalendarEvents(state.events);
}

async function setTimeFormat(fmt) {
  state.settings.time_format = fmt;
  document.getElementById('fmt-12h').classList.toggle('active', fmt === '12h');
  document.getElementById('fmt-24h').classList.toggle('active', fmt === '24h');
  await fetchJSON('/api/settings', 'POST', { time_format: fmt });
  populateCalendarEvents(state.events);
}

async function saveWatchedPerson() {
  const val = document.getElementById('watched-person-input').value.trim();
  if (!val) return;
  state.settings.watched_person = val;
  await fetchJSON('/api/settings', 'POST', { watched_person: val });
  showSchedStatus(`Now watching shifts for: ${val}`, 'ok');
}

// Work schedule config panel
function openSchedConfig() {
  document.getElementById('sched-config-overlay').classList.remove('hidden');
  applySettingsToUI();
}
function closeSchedConfig() {
  document.getElementById('sched-config-overlay').classList.add('hidden');
}

// ─────────────────────────────────────────────
//  HOLIDAYS
// ─────────────────────────────────────────────
async function loadHolidays(year) {
  try {
    const list = await fetchJSON(`/api/holidays?year=${year}`);
    list.forEach(h => { state.holidays[h.date] = h.name; });
  } catch { /* silent */ }
}

// ─────────────────────────────────────────────
//  DAILY REFRESH SCHEDULERS
// ─────────────────────────────────────────────
function scheduleDailyWeatherRefresh() {
  const now = new Date();
  const next7am = new Date(now.getFullYear(), now.getMonth(), now.getDate(), 7, 0, 0);
  if (now >= next7am) next7am.setDate(next7am.getDate() + 1);
  setTimeout(() => { loadWeather(); setInterval(loadWeather, 24*60*60*1000); }, next7am - now);
}
function scheduleDailyTrafficRefresh() {
  const now = new Date();
  const next7am = new Date(now.getFullYear(), now.getMonth(), now.getDate(), 7, 0, 0);
  if (now >= next7am) next7am.setDate(next7am.getDate() + 1);
  setTimeout(() => { loadTraffic(); setInterval(loadTraffic, 24*60*60*1000); }, next7am - now);
}

// ─────────────────────────────────────────────
//  WEATHER
// ─────────────────────────────────────────────
async function loadWeather(force = false) {
  const el = document.getElementById('weather-inline');
  try {
    const d = await fetchJSON(`/api/weather${force ? '?force=1' : ''}`);
    el.textContent = d.error ? '— add weather key —' : `${d.temp}°F · ${d.description}`;
  } catch { el.textContent = 'weather unavailable'; }
}

// ─────────────────────────────────────────────
//  TRAFFIC
// ─────────────────────────────────────────────
async function loadTraffic(force = false) {
  const body    = document.getElementById('traffic-body');
  body.innerHTML = '<div class="skeleton-block"></div>';
  const enabled = getEnabledCorridors();
  try {
    const fp   = force ? (enabled.length ? '&force=1' : '?force=1') : '';
    const ids  = enabled.length ? `?ids=${enabled.join(',')}` : '';
    const data = await fetchJSON(`/api/traffic${ids}${fp}`);
    if (!Array.isArray(data)) { body.innerHTML = noKeyNotice('TomTom','TOMTOM_API_KEY'); return; }
    const visible = data.filter(c => enabled.includes(c.id));
    if (!visible.length) {
      body.innerHTML = `<div class="no-key-notice"><span class="notice-msg">No highways selected. Open Settings ⚙.</span></div>`;
      return;
    }
    body.innerHTML = visible.map(c => `
      <div class="corridor">
        <div class="corridor-indicator indicator-${c.status}"></div>
        <div class="corridor-info">
          <div class="corridor-name">${c.name}</div>
          <div class="corridor-speed">${c.current_speed ? `${c.current_speed} mph · free flow ${c.free_flow} mph` : '—'}</div>
        </div>
        <div class="corridor-badge status-${c.status}">${c.status}</div>
      </div>`).join('');
    const ts = document.getElementById('traffic-last-updated');
    if (ts) ts.textContent = `Updated ${new Date().toLocaleTimeString('en-US',{hour:'2-digit',minute:'2-digit'})}`;
  } catch (e) {
    body.innerHTML = `<div class="no-key-notice"><span class="notice-title">Error</span><span class="notice-msg">${e.message}</span></div>`;
  }
}

async function loadCorridors() {
  try { state.corridors = await fetchJSON('/api/corridors'); buildHighwayToggles(); } catch { /* silent */ }
}
function getEnabledCorridors() {
  const saved = localStorage.getItem('wb-corridors');
  if (saved) return JSON.parse(saved);
  return state.corridors.filter(c => c.default).map(c => c.id);
}
function buildHighwayToggles() {
  const container = document.getElementById('highway-toggles');
  if (!container) return;
  const enabled = getEnabledCorridors();
  container.innerHTML = state.corridors.map(c => `
    <div class="toggle-row" onclick="toggleCorridor('${c.id}')">
      <span class="toggle-label">${c.name}</span>
      <div class="toggle-switch ${enabled.includes(c.id)?'on':''}" id="toggle-${c.id}"></div>
    </div>`).join('');
}
function toggleCorridor(id) {
  const sw = document.getElementById(`toggle-${id}`);
  const enabled = getEnabledCorridors();
  const idx = enabled.indexOf(id);
  if (idx === -1) enabled.push(id); else enabled.splice(idx, 1);
  localStorage.setItem('wb-corridors', JSON.stringify(enabled));
  sw.classList.toggle('on', enabled.includes(id));
  loadTraffic(true);
}

// ─────────────────────────────────────────────
//  MLB
// ─────────────────────────────────────────────
async function loadMLBToday() {
  try {
    const d = await fetchJSON('/api/mlb/today');
    if (d.featured_game) {
      const g = d.featured_game;
      const t = fmtTime(new Date(g.time).toLocaleTimeString('en-US',{hour:'2-digit',minute:'2-digit',hour12:false}));
      document.getElementById('vice-pill').classList.remove('hidden');
      document.getElementById('vice-game-status').textContent = `${g.away_team} @ ${g.home_team} · ${t}`;
    }
  } catch { /* silent */ }
}

function openMLBFloat() {
  state.mlbWeekStart = new Date(); state.mlbWeekStart.setHours(0,0,0,0);
  document.getElementById('mlb-float').classList.remove('hidden');
  fetchMLBWeek(); setupFloatDrag();
}
function closeMLBFloat() { document.getElementById('mlb-float').classList.add('hidden'); }
function shiftMLBWeek(days) {
  state.mlbWeekStart = new Date(state.mlbWeekStart.getTime() + days*86400000);
  fetchMLBWeek();
}
async function fetchMLBWeek() {
  const body = document.getElementById('mlb-float-body');
  body.innerHTML = '<div class="skeleton-block" style="margin:10px"></div>';
  const startStr = state.mlbWeekStart.toISOString().slice(0,10);
  try {
    const week = await fetchJSON(`/api/mlb/week?start=${startStr}`);
    if (!Array.isArray(week)||!week.length) {
      body.innerHTML = `<div style="padding:16px;color:var(--text-lo);font-size:12px">No games for this period.</div>`;
      return;
    }
    body.innerHTML = week.map(day => {
      if (!day.games?.length) return '';
      const label = new Date(day.date+'T12:00:00').toLocaleDateString('en-US',{weekday:'long',month:'short',day:'numeric'});
      const rows  = day.games.map(g => `
        <div class="float-game-row ${g.is_featured?'vice-row':''}">
          <div class="float-game-time">${formatGameTime(g.time)}</div>
          <div class="float-game-teams">${g.away_team}<span class="vs">@</span>${g.home_team}</div>
          ${g.is_featured?'<span class="vice-tag">VICE</span>':''}
        </div>`).join('');
      return `<div class="float-day-group"><div class="float-day-label">${label}</div>${rows}</div>`;
    }).filter(Boolean).join('');
  } catch (e) {
    body.innerHTML = `<div style="padding:16px;color:var(--text-lo);font-size:12px">Error: ${e.message}</div>`;
  }
}
function formatGameTime(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  const raw = d.toLocaleTimeString('en-US',{hour:'2-digit',minute:'2-digit',hour12:false});
  return fmtTime(raw);
}
let _floatDragSetup = false;
function setupFloatDrag() {
  if (_floatDragSetup) return; _floatDragSetup = true;
  const panel = document.getElementById('mlb-float');
  const handle = document.getElementById('mlb-float-drag');
  let dragging=false,startX,startY,origRight,origBottom;
  handle.addEventListener('mousedown',e=>{
    dragging=true;startX=e.clientX;startY=e.clientY;
    const rect=panel.getBoundingClientRect();
    origRight=window.innerWidth-rect.right;origBottom=window.innerHeight-rect.bottom;
    panel.style.transition='none';e.preventDefault();
  });
  document.addEventListener('mousemove',e=>{
    if(!dragging)return;
    panel.style.right=Math.max(0,origRight-(e.clientX-startX))+'px';
    panel.style.bottom=Math.max(0,origBottom-(e.clientY-startY))+'px';
  });
  document.addEventListener('mouseup',()=>{dragging=false;});
}

// ─────────────────────────────────────────────
//  SCHEDULE UPLOAD
// ─────────────────────────────────────────────
function setupUploadZone() {
  const zone = document.getElementById('upload-zone');
  if (!zone) return;
  zone.addEventListener('dragover',  e=>{e.preventDefault();zone.classList.add('drag-over');});
  zone.addEventListener('dragleave', ()=>zone.classList.remove('drag-over'));
  zone.addEventListener('drop', e=>{
    e.preventDefault();zone.classList.remove('drag-over');
    const file=e.dataTransfer.files[0]; if(file) handleUploadFile(file);
  });
}
function handleUpload(e) { if(e.target.files[0]) handleUploadFile(e.target.files[0]); }

async function handleUploadFile(file) {
  showSchedStatus(`Uploading ${file.name}…`, 'info');
  const form = new FormData(); form.append('file', file);
  try {
    const r = await fetch('/api/schedule/upload',{method:'POST',body:form});
    const d = await r.json();
    if (d.status==='received') {
      state.uploadedFile = d.file;
      const fn = document.getElementById('upload-filename');
      if (fn) fn.textContent = `📄 ${d.file}`;
      showSchedStatus('File ready. Click Preview or Import.','ok');
      document.getElementById('preview-btn').disabled = false;
      document.getElementById('import-btn').disabled  = false;
      document.getElementById('schedule-preview').classList.add('hidden');
    } else {
      showSchedStatus(d.error||'Upload failed.','err');
    }
  } catch { showSchedStatus('Upload failed.','err'); }
}

async function previewSchedule() {
  if (!state.uploadedFile) return;
  showSchedStatus('Fetching preview…','info');
  try {
    const d = await fetchJSON('/api/schedule/parse','POST',{filename:state.uploadedFile,preview:true});
    if (d.error) { showSchedStatus(`Error: ${d.error}`,'err'); return; }
    const box = document.getElementById('schedule-preview');
    box.classList.remove('hidden');
    const notes = [];
    if (d.skipped_past > 0)  notes.push(`${d.skipped_past} past dates skipped`);
    if (d.skipped_exc  > 0)  notes.push(`${d.skipped_exc} exception rows excluded`);
    const noteTxt = notes.length ? ` (${notes.join(', ')})` : '';
    if (!d.preview?.length) {
      box.innerHTML = `<span style="color:var(--text-lo)">No upcoming shifts found for "${d.watched}".${noteTxt}</span>`;
      showSchedStatus(`0 upcoming events for "${d.watched}".${noteTxt}`,'info');
      return;
    }
    showSchedStatus(`${d.count} upcoming events for "${d.watched}"${noteTxt}`, 'ok');
    box.innerHTML = d.preview.map(ev=>
      `<div>${ev.event_date} ${fmtTime(ev.start_time)} &nbsp;<strong>${ev.title}</strong>${ev.notes?' · '+ev.notes:''}</div>`
    ).join('');
  } catch(e) { showSchedStatus(`Error: ${e.message}`,'err'); }
}

async function importSchedule() {
  if (!state.uploadedFile) return;
  showSchedStatus('Importing…','info');
  try {
    const d = await fetchJSON('/api/schedule/parse','POST',{filename:state.uploadedFile,preview:false});
    if (d.error) { showSchedStatus(`Error: ${d.error}`,'err'); return; }
    const parts = [`✅ Imported ${d.inserted} events for "${d.watched}"`];
    if (d.skipped_existing) parts.push(`${d.skipped_existing} already existed`);
    if (d.skipped_past)     parts.push(`${d.skipped_past} past dates skipped`);
    if (d.skipped_exc)      parts.push(`${d.skipped_exc} exceptions excluded`);
    showSchedStatus(parts.join(' · '),'ok');
    document.getElementById('schedule-preview').classList.add('hidden');
    loadEventsForView();
  } catch(e) { showSchedStatus(`Error: ${e.message}`,'err'); }
}

function showSchedStatus(msg, type='info') {
  const el = document.getElementById('schedule-status');
  if (!el) return;
  el.textContent = msg;
  el.className   = `sched-status ${type}`;
}

// ─────────────────────────────────────────────
//  ROUTES
// ─────────────────────────────────────────────
async function loadRoutes() {
  const body = document.getElementById('routes-body');
  body.innerHTML = '<div class="skeleton-block"></div>';
  try {
    state.routes    = await fetchJSON('/api/routes');
    state.routePage = 0;
    renderRouteCarousel();
  } catch { body.innerHTML = `<div class="no-key-notice"><span class="notice-title">Error loading routes</span></div>`; }
}
function routesPage(dir) {
  const max = Math.max(0, Math.ceil(state.routes.length/ROUTES_PER_PAGE)-1);
  state.routePage = Math.max(0, Math.min(max, state.routePage+dir));
  renderRouteCarousel();
}
function renderRouteCarousel() {
  const body  = document.getElementById('routes-body');
  const total = state.routes.length;
  const max   = Math.max(0, Math.ceil(total/ROUTES_PER_PAGE)-1);
  const prev  = document.getElementById('routes-prev');
  const next  = document.getElementById('routes-next');
  const ind   = document.getElementById('routes-page-indicator');
  if (prev) prev.disabled = state.routePage===0;
  if (next) next.disabled = state.routePage>=max;
  if (ind)  ind.textContent = total>0 ? `${state.routePage+1}/${max+1}` : '';
  if (!total) {
    body.innerHTML = `<div class="no-key-notice"><span class="notice-msg">No routes yet. Hit + to draw one.</span></div>`;
    return;
  }
  const slice = state.routes.slice(state.routePage*ROUTES_PER_PAGE, (state.routePage+1)*ROUTES_PER_PAGE);
  body.innerHTML = slice.map(r => {
    let desc = `<div class="route-addresses">↑ ${r.origin}<br>↓ ${r.destination}</div>`;
    try {
      const wp = JSON.parse(r.waypoints||'{}');
      if (wp.type==='map'&&wp.points?.length) {
        const s=wp.points.find(p=>p.type==='start'), e=wp.points.find(p=>p.type==='end');
        const stops=wp.points.filter(p=>p.type==='middle');
        desc=`<div class="route-addresses">📍 ${s?`${s.lat.toFixed(4)},${s.lng.toFixed(4)}`:'—'}<br>${stops.length?`📌 ${stops.length} turn${stops.length>1?'s':''}<br>`:''}🏁 ${e?`${e.lat.toFixed(4)},${e.lng.toFixed(4)}`:'—'}</div>`;
      }
    } catch { /* use fallback */ }
    return `<div class="route-item" id="route-${r.id}">
      <div class="route-name">${r.name}</div>
      ${desc}
      <div class="route-eta-result" id="eta-${r.id}"></div>
      <div class="route-actions">
        <button class="btn-sm primary" onclick="getETA(${r.id})">📍 ETA</button>
        <button class="btn-sm" onclick="openLogModal(${r.id},'${escHtml(r.name)}')">📋 Log</button>
        <button class="btn-sm" onclick="openCheckpoints(${r.id},'${escHtml(r.name)}')">📌 Checkpoints</button>
        <button class="btn-sm" onclick="openHistory(${r.id},'${escHtml(r.name)}')">📊 History</button>
        <button class="btn-sm danger" onclick="deleteRoute(${r.id})">✕</button>
      </div>
    </div>`;
  }).join('');
}
async function deleteRoute(id) {
  if (!confirm('Delete this route and all its logs?')) return;
  await fetchJSON(`/api/routes/${id}`,'DELETE');
  loadRoutes();
}
async function getETA(routeId) {
  const el = document.getElementById(`eta-${routeId}`);
  el.textContent = 'Fetching ETA…';
  try {
    const d = await fetchJSON(`/api/routes/${routeId}/eta`);
    if (d.error==='no_key') { el.textContent='⚠ Add TOMTOM_API_KEY to .env'; return; }
    el.textContent = `🕒 ${d.travel_mins} min  (+${d.delay_mins} min traffic)  ${d.length_km} km`;
    state.activeLogEta = d.travel_mins;
  } catch(e) { el.textContent=`Error: ${e.message}`; }
}
function openLogModal(routeId, routeName) {
  state.activeLogRoute = routeId;
  document.getElementById('log-route-name').textContent = routeName;
  document.getElementById('log-actual').value = '';
  document.getElementById('log-notes').value  = '';
  document.getElementById('log-date').value   = new Date().toISOString().slice(0,10);
  const eta = document.getElementById(`eta-${routeId}`)?.textContent?.trim();
  document.getElementById('log-eta-display').textContent = eta||'Run "ETA" first to auto-fill estimated time.';
  const cpSection = document.getElementById('log-checkpoints-section');
  if (cpSection) cpSection.innerHTML = '<div style="color:var(--text-lo);font-size:11px">Loading…</div>';
  openModal('log-modal');
  fetchJSON(`/api/routes/${routeId}/checkpoints`).then(cps => {
    if (!cpSection) return;
    if (!cps.length) {
      cpSection.innerHTML = '<div style="color:var(--text-lo);font-size:11px;font-style:italic">No checkpoints — add them via 📌 Checkpoints on the route card.</div>';
      return;
    }
    cpSection.innerHTML = '<div class="cp-log-header">Checkpoint Times <span style="color:var(--text-lo);font-weight:400;font-size:10px">(all optional — leave blank to skip)</span></div>'
      + cps.map(cp =>
        `<div class="cp-log-row"><div class="cp-log-name"><span class="cp-position">${cp.position}</span>${cp.name}</div><div class="cp-log-input-wrap"><input type="number" class="cp-log-input" id="cp-time-${cp.id}" placeholder="—" min="0" step="0.5" data-cp-id="${cp.id}"><span class="cp-log-unit">min from start</span></div></div>`
      ).join('');
  }).catch(() => { if (cpSection) cpSection.innerHTML = ''; });
}
async function saveLog() {
  const actual = parseFloat(document.getElementById('log-actual').value);
  if (!actual||actual<=0) return alert('Enter your actual travel time in minutes.');
  const checkpoint_times = [];
  document.querySelectorAll('.cp-log-input').forEach(input => {
    const val = parseFloat(input.value);
    if (!isNaN(val) && val >= 0)
      checkpoint_times.push({ checkpoint_id: parseInt(input.dataset.cpId), minutes_from_start: val });
  });
  await fetchJSON(`/api/routes/${state.activeLogRoute}/logs`, 'POST', {
    actual_mins:    actual,
    log_date:       document.getElementById('log-date').value,
    notes:          document.getElementById('log-notes').value.trim(),
    estimated_mins: state.activeLogEta ?? null,
    checkpoint_times,
  });
  closeModal('log-modal'); state.activeLogEta = null;
}
async function openHistory(routeId, routeName) {
  document.getElementById('history-route-name').textContent = routeName;
  const body = document.getElementById('history-body');
  body.innerHTML='<div class="skeleton-block"></div>';
  openModal('history-modal');
  try {
    const [logs, cps] = await Promise.all([
      fetchJSON(`/api/routes/${routeId}/logs`),
      fetchJSON(`/api/routes/${routeId}/checkpoints`),
    ]);
    if (!logs.length) { body.innerHTML=`<div class="day-empty">No logs yet.</div>`; return; }

    const rows = logs.map(l => {
      const diff    = l.estimated_mins!=null?(l.actual_mins-l.estimated_mins).toFixed(1):'—';
      const diffCls = diff!=='—'?(parseFloat(diff)>0?'diff-positive':'diff-negative'):'';
      const diffStr = diff!=='—'?(parseFloat(diff)>0?`+${diff}`:diff)+' min':'—';

      // Build checkpoint breakdown if any were logged
      let cpRows = '';
      if (l.checkpoint_times?.length && cps.length) {
        const sorted = [...l.checkpoint_times].sort((a,b)=>a.position-b.position);
        let prevMin  = 0;
        cpRows = `<tr><td colspan="5" class="cp-history-block">
          <div class="cp-history-title">Checkpoint breakdown:</div>
          ${sorted.map((ct, i) => {
            const segMin  = (ct.minutes_from_start - prevMin).toFixed(1);
            const out     = `<div class="cp-history-row">
              <span class="cp-position">${ct.position}</span>
              <span class="cp-history-name">${ct.checkpoint_name}</span>
              <span class="cp-history-time">${ct.minutes_from_start} min from start</span>
              <span class="cp-history-seg">(+${segMin} min)</span>
            </div>`;
            prevMin = ct.minutes_from_start;
            return out;
          }).join('')}
          <div class="cp-history-row">
            <span class="cp-position">🏁</span>
            <span class="cp-history-name">End</span>
            <span class="cp-history-time">${l.actual_mins} min total</span>
            <span class="cp-history-seg">(+${(l.actual_mins - prevMin).toFixed(1)} min)</span>
          </div>
        </td></tr>`;
      }

      return `<tr>
        <td>${l.log_date}</td>
        <td>${l.estimated_mins!=null?l.estimated_mins+' min':'—'}</td>
        <td>${l.actual_mins} min</td>
        <td class="${diffCls}">${diffStr}</td>
        <td style="color:var(--text-lo)">${l.notes||'—'}</td>
      </tr>${cpRows}`;
    }).join('');

    body.innerHTML=`<table class="history-table">
      <thead><tr><th>Date</th><th>Estimated</th><th>Actual</th><th>Diff</th><th>Notes</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
  } catch { body.innerHTML=`<div class="day-empty">Error loading history.</div>`; }
}

// ─────────────────────────────────────────────
//  CHECKPOINT MANAGEMENT
// ─────────────────────────────────────────────
async function openCheckpoints(routeId, routeName) {
  state.activeCheckpointRoute = routeId;
  document.getElementById('cp-route-name').textContent = routeName;
  openModal('checkpoints-modal');
  loadCheckpointList(routeId);
}

async function loadCheckpointList(routeId) {
  const body = document.getElementById('cp-list');
  if (!body) return;
  try {
    const cps = await fetchJSON(`/api/routes/${routeId}/checkpoints`);
    if (!cps.length) {
      body.innerHTML = `<div class="day-empty" style="padding:14px">No checkpoints yet. Add your first stop below.</div>`;
      return;
    }
    body.innerHTML = cps.map(cp => `
      <div class="cp-item" id="cp-item-${cp.id}">
        <span class="cp-position">${cp.position}</span>
        <span class="cp-item-name" id="cp-name-${cp.id}">${cp.name}</span>
        <div class="cp-item-actions">
          <button class="btn-sm" onclick="renameCheckpoint(${cp.id})">✎</button>
          <button class="btn-sm danger" onclick="deleteCheckpoint(${cp.id}, ${routeId})">✕</button>
        </div>
      </div>`).join('');
  } catch { body.innerHTML = `<div class="day-empty" style="padding:14px">Error loading checkpoints.</div>`; }
}

async function addCheckpoint() {
  const input = document.getElementById('cp-new-name');
  const name  = input.value.trim();
  if (!name) return;
  await fetchJSON(`/api/routes/${state.activeCheckpointRoute}/checkpoints`, 'POST', { name });
  input.value = '';
  loadCheckpointList(state.activeCheckpointRoute);
}

async function renameCheckpoint(cpId) {
  const nameEl = document.getElementById(`cp-name-${cpId}`);
  const newName = prompt('Rename checkpoint:', nameEl.textContent);
  if (!newName || !newName.trim()) return;
  await fetchJSON(`/api/checkpoints/${cpId}`, 'PUT', { name: newName.trim() });
  loadCheckpointList(state.activeCheckpointRoute);
}

async function deleteCheckpoint(cpId, routeId) {
  if (!confirm('Delete this checkpoint? Its logged times will also be removed.')) return;
  await fetchJSON(`/api/checkpoints/${cpId}`, 'DELETE');
  loadCheckpointList(routeId);
}

// ─────────────────────────────────────────────
//  CALENDAR
// ─────────────────────────────────────────────
function setCalView(v) {
  state.calView = v;
  document.getElementById('btn-week').classList.toggle('active', v==='week');
  document.getElementById('btn-day').classList.toggle('active',  v==='day');
  renderCalendar(); loadEventsForView();
}
function calNav(dir) {
  const d = state.calDate;
  state.calDate = state.calView==='week'
    ? new Date(d.getFullYear(), d.getMonth(), d.getDate()+dir*7)
    : new Date(d.getFullYear(), d.getMonth(), d.getDate()+dir);
  renderCalendar(); loadEventsForView();
}
function calToday() { state.calDate=new Date(); renderCalendar(); loadEventsForView(); }

function renderCalendar() {
  const body  = document.getElementById('calendar-body');
  const d     = state.calDate;
  const today = new Date(); today.setHours(0,0,0,0);

  if (state.calView==='week') {
    const dow  = d.getDay();
    const diff = dow===0?-6:1-dow;
    const mon  = new Date(d.getFullYear(), d.getMonth(), d.getDate()+diff);
    const sun  = new Date(mon.getFullYear(), mon.getMonth(), mon.getDate()+6);
    document.getElementById('cal-label').textContent =
      `${mon.toLocaleDateString('en-US',{month:'short',day:'numeric'})} – ${sun.toLocaleDateString('en-US',{month:'short',day:'numeric',year:'numeric'})}`;
    const DAYS = ['Mon','Tue','Wed','Thu','Fri','Sat','Sun'];
    body.innerHTML = `<div class="cal-week">${DAYS.map((name,i)=>{
      const day = new Date(mon.getFullYear(), mon.getMonth(), mon.getDate()+i);
      day.setHours(0,0,0,0);
      const dateStr   = day.toISOString().slice(0,10);
      const isToday   = day.getTime()===today.getTime();
      const holidayName = state.holidays[dateStr];
      const isHoliday   = !!holidayName;
      return `<div class="cal-day-col ${isToday?'today':''} ${isHoliday?'holiday':''}" onclick="openDayClick('${dateStr}')" data-date="${dateStr}">
        <div class="cal-day-header">
          <div class="cal-day-name">${name}</div>
          <div class="cal-day-num">${day.getDate()}</div>
          ${isHoliday?`<div class="holiday-name">${holidayName}</div>`:''}
        </div>
        <div class="cal-events" id="events-${dateStr}"></div>
      </div>`;
    }).join('')}</div>`;
  } else {
    const copy = new Date(d); copy.setHours(0,0,0,0);
    document.getElementById('cal-label').textContent =
      copy.toLocaleDateString('en-US',{weekday:'long',month:'long',day:'numeric',year:'numeric'});
    body.innerHTML=`<div class="cal-day-view" id="day-view-body"><div class="day-empty">Loading…</div></div>`;
  }
}

async function loadEventsForView() {
  const d     = state.calDate;
  const month = `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}`;
  try {
    const prev = fetchJSON(`/api/events?month=${prevMonth(month)}`);
    const curr = fetchJSON(`/api/events?month=${month}`);
    const next = fetchJSON(`/api/events?month=${nextMonth(month)}`);
    const all  = (await Promise.all([prev,curr,next])).flat();
    const seen = new Set();
    state.events = all.filter(e=>{ if(seen.has(e.id))return false; seen.add(e.id); return true; });
    populateCalendarEvents(state.events);
  } catch { /* silent */ }
}
function prevMonth(ym) { const [y,m]=ym.split('-').map(Number); return m===1?`${y-1}-12`:`${y}-${String(m-1).padStart(2,'0')}`; }
function nextMonth(ym) { const [y,m]=ym.split('-').map(Number); return m===12?`${y+1}-01`:`${y}-${String(m+1).padStart(2,'0')}`; }

function populateCalendarEvents(events) {
  const s          = state.settings;
  const regEnabled = s.regular_hours_enabled === 'true';
  const override   = s.uploads_override === 'true';
  const regStart   = s.regular_hours_start || '09:00';
  const regEnd     = s.regular_hours_end   || '17:00';
  const regDays    = (s.regular_hours_days || '1,2,3,4,5').split(',').map(Number);

  if (state.calView==='week') {
    document.querySelectorAll('.cal-events').forEach(el=>el.innerHTML='');

    // Figure out which dates have a real game/work event (for override check)
    const datesWithRealEvents = new Set();
    if (override) {
      events.forEach(ev => {
        if (ev.event_type==='work'||ev.event_type==='game') {
          // Expand date range
          let cur = new Date(ev.event_date+'T00:00:00');
          const end = new Date((ev.end_date||ev.event_date)+'T00:00:00');
          while (cur <= end) {
            datesWithRealEvents.add(cur.toISOString().slice(0,10));
            cur.setDate(cur.getDate()+1);
          }
        }
      });
    }

    // Draw real events
    events.forEach(ev => {
      document.querySelectorAll('.cal-day-col').forEach(col => {
        const colDate = col.dataset.date;
        if (!colDate) return;
        const startDate = ev.event_date;
        const endDate   = ev.end_date || ev.event_date;
        if (colDate < startDate || colDate > endDate) return;
        const container = document.getElementById(`events-${colDate}`);
        if (!container) return;
        const isMulti  = endDate !== startDate;
        const isStart  = colDate === startDate;
        const chip     = document.createElement('div');
        chip.className = 'cal-event';
        chip.style.cssText = `background:${ev.color}28;color:${ev.color};border-left:${isStart||!isMulti?'3px solid '+ev.color:'none'};`;
        const timeLine  = ev.start_time ? fmtTime(ev.start_time) : (isMulti && !isStart ? '↔ cont.' : 'All day');
        const titleLine = isStart ? ev.title : `↔ ${ev.title}`;
        chip.innerHTML  = `<div class="cal-event-time">${timeLine}</div><div class="cal-event-title">${titleLine}</div>`;
        chip.title      = `${timeLine} — ${ev.title}`;
        chip.addEventListener('click', e => { e.stopPropagation(); openEventDetail(ev); });
        container.appendChild(chip);
      });
    });

    // Draw virtual regular hours chips
    if (regEnabled) {
      document.querySelectorAll('.cal-day-col').forEach(col => {
        const colDate = col.dataset.date;
        if (!colDate) return;
        const dayOfWeek = new Date(colDate+'T12:00:00').getDay();
        const isoDow    = dayOfWeek === 0 ? 7 : dayOfWeek;
        if (!regDays.includes(isoDow)) return;
        if (colDate < new Date().toISOString().slice(0,10)) return;
        if (override && datesWithRealEvents.has(colDate)) return;
        const container = document.getElementById(`events-${colDate}`);
        if (!container) return;
        const chip      = document.createElement('div');
        chip.className  = 'cal-event reg-hours';
        chip.innerHTML  = `<div class="cal-event-time">${fmtTime(regStart)} – ${fmtTime(regEnd)}</div><div class="cal-event-title">Work</div>`;
        chip.title      = `Regular hours: ${fmtTime(regStart)} – ${fmtTime(regEnd)}`;
        chip.addEventListener('click', e => e.stopPropagation());
        container.appendChild(chip);
      });
    }

  } else {
    // Day view
    const dateStr   = state.calDate.toISOString().slice(0,10);
    const dayEvents = events.filter(e => {
      const start = e.event_date;
      const end   = e.end_date || e.event_date;
      return dateStr >= start && dateStr <= end;
    });

    const hasGameEvent = dayEvents.some(e=>e.event_type==='work'||e.event_type==='game');
    const isoDow = (()=>{ const d=new Date(dateStr+'T12:00:00').getDay(); return d===0?7:d; })();
    const showReg = regEnabled && regDays.includes(isoDow) &&
                    dateStr >= new Date().toISOString().slice(0,10) &&
                    !(override && hasGameEvent);

    const viewBody = document.getElementById('day-view-body');
    if (!viewBody) return;

    let rows = '';
    if (showReg) {
      rows += `<div class="day-event-row" style="cursor:default">
        <div class="day-event-time">${fmtTime(regStart)} – ${fmtTime(regEnd)}</div>
        <div class="day-event-dot" style="background:var(--reg-hours)"></div>
        <div><div class="day-event-title" style="color:var(--reg-hours);font-style:italic">Regular Hours</div></div>
      </div>`;
    }
    rows += dayEvents.map(ev=>`
      <div class="day-event-row" onclick="openEventDetail(${JSON.stringify(ev).replace(/"/g,'&quot;')})">
        <div class="day-event-time">${ev.start_time?fmtTime(ev.start_time):ev.end_time?fmtTime(ev.end_time):'All day'}</div>
        <div class="day-event-dot" style="background:${ev.color}"></div>
        <div>
          <div class="day-event-title">${ev.title}</div>
          <div class="day-event-type">${ev.event_type}${ev.notes?' · '+ev.notes:''}</div>
        </div>
        <span style="margin-left:auto;font-size:11px;color:var(--text-lo)">✎</span>
      </div>`).join('');

    viewBody.innerHTML = rows || `<div class="day-empty">No events today. Click a day in week view to add one.</div>`;
  }
}

function toggleMultiday(prefix) {
  const cb = document.getElementById(`${prefix}-multiday`); if(!cb)return;
  if (prefix==='add') {
    document.getElementById('add-dates-single').classList.toggle('hidden',cb.checked);
    document.getElementById('add-dates-range').classList.toggle('hidden',!cb.checked);
  } else {
    document.getElementById('detail-dates-single').classList.toggle('hidden',cb.checked);
    document.getElementById('detail-dates-range').classList.toggle('hidden',!cb.checked);
  }
}

function openDayClick(dateStr) {
  document.getElementById('event-modal-date').textContent = dateStr;
  ['event-title','event-start','event-end','event-notes'].forEach(id=>{ const el=document.getElementById(id); if(el) el.value=''; });
  document.getElementById('event-type').value = 'personal';
  document.getElementById('add-multiday').checked = false;
  document.getElementById('add-single-date').value  = dateStr;
  document.getElementById('add-start-date').value   = dateStr;
  document.getElementById('add-end-date').value     = dateStr;
  document.getElementById('add-dates-single').classList.remove('hidden');
  document.getElementById('add-dates-range').classList.add('hidden');
  document.getElementById('event-modal').dataset.date = dateStr;
  openModal('event-modal');
}

async function saveEvent() {
  const title   = document.getElementById('event-title').value.trim();
  const isMulti = document.getElementById('add-multiday').checked;
  if (!title) return alert('Event title is required.');
  const eventDate = isMulti ? document.getElementById('add-start-date').value : document.getElementById('add-single-date').value;
  const endDate   = isMulti ? document.getElementById('add-end-date').value   : eventDate;
  if (!eventDate) return alert('Please select a date.');
  if (isMulti && endDate < eventDate) return alert('End day must be on or after start day.');
  const type     = document.getElementById('event-type').value;
  const colorMap = {personal:'#00a3e0',work:'#ff6b35',game:'#22c55e',other:'#8a99b5'};
  await fetchJSON('/api/events','POST',{
    title, event_date:eventDate, end_date:endDate,
    start_time:document.getElementById('event-start').value,
    end_time:document.getElementById('event-end').value,
    event_type:type, color:colorMap[type]??'#00a3e0',
    notes:document.getElementById('event-notes').value.trim(),
  });
  closeModal('event-modal'); loadEventsForView();
}

// ─────────────────────────────────────────────
//  EVENT DETAIL / EDIT / DELETE
// ─────────────────────────────────────────────
function openEventDetail(ev) {
  if (typeof ev==='string') { try{ev=JSON.parse(ev);}catch{return;} }
  const isMulti = ev.end_date && ev.end_date!==ev.event_date;
  document.getElementById('detail-event-id').value = ev.id;
  document.getElementById('detail-title').value    = ev.title||'';
  document.getElementById('detail-type').value     = ev.event_type||'personal';
  document.getElementById('detail-start').value    = ev.start_time||'';
  document.getElementById('detail-end').value      = ev.end_time||'';
  document.getElementById('detail-notes').value    = ev.notes||'';
  document.getElementById('detail-multiday').checked = isMulti;
  if (isMulti) {
    document.getElementById('detail-dates-single').classList.add('hidden');
    document.getElementById('detail-dates-range').classList.remove('hidden');
    document.getElementById('detail-start-date').value = ev.event_date||'';
    document.getElementById('detail-end-date').value   = ev.end_date||'';
  } else {
    document.getElementById('detail-dates-single').classList.remove('hidden');
    document.getElementById('detail-dates-range').classList.add('hidden');
    document.getElementById('detail-date').value = ev.event_date||'';
  }
  openModal('event-detail-modal');
}
async function saveEventEdit() {
  const id      = document.getElementById('detail-event-id').value;
  const title   = document.getElementById('detail-title').value.trim();
  const isMulti = document.getElementById('detail-multiday').checked;
  if (!title) return alert('Title is required.');
  const eventDate = isMulti ? document.getElementById('detail-start-date').value : document.getElementById('detail-date').value;
  const endDate   = isMulti ? document.getElementById('detail-end-date').value   : eventDate;
  if (isMulti && endDate < eventDate) return alert('End day must be on or after start day.');
  const type     = document.getElementById('detail-type').value;
  const colorMap = {personal:'#00a3e0',work:'#ff6b35',game:'#22c55e',other:'#8a99b5'};
  try {
    await fetchJSON(`/api/events/${id}`,'PUT',{
      title, event_date:eventDate, end_date:endDate,
      start_time:document.getElementById('detail-start').value,
      end_time:document.getElementById('detail-end').value,
      event_type:type, color:colorMap[type]??'#00a3e0',
      notes:document.getElementById('detail-notes').value.trim(),
    });
    closeModal('event-detail-modal'); loadEventsForView();
  } catch(e) { alert(`Save failed: ${e.message}`); }
}
async function deleteEventFromDetail() {
  const id = document.getElementById('detail-event-id').value;
  if (!confirm('Delete this event?')) return;
  try {
    await fetchJSON(`/api/events/${id}`,'DELETE');
    closeModal('event-detail-modal'); loadEventsForView();
  } catch(e) { alert(`Delete failed: ${e.message}`); }
}

// ─────────────────────────────────────────────
//  MAP ROUTE BUILDER
// ─────────────────────────────────────────────
const MARKER_COLORS = {start:'#22c55e',middle:'#00a3e0',end:'#ef4444'};
function openMapBuilder() {
  openModal('map-modal');
  if (!state.mapInited) { setTimeout(initLeafletMap,150); }
  else { state.map.invalidateSize(); mapClear(); }
  document.getElementById('map-route-name').value='';
  updateInstructionStep(1);
}
function closeMapBuilder() { closeModal('map-modal'); }
function initLeafletMap() {
  state.mapInited=true;
  const lat = parseFloat(document.body.dataset.mapLat);
  const lng = parseFloat(document.body.dataset.mapLng);
  const hasCoords = Number.isFinite(lat) && Number.isFinite(lng);
  const center = hasCoords ? [lat, lng] : [39.8283, -98.5795];
  const zoom = hasCoords ? 12 : 4;
  state.map=L.map('leaflet-map',{zoomControl:true}).setView(center, zoom);
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',{attribution:'© OpenStreetMap contributors',maxZoom:19}).addTo(state.map);
  state.map.on('click',onMapClick);
  state.map.on('contextmenu',e=>L.DomEvent.preventDefault(e));
}
function makeMarkerIcon(type) {
  const c=MARKER_COLORS[type]||'#00a3e0';
  const labels={start:'S',middle:'·',end:'E'};
  return L.divIcon({className:'',html:`<div style="width:18px;height:18px;border-radius:50%;background:${c};border:2.5px solid #fff;box-shadow:0 1px 6px rgba(0,0,0,.5);display:flex;align-items:center;justify-content:center;font-size:9px;font-weight:700;color:#fff;font-family:sans-serif">${labels[type]||'·'}</div>`,iconSize:[18,18],iconAnchor:[9,9]});
}
async function onMapClick(e) {
  if (state.mapHasEnd) return;
  const {lat,lng}=e.latlng;
  const type=state.mapPoints.length===0?'start':'middle';
  const marker=L.marker([lat,lng],{icon:makeMarkerIcon(type),draggable:true}).addTo(state.map);
  const idx=state.mapPoints.length;
  state.mapPoints.push({lat,lng,type}); state.mapMarkers.push(marker);
  marker.on('contextmenu',()=>setMarkerAsEnd(idx));
  marker.on('dragend',async()=>{const p=marker.getLatLng();state.mapPoints[idx].lat=p.lat;state.mapPoints[idx].lng=p.lng;await redrawAllSegments();updatePointCount();});
  if (state.mapPoints.length>=2) await appendSegment(state.mapPoints[idx-1],state.mapPoints[idx]);
  updatePointCount();
  if (type==='start') updateInstructionStep(2);
  if (state.mapPoints.length>=3) {document.getElementById('map-end-hint').classList.remove('hidden');updateInstructionStep(3);}
}
function setMarkerAsEnd(idx) {
  if (state.mapHasEnd) return; state.mapHasEnd=true;
  state.mapPoints[idx].type='end'; state.mapMarkers[idx].setIcon(makeMarkerIcon('end'));
  document.getElementById('map-end-hint').classList.add('hidden'); updateInstructionStep(4);
}
async function appendSegment(from,to) {
  setSnapNotice('Snapping to road…');
  try {
    const res=await fetchJSON(`/api/route-segment?from=${from.lat},${from.lng}&to=${to.lat},${to.lng}`);
    let pts;
    if (res.fallback||!res.points?.length){pts=[[from.lat,from.lng],[to.lat,to.lng]];if(res.fallback)setSnapNotice('Straight lines (add TOMTOM_API_KEY for real streets)');}
    else{pts=res.points.map(p=>[p.lat,p.lng]);setSnapNotice('');}
    state.mapSegments.push(L.polyline(pts,{color:'#00a3e0',weight:4,opacity:.8}).addTo(state.map));
  } catch {
    state.mapSegments.push(L.polyline([[from.lat,from.lng],[to.lat,to.lng]],{color:'#00a3e0',weight:3,opacity:.6,dashArray:'5,5'}).addTo(state.map));
    setSnapNotice('');
  }
}
async function redrawAllSegments() {
  state.mapSegments.forEach(s=>state.map.removeLayer(s)); state.mapSegments=[];
  for (let i=1;i<state.mapPoints.length;i++) await appendSegment(state.mapPoints[i-1],state.mapPoints[i]);
}
function setSnapNotice(msg){const el=document.getElementById('map-snap-notice');if(el)el.textContent=msg;}
function mapUndo() {
  if (!state.mapPoints.length) return;
  if (state.mapPoints[state.mapPoints.length-1].type==='end') state.mapHasEnd=false;
  state.map.removeLayer(state.mapMarkers.pop()); state.mapPoints.pop();
  if (state.mapSegments.length) state.map.removeLayer(state.mapSegments.pop());
  updatePointCount();
  document.getElementById('map-end-hint').classList.toggle('hidden',state.mapPoints.length<3||state.mapHasEnd);
  if(!state.mapPoints.length)updateInstructionStep(1);
  else if(state.mapPoints.length===1)updateInstructionStep(2);
  else if(!state.mapHasEnd)updateInstructionStep(3);
}
function mapClear() {
  state.mapMarkers.forEach(m=>state.map&&state.map.removeLayer(m));
  state.mapSegments.forEach(s=>state.map&&state.map.removeLayer(s));
  state.mapMarkers=[]; state.mapSegments=[]; state.mapPoints=[]; state.mapHasEnd=false;
  updatePointCount(); document.getElementById('map-end-hint')?.classList.add('hidden');
  updateInstructionStep(1); setSnapNotice('');
}
function updatePointCount(){const el=document.getElementById('map-point-count');if(el)el.textContent=`${state.mapPoints.length} point${state.mapPoints.length!==1?'s':''}`;}
function updateInstructionStep(a){for(let i=1;i<=4;i++)document.getElementById(`inst-${i}`)?.classList.toggle('active',i===a);}
async function mapSearch() {
  const q=document.getElementById('map-search').value.trim(); if(!q||!state.map)return;
  try{const d=await fetchJSON(`/api/geocode?q=${encodeURIComponent(q)}`);state.map.setView([d.lat,d.lng],14);}
  catch{alert('Address not found.');}
}
async function saveMapRoute() {
  const name=document.getElementById('map-route-name').value.trim();
  if (!name) return alert('Give your route a name first.');
  if (state.mapPoints.length<2) return alert('Drop at least 2 points on the map.');
  const sp=state.mapPoints[0]; const ep=state.mapPoints[state.mapPoints.length-1];
  try {
    await fetchJSON('/api/routes','POST',{name,origin:`${sp.lat.toFixed(6)},${sp.lng.toFixed(6)}`,destination:`${ep.lat.toFixed(6)},${ep.lng.toFixed(6)}`,waypoints:JSON.stringify({type:'map',points:state.mapPoints.map(p=>({lat:p.lat,lng:p.lng,type:p.type}))})});
    mapClear(); closeMapBuilder(); loadRoutes();
  } catch(e){alert(`Save failed: ${e.message}`);}
}

// ─────────────────────────────────────────────
//  MODALS
// ─────────────────────────────────────────────
function openModal(id)  { document.getElementById(id).classList.remove('hidden'); }
function closeModal(id) { document.getElementById(id).classList.add('hidden'); }
function setupModalOverlayClose() {
  document.addEventListener('click', e => {
    if (e.target.classList.contains('modal-overlay')) e.target.classList.add('hidden');
    if (e.target.classList.contains('sched-modal-overlay')) e.target.classList.add('hidden');
  });
}

// ─────────────────────────────────────────────
//  HELPERS
// ─────────────────────────────────────────────
async function fetchJSON(url, method='GET', body=null) {
  const opts={method,headers:body?{'Content-Type':'application/json'}:{}};
  if (body) opts.body=JSON.stringify(body);
  const r=await fetch(url,opts);
  if (!r.ok){const err=await r.json().catch(()=>({message:r.statusText}));throw new Error(err.message||r.statusText);}
  return r.json();
}
function noKeyNotice(service, envVar) {
  return `<div class="no-key-notice"><span class="notice-title">⚙ ${service} key not configured</span><span class="notice-msg">Add <strong>${envVar}</strong> to your .env and restart.</span></div>`;
}
function escHtml(str) { return String(str).replace(/'/g,"\\'").replace(/"/g,'&quot;'); }
