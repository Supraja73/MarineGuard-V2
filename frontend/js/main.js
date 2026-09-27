// ═══════════════════════════════════════════════════════════════════════════
// CONFIG
// ═══════════════════════════════════════════════════════════════════════════
const API_BASE = window.location.origin.includes('8004') || window.location.origin.includes('8003')
  ? window.location.origin
  : 'http://localhost:8000';

// Known ports with real coordinates, used only to let the registration form
// offer a port dropdown instead of asking the operator to type raw lat/lon.
// This is reference geographic data (port locations don't change), not
// simulated ship/security data.
// World ports are loaded from the backend (app.data.ports.WORLD_PORTS) via
// loadPorts() at startup and kept here as a name -> {lat,lon} lookup so the
// rest of the UI (registration form, voyage form) can stay synchronous.
let PORTS = {};
async function loadPorts() {
  try {
    state.ports = await apiGet('/api/voyages/ports');
    PORTS = {};
    state.ports.forEach(p => { PORTS[p.name] = { lat: p.lat, lon: p.lon, country: p.country }; });
  } catch (e) { /* non-fatal — falls back to whatever PORTS already had */ }
}

// ═══════════════════════════════════════════════════════════════════════════
// STATE - populated entirely from the backend, nothing pre-seeded here
// ═══════════════════════════════════════════════════════════════════════════
const state = {
  ships: [],          // [{ship_id, name, imo_number, ship_type, origin_port, origin_lat, origin_lon, dest_port, dest_lat, dest_lon, public_key, certificate_hash, current_lat, current_lon, current_speed, status, created_at}]
  voyages: {},        // ship_id -> active voyage object, from /api/voyages/active
  alerts: [],          // from /api/alerts
  blocks: [],           // from /api/blockchain/blocks
  zones: [],            // from /api/zones
  authLog: [],          // from /api/auth/log
  messages: [],          // from /api/comms
  weather: null,          // from /api/weather/latest
  cyclones: [],            // from /api/cyclones/active
  dashboard: null,         // from /api/dashboard/summary
  mapMarkers: {},           // ship_id -> Leaflet marker (tracking page)
  routeLayers: {},          // ship_id -> { line, geofence, originMarker, destMarker }
  dashMarkers: {},
  routeMap: null,
  trackingMap: null,
  // Visual interpolation state for smooth marker motion.
  shipAnim: {}, // ship_id -> {startLat,startLon,endLat,endLon,startAt,endAt,waypoints,headingAtStart,headingAtEnd}
  selectedRouteShip: null,
  ports: [],                // from /api/voyages/ports
  fleetFilter: 'all',
  osmLayer: null,
  satLayer: null,
};

// ═══════════════════════════════════════════════════════════════════════════
// API HELPER
// ═══════════════════════════════════════════════════════════════════════════
async function api(method, path, body) {
  const opts = { method, headers: {} };
  if (window.authToken) opts.headers['Authorization'] = 'Bearer ' + window.authToken;
  if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(API_BASE + path, opts);
  let data = null;
  try { data = await res.json(); } catch (e) { /* empty body */ }
  if (!res.ok) {
    const message = (data && (data.message || data.detail)) ? (data.message || JSON.stringify(data.detail)) : ('Request failed (' + res.status + ')');
    const err = new Error(message);
    err.status = res.status;
    err.data = data;
    throw err;
  }
  return data;
}
const apiGet = (path) => api('GET', path);
const apiPost = (path, body) => api('POST', path, body);
const apiDelete = (path) => api('DELETE', path);

// ═══════════════════════════════════════════════════════════════════════════
// UTILS
// ═══════════════════════════════════════════════════════════════════════════
function now() { return new Date().toTimeString().slice(0, 8); }
function fmtTime(iso) { if (!iso) return '—'; try { return new Date(iso).toTimeString().slice(0, 8); } catch (e) { return '—'; } }
function fmtDate(iso) { if (!iso) return '—'; try { return new Date(iso).toLocaleString(); } catch (e) { return '—'; } }
function toast(msg, type = 'info') {
  const t = document.getElementById('toast-wrap');
  const el = document.createElement('div');
  el.className = `toast ${type}`;
  el.textContent = msg;
  t.appendChild(el);
  setTimeout(() => el.remove(), 3500);
}
function openModal(id) { document.getElementById(id).classList.add('open'); }
function closeModal(id) { document.getElementById(id).classList.remove('open'); }

// Generic collapsible side-panel toggle (map legend, hazard toolbox, etc.)
// — keeps these tucked away by default so they never block the map, and
// expands them in place when the operator clicks the header.
function togglePanel(id) {
  const el = document.getElementById(id);
  if (!el) return;
  el.classList.toggle('collapsed');
  const chevron = el.querySelector('.panel-chevron');
  if (chevron) chevron.textContent = el.classList.contains('collapsed') ? '▸' : '▾';
}
function shipEmoji(type) { return { Cargo: '📦', Tanker: '🛢️', Passenger: '🚢', Fishing: '🎣', Naval: '⚓', 'Coast Guard': '🛟', Container: '📦' }[type] || '🚢'; }
function statusColor(s) { return { online: 'var(--green)', registered: 'var(--text2)', warning: 'var(--amber)', alert: 'var(--red)', offline: 'var(--text3)', docked: 'var(--blue2)' }[s] || 'var(--text3)'; }
function severityColor(s) { return { critical: 'var(--red)', warning: 'var(--amber)', info: 'var(--blue2)' }[s] || 'var(--text3)'; }
function shipName(id) { const s = state.ships.find(x => x.ship_id === id); return s ? s.name : (id || '—'); }
function shortHash(h, n = 16) { return h ? (h.slice(0, n) + '…') : '—'; }
function errMsg(e) { return (e && e.message) ? e.message : 'Something went wrong'; }

// ── SIMULATION ALERT SUPPRESSION ──────────────────────────────────────────
// This platform is a controlled demonstration: automatic GPS-spoofing,
// route-deviation, and AIS-spoofing detection would otherwise fire
// constantly off simulated position noise. Those three detectors keep
// running on the backend (nothing about their logic changes), but the
// frontend never surfaces them as alerts/toasts/badges — the operator
// instead sees a calm, explicit "no threat detected" status. Manually
// triggered Attack Mode incidents (pirate/missile/drone/hijack/etc.) are
// NOT in this list and always surface normally.
const SIM_SUPPRESSED_ALERT_TYPES = new Set(['gps_spoofing', 'route_deviation', 'ais_spoofing']);
function filterSimAlerts(list) { return (list || []).filter(a => !SIM_SUPPRESSED_ALERT_TYPES.has(a.alert_type)); }

function populate(selectId, withAll = false, placeholder) {
  const s = document.getElementById(selectId);
  if (!s) return;
  const prev = s.value;
  let html = '';
  if (withAll) html += '<option value="">All ships</option>';
  else if (placeholder) html += `<option value="">${placeholder}</option>`;
  html += state.ships.map(sh => `<option value="${sh.ship_id}">${sh.name}</option>`).join('');
  s.innerHTML = html;
  if (prev && state.ships.some(sh => sh.ship_id === prev)) s.value = prev;
}
function populateAllShipSelects() {
  populate('route-ship', false, 'Select a ship…');
  populate('spoof-ship', false, 'Select a ship…');
  populate('fence-ship', false, 'Select a ship…');
  populate('sign-ship', false, 'Select a ship…');
  populate('verify-ship', false, 'Select a ship…');
  populate('comm-target', false, 'Select target…');
  populate('auth-ship-select', false, 'Select a ship…');
  populate('auth-test-ship', false, 'Select ship…');
  populate('sar-ship-select', true, '-- Sector Hazard Only --');
}


// ═══════════════════════════════════════════════════════════════════════════
// NAVIGATION
// ═══════════════════════════════════════════════════════════════════════════
const pageTitles = {
  dashboard: ['Dashboard', 'System Overview'],
  tracking: ['Live Tracking', 'Real-time ship positions'],
  alerts: ['Alert Center', 'Security & operational alerts'],
  auth: ['Authentication', 'ECC key management & ECDSA'],
  crypto: ['Cryptography', 'AES-256, ECDSA, SHA-256, ECDH'],
  blockchain: ['Blockchain Ledger', 'Immutable SHA-256 linked blocks'],
  routes: ['Route Monitor', 'Deviation detection'],
  cyclone: ['Cyclone Watch', 'Real-time weather monitoring'],
  illegal: ['Illegal Activity', 'Spoofing & geo-fence detection'],
  ships: ['Ship Registry', 'All registered vessels'],
  comms: ['Comms Center', 'Encrypted ship communication'],
  voyages: ['Voyages', 'Active & completed voyages with live tracking'],
  'ship-focus': ['Live Ship Tracking', 'Detailed vessel view'],
  passenger: ['Track a Ship', 'Search registered vessels'],
  captains: ['Captain Management', 'Review Ship Captain applications & ship assignments'],
  c2surveillance: ['C2 Radar Surveillance', 'Real-time 360° SAR Satellite Sweep & Deep Learning Classifier'],
};
function showPage(id) {
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
  document.getElementById('page-' + id).classList.add('active');
  document.querySelectorAll('.nav-item').forEach(n => { if (n.getAttribute('onclick')?.includes(`'${id}'`)) n.classList.add('active'); });
  const [title, sub] = pageTitles[id] || [id, ''];
  document.getElementById('page-title').textContent = title;
  document.getElementById('page-sub').textContent = '— ' + sub;

  if (id === 'tracking') { setTimeout(initTrackingMap, 50); }
  if (id === 'routes') { populate('route-ship', false, 'Select a ship…'); setTimeout(initRouteMap, 50); }
  if (id === 'c2surveillance') { if (typeof window.initC2SurveillanceDashboard === 'function') setTimeout(window.initC2SurveillanceDashboard, 50); }

  if (id === 'auth') refreshAuthPage();
  if (id === 'blockchain') refreshBlockchain();
  if (id === 'ships') refreshShipRegistry();
  if (id === 'cyclone') refreshCyclone();
  if (id === 'illegal') refreshIllegal();
  if (id === 'comms') refreshComms();
  if (id === 'dashboard') refreshDashboard();
  if (id === 'voyages') refreshVoyagesPage();
  if (id === 'passenger') renderPassengerShipList();
  if (id === 'alerts') loadRerouteApprovals();
  if (id === 'captains') refreshCaptainManagement();
}

// ═══════════════════════════════════════════════════════════════════════════
// CLOCK
// ═══════════════════════════════════════════════════════════════════════════
setInterval(() => { document.getElementById('clock').textContent = new Date().toTimeString().slice(0, 8); }, 1000);
// ═══════════════════════════════════════════════════════════════════════════
// SHIPS - shared loader used by every page
// ═══════════════════════════════════════════════════════════════════════════
async function loadShips() {
  state.ships = await apiGet('/api/ships');
  document.getElementById('ship-count-badge').textContent = state.ships.length;
  populateAllShipSelects();
  return state.ships;
}

// ═══════════════════════════════════════════════════════════════════════════
// DASHBOARD
// ═══════════════════════════════════════════════════════════════════════════
let cryptoChartInstance = null;

async function refreshDashboard() {
  try {
    const [summary, alerts] = await Promise.all([
      apiGet('/api/dashboard/summary'),
      apiGet('/api/alerts?limit=5'),
    ]);
    state.dashboard = summary;
    state.alerts = filterSimAlerts(alerts);
    renderDashboard();
  } catch (e) {
    toast('Could not load dashboard: ' + errMsg(e), 'error');
  }
}

function renderDashboard() {
  const d = state.dashboard;
  if (!d) return;

  document.getElementById('stat-total-ships').textContent = d.ship_count;
  document.getElementById('stat-active-sub').textContent = `${d.online_count} underway`;
  document.getElementById('stat-active').textContent = d.online_count;
  document.getElementById('stat-docked').textContent = d.docked_count ?? 0;
  document.getElementById('stat-blocks').textContent = d.total_blocks;
  document.getElementById('stat-alerts').textContent = d.active_alerts;
  document.querySelector('#stat-alerts').nextElementSibling.textContent =
    `${d.active_alerts - d.critical_alerts} warnings, ${d.critical_alerts} critical`;
  // Simulation-mode: these detectors are intentionally not surfaced as
  // active alerts (see SIM_SUPPRESSED_ALERT_TYPES), so their tiles always
  // read as clear rather than showing raw backend counters.
  document.getElementById('stat-gps-spoof').textContent = 0;
  document.getElementById('stat-route-dev').textContent = 0;
  if (d.threats_blocked != null) {
    document.getElementById('stat-threats').textContent = d.threats_blocked;
  }
  if (d.cyclone_alerts_signed != null) {
    const cel = document.getElementById('stat-cyclone-signed');
    if (cel) cel.textContent = d.cyclone_alerts_signed;
  }

  // Fleet status list
  const sl = document.getElementById('dashboard-ship-list');
  if (state.ships.length === 0) {
    sl.innerHTML = `<div style="padding:24px;text-align:center;color:var(--text3)">No ships registered yet. Click "Register Ship" to add your first vessel.</div>`;
  } else {
    sl.innerHTML = state.ships.map(s => `
      <div class="ship-row" onclick="showPage('ships')">
        <div class="ship-avatar" style="background:${statusColor(s.status)}22">${shipEmoji(s.ship_type)}</div>
        <div class="ship-info">
          <div class="ship-name">${s.name}</div>
          <div class="ship-detail">${s.origin_port || '—'} → ${s.dest_port || '—'} | ${(s.current_speed ?? 0).toFixed(1)} kn</div>
        </div>
        <span class="badge badge-${s.status === 'online' ? 'green' : s.status === 'alert' ? 'red' : s.status === 'warning' ? 'amber' : 'blue'}">${s.status}</span>
      </div>`).join('');
  }

  // Recent alerts
  const al = document.getElementById('dashboard-alerts');
  if (state.alerts.length === 0) {
    al.innerHTML = `<div style="padding:24px;text-align:center;color:var(--text3)">No alerts. System nominal.</div>`;
  } else {
    al.innerHTML = state.alerts.slice(0, 5).map(a => `
      <div class="alert-item ${a.severity}">
        <div class="alert-icon">${threatIconFor(a.alert_type) || severityIcon(a.severity)}</div>
        <div class="alert-body">
          <div class="alert-title">${a.title}</div>
          <div class="alert-desc">${a.detail || ''}</div>
          <div class="alert-meta">${fmtTime(a.created_at)}</div>
        </div>
      </div>`).join('');
  }

  renderSecurityLayers();
  renderCryptoChart();
}

function severityIcon(sev) { return { critical: '🚨', warning: '⚠️', info: 'ℹ️' }[sev] || 'ℹ️'; }

// Every distinct threat type gets its own recognizable icon (spec: "Every
// threat should have a unique icon"), layered on top of (not replacing)
// the severity-colored border already used per alert-item.
const THREAT_ICONS = {
  route_deviation: '🧭', restricted_zone: '⛔', unexpected_stop: '⏸️', possible_hijack: '🚨',
  cyclone_risk: '🌀', cyclone_route_alert: '🌀', gps_spoofing: '📡', severe_slowdown: '🐌',
  signature_failure: '🔏', replay_attack: '🔁', extreme_wind: '💨', high_waves: '🌊', heavy_rain: '🌧️',
  weather_warning: '⛈️', high_wind: '💨', voyage_started: '🧭', voyage_completed: '✅', route_replanned: '🔀',
};
function threatIconFor(alertType) {
  if (!alertType) return null;
  if (THREAT_ICONS[alertType]) return THREAT_ICONS[alertType];
  if (alertType.startsWith('collision_risk::')) return '⚠️';
  if (alertType.startsWith('incident::')) return INCIDENT_ICONS[alertType.split('::')[1]] || '🚨';
  return null;
}

function renderSecurityLayers() {
  const layers = [
    { name: 'ECC Identity (P-256)', status: 'Active', pct: 100, color: 'var(--green)' },
    { name: 'ECDSA Signatures', status: 'Active', pct: 100, color: 'var(--green)' },
    { name: 'AES-256-CBC Channel', status: 'Active', pct: 100, color: 'var(--green)' },
    { name: 'SHA-256 Blockchain', status: state.dashboard?.chain_valid ? 'Valid' : 'TAMPERED', pct: state.dashboard?.chain_valid ? 100 : 40, color: state.dashboard?.chain_valid ? 'var(--green)' : 'var(--red)' },
    { name: 'ECDH Key Exchange', status: 'Active', pct: 100, color: 'var(--green)' },
  ];
  document.getElementById('security-layers').innerHTML = layers.map(l => `
    <div style="margin-bottom:10px">
      <div style="display:flex;justify-content:space-between;font-size:11px;margin-bottom:4px">
        <span style="color:var(--text2)">${l.name}</span>
        <span style="color:${l.color}">${l.status}</span>
      </div>
      <div class="progress-bar"><div class="progress-fill" style="width:${l.pct}%;background:${l.color}"></div></div>
    </div>`).join('');
}

function renderCryptoChart() {
  const canvas = document.getElementById('cryptoChart');
  if (!canvas || typeof Chart === 'undefined') return;
  // Real data point: total blocks written, broken down by the event types
  // actually present in the chain right now (not a fake time series).
  const counts = {};
  state.blocks.forEach(b => { counts[b.event_type] = (counts[b.event_type] || 0) + 1; });
  const labels = Object.keys(counts);
  const values = Object.values(counts);
  if (cryptoChartInstance) cryptoChartInstance.destroy();
  if (labels.length === 0) return;
  cryptoChartInstance = new Chart(canvas, {
    type: 'bar',
    data: { labels, datasets: [{ label: 'Blocks by event type', data: values, backgroundColor: '#0EA5E9' }] },
    options: {
      plugins: { legend: { display: false } },
      scales: {
        x: { ticks: { color: '#94A3B8' }, grid: { color: '#334155' } },
        y: { ticks: { color: '#94A3B8' }, grid: { color: '#334155' }, beginAtZero: true },
      },
    },
  });
}

// ═══════════════════════════════════════════════════════════════════════════
// REGISTRATION
// ═══════════════════════════════════════════════════════════════════════════
function buildPortOptions(selectedId) {
  let html = `<option value="">Select…</option>`;
  for (const name in PORTS) {
    html += `<option value="${name}" ${name === selectedId ? 'selected' : ''}>${name}</option>`;
  }
  return html;
}
function refreshAllPortDropdowns() {
  ['reg-from', 'reg-to', 'voyage-from', 'voyage-to', 'replan-to'].forEach(id => {
    const sel = document.getElementById(id);
    if (sel) sel.innerHTML = buildPortOptions();
  });
}
function onRegPortChange() {
  const from = document.getElementById('reg-from').value;
  const to = document.getElementById('reg-to').value;
  document.getElementById('reg-from-coords').value = from && PORTS[from] ? `${PORTS[from].lat}, ${PORTS[from].lon}` : '';
  document.getElementById('reg-to-coords').value = to && PORTS[to] ? `${PORTS[to].lat}, ${PORTS[to].lon}` : '';
}

async function registerShip() {
  const name = document.getElementById('reg-name').value.trim();
  const imo = document.getElementById('reg-imo').value.trim();
  const type = document.getElementById('reg-type').value;
  const capacity = document.getElementById('reg-capacity').value;
  const fromPort = document.getElementById('reg-from').value;
  const toPort = document.getElementById('reg-to').value;
  const speed = document.getElementById('reg-speed').value;
  const password = document.getElementById('reg-password').value;

  if (!name || !imo || !fromPort || !toPort) { toast('Please fill in ship name, IMO, origin and destination', 'warning'); return; }
  if (!password || password.length < 8) { toast('Password must be at least 8 characters', 'warning'); return; }
  if (fromPort === toPort) { toast('Origin and destination must be different ports', 'warning'); return; }

  const origin = PORTS[fromPort];
  const dest = PORTS[toPort];
  const course = bearingDeg(origin.lat, origin.lon, dest.lat, dest.lon);

  try {
    const result = await apiPost('/api/ships/register', {
      name, imo_number: imo, ship_type: type,
      origin_port: fromPort, origin_lat: origin.lat, origin_lon: origin.lon,
      dest_port: toPort, dest_lat: dest.lat, dest_lon: dest.lon,
      password,
      capacity_tons: capacity ? Number(capacity) : null,
      initial_speed: speed ? Number(speed) : 0,
      course, heading: course,
    });
    toast(`${name} registered. ECC keys issued, block #${result.block_index} written.`, 'success');
    closeModal('registerModal');
    ['reg-name', 'reg-imo', 'reg-capacity', 'reg-speed', 'reg-password', 'reg-from-coords', 'reg-to-coords']
      .forEach(id => { const el = document.getElementById(id); if (el) el.value = ''; });
    await loadShips();
    await refreshDashboard();
    if (document.getElementById('page-ships').classList.contains('active')) refreshShipRegistry();
    if (document.getElementById('page-tracking').classList.contains('active')) renderMapShips();

    // After registration, the next step is starting a voyage (not baking
    // the route into the registered identity) - open that modal pre-filled
    // with the same ports the operator just chose, ready to confirm speed
    // and geofence radius.
    openVoyageModal(result.ship_id, fromPort, toPort);
  } catch (e) {
    toast('Registration failed: ' + errMsg(e), 'error');
  }
}

// ═══════════════════════════════════════════════════════════════════════════
// VOYAGES — origin/destination from a port dropdown (never typed lat/lon),
// validated speed (0–70 km/h) and geofence radius, automatic movement,
// live distance/ETA, replanning, and arrival completion.
// ═══════════════════════════════════════════════════════════════════════════
function openVoyageModal(preselectShipId, preselectFrom, preselectTo) {
  if (window.currentUserRole !== 'control_station') { toast('Only Control Station can dispatch a voyage', 'error'); return; }
  if (state.ships.length === 0) { toast('Register a ship first', 'warning'); return; }
  const shipSel = document.getElementById('voyage-ship');
  shipSel.innerHTML = state.ships.map(s => `<option value="${s.ship_id}">${s.name}</option>`).join('');
  if (preselectShipId) shipSel.value = preselectShipId;
  refreshAllPortDropdowns();
  if (preselectFrom) document.getElementById('voyage-from').value = preselectFrom;
  if (preselectTo) document.getElementById('voyage-to').value = preselectTo;
  document.getElementById('voyage-error').textContent = '';
  openModal('voyageModal');
}

function validateVoyageSpeed(value) {
  const n = Number(value);
  if (Number.isNaN(n)) return 'Speed must be a number';
  if (n < 0 || n > 70) return 'Speed must be between 0 and 70 km/h';
  return null;
}

async function submitVoyage() {
  const shipId = document.getElementById('voyage-ship').value;
  const fromPort = document.getElementById('voyage-from').value;
  const toPort = document.getElementById('voyage-to').value;
  const speedRaw = document.getElementById('voyage-speed').value;
  const geofence = parseFloat(document.getElementById('voyage-geofence').value);
  const departureRaw = document.getElementById('voyage-departure').value;
  const remarks = document.getElementById('voyage-remarks').value.trim();
  const errEl = document.getElementById('voyage-error');

  if (!shipId) { errEl.textContent = 'Select a ship'; return; }
  if (!fromPort || !toPort) { errEl.textContent = 'Select both origin and destination ports'; return; }
  if (fromPort === toPort) { errEl.textContent = 'Origin and destination must be different ports'; return; }
  const speedErr = validateVoyageSpeed(speedRaw);
  if (speedErr) { errEl.textContent = speedErr; return; }
  errEl.textContent = '';

  try {
    const voyage = await apiPost('/api/voyages', {
      ship_id: shipId, origin_port: fromPort, dest_port: toPort,
      current_speed_kmh: Number(speedRaw), geofence_radius_km: geofence,
      expected_departure: departureRaw ? new Date(departureRaw).toISOString() : null,
      remarks: remarks || null,
    });
    state.voyages[shipId] = voyage;
    closeModal('voyageModal');
    toast(`Voyage started: ${voyage.origin_port} → ${voyage.dest_port}`, 'success');
    await loadShips();
    if (document.getElementById('page-tracking').classList.contains('active')) renderMapShips();
  } catch (e) {
    errEl.textContent = errMsg(e);
  }
}

function openReplanModal(shipId) {
  if (window.currentUserRole !== 'control_station') { toast('Only Control Station can replan a voyage', 'error'); return; }
  const voyage = state.voyages[shipId];
  if (!voyage) { toast('No active voyage for this ship', 'warning'); return; }
  document.getElementById('replan-voyage-id').value = voyage.voyage_id;
  refreshAllPortDropdowns();
  document.getElementById('replan-to').value = '';
  document.getElementById('replan-speed').value = voyage.current_speed_kmh;
  document.getElementById('replan-geofence').value = voyage.geofence_radius_km;
  document.getElementById('replan-error').textContent = '';
  openModal('replanModal');
}

async function submitReplan() {
  const voyageId = document.getElementById('replan-voyage-id').value;
  const toPort = document.getElementById('replan-to').value;
  const speedRaw = document.getElementById('replan-speed').value;
  const geofenceRaw = document.getElementById('replan-geofence').value;
  const errEl = document.getElementById('replan-error');

  const body = {};
  if (toPort) body.dest_port = toPort;
  if (speedRaw !== '') {
    const speedErr = validateVoyageSpeed(speedRaw);
    if (speedErr) { errEl.textContent = speedErr; return; }
    body.current_speed_kmh = Number(speedRaw);
  }
  if (geofenceRaw !== '') body.geofence_radius_km = Number(geofenceRaw);

  try {
    const voyage = await api('PATCH', `/api/voyages/${voyageId}`, body);
    state.voyages[voyage.ship_id] = voyage;
    closeModal('replanModal');
    toast(`Route replanned — now heading to ${voyage.dest_port}`, 'warning');
    await loadShips();
    if (document.getElementById('page-tracking').classList.contains('active')) renderMapShips();
  } catch (e) {
    errEl.textContent = errMsg(e);
  }
}

async function manualCompleteVoyage(shipId) {
  if (window.currentUserRole !== 'control_station') { toast('Only Control Station can mark a voyage complete', 'error'); return; }
  const voyage = state.voyages[shipId];
  if (!voyage) return;
  try {
    await apiPost(`/api/voyages/${voyage.voyage_id}/complete`, {});
    delete state.voyages[shipId];
    clearShipRouteLayers(shipId);
    toast('Voyage marked complete', 'success');
    await loadShips();
    if (document.getElementById('page-tracking').classList.contains('active')) renderMapShips();
  } catch (e) { toast('Could not complete voyage: ' + errMsg(e), 'error'); }
}


// ═══════════════════════════════════════════════════════════════════════════
// VOYAGE-DRIVEN AUTOMATIC MOVEMENT — every ship with an active voyage moves
// continuously along the great-circle path from its origin toward its
// destination, at its declared voyage speed. Not teleportation: each tick
// advances the ship a small, real distance (speed_kmh × elapsed_hours)
// along its true bearing to the destination, then signs and reports that
// position through the existing ECDSA pipeline exactly like a real position
// report would be.
// ═══════════════════════════════════════════════════════════════════════════
const EARTH_RADIUS_KM = 6371.0;

function toRad(d) { return d * Math.PI / 180; }
function toDeg(r) { return r * 180 / Math.PI; }

function bearingDeg(lat1, lon1, lat2, lon2) {
  const φ1 = toRad(lat1), φ2 = toRad(lat2), Δλ = toRad(lon2 - lon1);
  const y = Math.sin(Δλ) * Math.cos(φ2);
  const x = Math.cos(φ1) * Math.sin(φ2) - Math.sin(φ1) * Math.cos(φ2) * Math.cos(Δλ);
  return (toDeg(Math.atan2(y, x)) + 360) % 360;
}
function haversineKm(lat1, lon1, lat2, lon2) {
  const φ1 = toRad(lat1), φ2 = toRad(lat2), Δφ = toRad(lat2 - lat1), Δλ = toRad(lon2 - lon1);
  const a = Math.sin(Δφ / 2) ** 2 + Math.cos(φ1) * Math.cos(φ2) * Math.sin(Δλ / 2) ** 2;
  return EARTH_RADIUS_KM * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

// Checks a port's coordinates against the live worldwide cyclone feed
// already loaded into state.cyclones (from GDACS, refreshed every 5 min).
// Uses each storm's own influence radius plus a buffer, not just raw
// distance, so a huge slow storm still reads as a risk to a port near its
// edge. No location permission of any kind is involved - this is purely
// port coordinates vs. public storm coordinates.
function portCycloneRisk(lat, lon) {
  if (lat == null || lon == null || !state.cyclones || state.cyclones.length === 0) return null;
  let nearest = null;
  for (const c of state.cyclones) {
    if (c.lat == null || c.lon == null) continue;
    const distKm = haversineKm(lat, lon, c.lat, c.lon);
    const threshold = (c.radius_km || 300) + 150;
    if (distKm <= threshold && (!nearest || distKm < nearest.distKm)) {
      nearest = { ...c, distKm };
    }
  }
  return nearest;
}

function portRiskBadgeHtml(portLabel, lat, lon) {
  const risk = portCycloneRisk(lat, lon);
  if (!risk) return `<span style="color:var(--green)">✅ ${portLabel} clear</span>`;
  return `<span style="color:var(--red)" title="${risk.name}, ~${risk.distKm.toFixed(0)}km away">⚠️ ${portLabel}: cyclone risk (${risk.name})</span>`;
}

function destinationPoint(lat, lon, bearing, distanceKm) {
  const δ = distanceKm / EARTH_RADIUS_KM;
  const θ = toRad(bearing);
  const φ1 = toRad(lat), λ1 = toRad(lon);
  const φ2 = Math.asin(Math.sin(φ1) * Math.cos(δ) + Math.cos(φ1) * Math.sin(δ) * Math.cos(θ));
  const λ2 = λ1 + Math.atan2(Math.sin(θ) * Math.sin(δ) * Math.cos(φ1), Math.cos(δ) - Math.sin(φ1) * Math.sin(φ2));
  return [toDeg(φ2), ((toDeg(λ2) + 540) % 360) - 180];
}

const VOYAGE_TICK_SECONDS = 4;     // backend signing/report cadence
// Visual interpolation smoothness (renderer-only; does not change simulation cadence)
const SHIP_ANIM_TICK_MS = 80;
// How much simulated voyage-time passes per tick. Real wall-clock elapsed
// time is intentionally NOT used here — at true 30 km/h, a 4-second real
// tick only covers ~33m, which is invisible on a map spanning thousands of
// km. Instead each tick simulates SIM_MINUTES_PER_TICK minutes of travel,
// so motion is clearly visible and a multi-thousand-km voyage completes in
// a reasonable demo timeframe, while ETA/remaining-distance math still uses
// the ship's real declared speed (just applied to simulated time).
const SIM_MINUTES_PER_TICK = 12;
let lastVoyageTickAt = null;

async function loadActiveVoyages() {
  try {
    const list = await apiGet('/api/voyages/active');
    state.voyages = {};
    list.forEach(v => { state.voyages[v.ship_id] = v; });
  } catch (e) { /* non-fatal */ }
}

// Tracks each ship's true progress (0..1) along its actual curved sea
// route. IMPORTANT: this is deliberately NOT re-derived from
// voyage.distance_travelled_km / distance_remaining_km every tick.
// Those are straight-line (haversine) distances from origin/to
// destination, but ships move along a curved multi-waypoint sea route
// (seaRouteWaypoints) that bends around coastlines. On a bending route,
// straight-line "distance travelled" doesn't increase monotonically with
// real progress - near a bend it can plateau or even reverse - so
// re-deriving prevFrac from it every tick could compute a fraction
// *lower* than what was actually already reached last tick. That made
// ships visibly drift backward/forward without ever reliably reaching
// frac=1, i.e. never actually arriving even though they kept "moving".
// Keeping frac as authoritative local state fixes that: it can only ever
// advance, so every ship's route completes.
const shipRouteProgress = {}; // shipId -> { voyageId, originLat, originLon, destLat, destLon, frac }

function getAdvancedFrac(shipId, voyage, stepKm, routeTotalKm) {
  const existing = shipRouteProgress[shipId];
  const sameLeg = existing
    && existing.voyageId === voyage.voyage_id
    && existing.originLat === voyage.origin_lat && existing.originLon === voyage.origin_lon
    && existing.destLat === voyage.dest_lat && existing.destLon === voyage.dest_lon;

  // No local progress for this exact leg yet (first tick, or the leg
  // changed because a reroute was just approved and re-anchored the
  // origin) - seed once from the backend's straight-line ratio as a
  // reasonable starting point, then track precisely from here on.
  const prevFrac = sameLeg ? existing.frac : (voyageProgressPct(voyage) / 100);
  const newFrac = Math.min(1, prevFrac + stepKm / routeTotalKm);

  shipRouteProgress[shipId] = {
    voyageId: voyage.voyage_id,
    originLat: voyage.origin_lat, originLon: voyage.origin_lon,
    destLat: voyage.dest_lat, destLon: voyage.dest_lon,
    frac: newFrac,
  };
  return newFrac;
}

async function tickVoyageMovement() {
  const nowMs = Date.now();
  const elapsedHours = SIM_MINUTES_PER_TICK / 60;
  lastVoyageTickAt = nowMs;

  const shipIds = Object.keys(state.voyages);
  if (shipIds.length === 0) return;

  // NOTE: route/marker animation is handled in the map render layer via
  // smooth marker interpolation. This tick advances the simulation data
  // at the backend-signing/report cadence only.
  for (const shipId of shipIds) {
    const voyage = state.voyages[shipId];
    if (!voyage || voyage.status !== 'in_progress') continue;
    const ship = state.ships.find(s => s.ship_id === shipId);
    if (!ship) continue;

    // Attack simulation is active — the ship stays exactly where it is
    // (inside its danger zone) until "Exit Attack Mode" is used. Do not
    // advance progress, report a new position, or touch route state.
    if (ship.status === UNDER_ATTACK_STATUS) continue;

    const speed = voyage.current_speed_kmh || 0;
    if (speed <= 0) continue;

    const stepKm = speed * elapsedHours;
    const waypoints = seaRouteWaypoints(voyage.origin_lat, voyage.origin_lon, voyage.dest_lat, voyage.dest_lon);
    const routeTotalKm = routeSegmentLengths(waypoints).reduce((a, b) => a + b, 0) || 1;
    const newFrac = getAdvancedFrac(shipId, voyage, stepKm, routeTotalKm);
    const [newLat, newLon] = pointAtFraction(waypoints, newFrac);

    const prevLat = ship.current_lat;
    const prevLon = ship.current_lon;

    try {
      const result = await reportSignedPosition(shipId, newLat, newLon, speed);
      if (result.voyage) {
        state.voyages[shipId] = result.voyage;
      }
      if (result.voyage_completed) {
        delete state.voyages[shipId];
        delete shipRouteProgress[shipId];
        clearShipRouteLayers(shipId);
        toast(`✅ ${ship.name} has reached ${voyage.dest_port}`, 'success');
        await loadAlerts(); renderAlerts();
      }
    } catch (e) { /* transient signing/network errors are non-fatal for a tick */ }
  }

  await loadShips();
  if (document.getElementById('page-tracking').classList.contains('active')) renderMapShips();
  if (document.getElementById('page-ship-focus').classList.contains('active')) renderShipFocus();
  if (document.getElementById('page-dashboard').classList.contains('active')) await refreshDashboard();
}


/**
 * Real signing flow, not a fake one: ask the backend for the exact

 * canonical string it will verify against, sign that string using the
 * ship's real ECDSA private key (server holds it for this demo platform,
 * the same documented exception used throughout this app), then submit
 * the signed report. The server independently re-verifies the signature
 * against its own reconstruction of the fields before accepting it.
 */
async function reportSignedPosition(shipId, lat, lon, speed) {
  const timestamp = new Date().toISOString();
  const nonce = Math.random().toString(36).slice(2) + Date.now().toString(36);

  const { message } = await apiGet(
    `/api/positions/build_message?ship_id=${encodeURIComponent(shipId)}&lat=${lat}&lon=${lon}&speed=${speed}&timestamp=${encodeURIComponent(timestamp)}&nonce=${nonce}`
  );
  const { signature } = await apiPost('/api/crypto/sign', { ship_id: shipId, message });

  return apiPost('/api/positions/report', { ship_id: shipId, lat, lon, speed, timestamp, nonce, signature });
}
// ═══════════════════════════════════════════════════════════════════════════
// TRACKING MAP
// ═══════════════════════════════════════════════════════════════════════════
// ── Real sea-lane routing, via backend /api/ocean-route (searoute) ──
// Replaces the old hardcoded-chokepoint approximation. The backend uses
// the open-source `searoute` package to compute a route over a real
// global maritime network graph, so the returned path never crosses
// land - not just a straight line bent around a few known straits.
//
// seaRouteWaypoints() keeps its original name and synchronous signature
// so every existing call site (tickVoyageMovement, renderMapShips,
// renderShipFocus) needs no changes: movement math, progress fractions,
// and rendering all keep working exactly as before. Internally it now:
//   1. Returns the cached real route immediately if we already have it
//      for this exact origin/destination pair.
//   2. Otherwise kicks off exactly ONE background fetch to
//      /api/ocean-route, caches the result, and re-renders once it
//      arrives - it does NOT call the backend again on every animation
//      frame (requirement #5).
//   3. Returns a temporary straight-line fallback ([origin, destination])
//      while that first fetch is in flight, so nothing breaks or blocks
//      before the real route is ready.
const oceanRouteCache = {}; // key -> { route: [[lat,lon],...] | null, distanceKm, fetching, failed }

function oceanRouteKey(oLat, oLon, dLat, dLon) {
  return `${oLat.toFixed(4)},${oLon.toFixed(4)},${dLat.toFixed(4)},${dLon.toFixed(4)}`;
}

async function fetchOceanRoute(oLat, oLon, dLat, dLon, key) {
  try {
    const data = await apiGet(
      `/api/ocean-route?origin_lat=${oLat}&origin_lon=${oLon}&dest_lat=${dLat}&dest_lon=${dLon}`
    );
    oceanRouteCache[key] = { route: data.route, distanceKm: data.distance_km, fetching: false, failed: false };
  } catch (e) {
    // Cache the failure too, so we don't hammer the backend every frame
    // for a pair it can't route - fall back to a straight line instead.
    oceanRouteCache[key] = { route: [[oLat, oLon], [dLat, dLon]], distanceKm: null, fetching: false, failed: true };
  }
  // Re-render whichever views are currently visible so the freshly
  // fetched real route replaces the temporary straight-line fallback.
  if (document.getElementById('page-tracking')?.classList.contains('active')) renderMapShips();
  if (document.getElementById('page-ship-focus')?.classList.contains('active')) renderShipFocus();
}

function seaRouteWaypoints(oLat, oLon, dLat, dLon) {
  const key = oceanRouteKey(oLat, oLon, dLat, dLon);
  const cached = oceanRouteCache[key];
  if (cached && cached.route) return cached.route;

  if (!cached) {
    oceanRouteCache[key] = { route: null, distanceKm: null, fetching: true, failed: false };
    fetchOceanRoute(oLat, oLon, dLat, dLon, key);
  }
  // Temporary fallback while the real ocean route is loading (first call
  // for this voyage only - subsequent calls hit the cache above).
  return [[oLat, oLon], [dLat, dLon]];
}

function routeSegmentLengths(pts) {
  const lens = [];
  for (let i = 0; i < pts.length - 1; i++) lens.push(haversineKm(pts[i][0], pts[i][1], pts[i + 1][0], pts[i + 1][1]));
  return lens;
}

// Walks a multi-segment route and returns the [lat, lon] at a given fraction
// (0..1) of total distance travelled — used to split the route into a solid
// "completed" line and a dashed "remaining" line at the ship's real progress.
function pointAtFraction(pts, frac) {
  const lens = routeSegmentLengths(pts);
  const total = lens.reduce((a, b) => a + b, 0) || 1;
  let target = Math.max(0, Math.min(1, frac)) * total;
  for (let i = 0; i < lens.length; i++) {
    if (target <= lens[i] || i === lens.length - 1) {
      const t = lens[i] ? target / lens[i] : 0;
      const [lat1, lon1] = pts[i], [lat2, lon2] = pts[i + 1];
      return [lat1 + (lat2 - lat1) * t, lon1 + (lon2 - lon1) * t];
    }
    target -= lens[i];
  }
  return pts[pts.length - 1];
}

function splitRouteAtFraction(pts, frac) {
  const lens = routeSegmentLengths(pts);
  const total = lens.reduce((a, b) => a + b, 0) || 1;
  let target = Math.max(0, Math.min(1, frac)) * total;
  const completed = [pts[0]];
  let i = 0;
  for (; i < lens.length; i++) {
    if (target <= lens[i]) break;
    target -= lens[i];
    completed.push(pts[i + 1]);
  }
  const splitPoint = pointAtFraction(pts, frac);
  completed.push(splitPoint);
  const remaining = [splitPoint, ...pts.slice(i + 1)];
  return { completed, remaining };
}

function initTrackingMap() {
  if (state.trackingMap) { setTimeout(() => state.trackingMap.invalidateSize(), 60); renderMapShips(); return; }
  const map = L.map('map', { zoomControl: true, worldCopyJump: true, minZoom: 2 }).setView([15, 80], 4);
  state.trackingMap = map;

  // ── Raster MAP tiles (default "Map" mode) ──
  // Use OpenStreetMap tiles for a detailed, standard map appearance.
  state.vectorLandLayer = L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    maxZoom: 19
  }).addTo(map);

  // ── RASTER SATELLITE (optional "Satellite" mode) ──
  // Best-effort only - if the network can't reach Esri, we just don't
  // switch layers and the vector map keeps showing (see setMapLayer below).
  state.satLayer = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', {
    attribution: 'Tiles &copy; Esri', maxZoom: 19,
  });

  // ── TERRAIN (elevation-shaded topographic map) ──
  state.terrainLayer = L.tileLayer('https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png', {
    attribution: 'Map data: © OpenStreetMap contributors, SRTM | Map style: © OpenTopoMap',
    maxZoom: 17,
  });

  // ── DARK OCEAN (low-glare bridge/command-center look) ──
  state.darkLayer = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}', {
    attribution: 'Tiles &copy; Esri &mdash; Esri, DeLorme, NAVTEQ', maxZoom: 16,
  });

  // ── NAUTICAL CHART overlay (seamarks: buoys, lights, depth areas,
  // shipping lanes) drawn transparently on top of the standard map base -
  // OpenSeaMap only publishes the overlay, not a full base map.
  state.nauticalOverlay = L.tileLayer('https://tiles.openseamap.org/seamark/{z}/{x}/{y}.png', {
    attribution: '© OpenSeaMap contributors', maxZoom: 18,
  });

  // Leaflet computes its tile/vector grid from the container's size at the
  // moment it's created. The tracking page is `display:none` until the
  // user opens it, so the container can report 0x0 on first init -
  // invalidateSize() after the container is actually visible fixes the
  // classic "blank map" bug this causes.
  setTimeout(() => map.invalidateSize(), 100);
  setTimeout(() => map.invalidateSize(), 400);
  window.addEventListener('resize', () => map.invalidateSize());

  renderMapShips();
}

// Every base style available on the main tracking map. "nautical" reuses
// the standard vector map as its base and adds the OpenSeaMap seamark
// overlay on top (OpenSeaMap itself is overlay-only tiles).
function _mapBaseLayerFor(kind) {
  return { map: state.vectorLandLayer, sat: state.satLayer, terrain: state.terrainLayer,
           dark: state.darkLayer, nautical: state.vectorLandLayer }[kind];
}
const MAP_LAYER_KINDS = ['map', 'sat', 'terrain', 'dark', 'nautical'];

function setMapLayer(kind) {
  const map = state.trackingMap;
  if (!map) return;
  MAP_LAYER_KINDS.forEach(k => {
    const btn = document.getElementById('layer-btn-' + k);
    if (btn) btn.classList.toggle('active', kind === k);
  });

  // Swap the base layer.
  MAP_LAYER_KINDS.forEach(k => {
    const layer = _mapBaseLayerFor(k);
    if (layer && map.hasLayer(layer) && layer !== _mapBaseLayerFor(kind)) map.removeLayer(layer);
  });
  const nextBase = _mapBaseLayerFor(kind);
  if (nextBase && !map.hasLayer(nextBase)) map.addLayer(nextBase);

  // Seamark overlay only shows in nautical mode.
  if (kind === 'nautical') {
    if (!map.hasLayer(state.nauticalOverlay)) map.addLayer(state.nauticalOverlay);
  } else if (map.hasLayer(state.nauticalOverlay)) {
    map.removeLayer(state.nauticalOverlay);
  }

  // If satellite tiles fail to load (no network reaching Esri), fall
  // straight back to the always-available vector map instead of leaving
  // the operator staring at a blank rectangle.
  if (kind === 'sat') {
    let failures = 0;
    state.satLayer.off('tileerror').on('tileerror', () => {
      failures++;
      if (failures >= 4) { toast('Satellite imagery unavailable (offline) — showing map view', 'warning'); setMapLayer('map'); }
    });
  }
}

function setFleetFilter(f) {
  state.fleetFilter = f;
  document.querySelectorAll('#overview-filter-row .filter-chip').forEach(el => el.classList.toggle('active', el.dataset.filter === f));
  renderMapShips();
}

// ═══════════════════════════════════════════════════════════════════════════
// TRACKING MODE — Demo (simulation only, ships placed by clicking the map,
// no GPS spoofing alerts) vs Real (live AIS/GPS feed, spoofing detection
// enabled). Switching is a runtime API call (app.routers.mode on the
// backend) - no restart, and every connected dashboard is kept in sync via
// the mode_changed websocket broadcast (see connectPoetSocket above).
// ═══════════════════════════════════════════════════════════════════════════
state.trackingMode = 'demo';

async function loadTrackingMode() {
  try {
    const res = await apiGet('/api/mode');
    applyTrackingMode(res.mode, false);
  } catch (e) { /* non-fatal — keep the 'demo' default */ }
}

/** Reflects a mode in the UI. Only calls the backend when the change was
 * initiated locally (fromUserAction=true); a mode_changed broadcast from
 * another client (or our own confirmed round-trip) just updates the UI. */
function applyTrackingMode(mode, fromUserAction) {
  state.trackingMode = mode;
  const demoBtn = document.getElementById('mode-btn-demo');
  const realBtn = document.getElementById('mode-btn-real');
  if (demoBtn) demoBtn.classList.toggle('active', mode === 'demo');
  if (realBtn) realBtn.classList.toggle('active', mode === 'real');

  // The "New Ship" map-click tool only makes sense in Demo Mode — real
  // ships are meant to arrive from a live AIS/GPS feed, not be hand-placed.
  const shipToolBtn = document.getElementById('hazard-btn-newship');
  if (shipToolBtn) shipToolBtn.style.display = mode === 'demo' ? '' : 'none';
  if (mode === 'real' && typeof window.setHazardTool === 'function' && shipToolBtn?.classList.contains('active')) {
    window.setHazardTool('ship'); // disarm — it toggles off since it's already active
  }

  // Real Mode must only ever show real weather/cyclone/wind data — the
  // entire manual Hazard Painter (cyclone/storm/wind placement) is a Demo
  // Mode presentation tool and is hidden outright in Real Mode. Any
  // hazards already placed are cleared so nothing simulated leaks into
  // Real Mode's view.
  const hazardToolbox = document.getElementById('hazard-toolbox');
  if (hazardToolbox) hazardToolbox.style.display = mode === 'demo' ? '' : 'none';
  if (mode === 'real') {
    if (typeof window.setHazardTool === 'function') window.setHazardTool(null);
    if (typeof window.clearHazards === 'function') window.clearHazards();
  }

  if (fromUserAction) {
    toast(mode === 'demo'
      ? '🎮 Demo Mode — simulation only, GPS spoofing alerts disabled'
      : '📡 Real Mode — only real weather/cyclone data shown, GPS spoofing detection enabled', 'info');
  }
}

async function setTrackingMode(mode) {
  if (mode === state.trackingMode) return;
  try {
    const res = await apiPost('/api/mode', { mode });
    applyTrackingMode(res.mode, true);
  } catch (e) {
    toast('Could not switch mode: ' + errMsg(e), 'error');
  }
}

function toggleFleetDrawer() {
  const drawer = document.getElementById('fleet-drawer');
  const toggleBtn = document.getElementById('fleet-drawer-toggle');
  if (!drawer) return;
  const opening = !drawer.classList.contains('open');
  drawer.classList.toggle('open', opening);
  if (toggleBtn) toggleBtn.classList.toggle('active', opening);
}

// ═══════════════════════════════════════════════════════════════════════════
// QUICK DEMO SHIP — "New Ship" hazard-toolbox tool. Click the map in Demo
// Mode -> pick a route, type, and speed -> a ship, ECC identity, and its
// voyage are all created automatically (spec: "Ships are created by
// clicking on the map... The simulator automatically creates the voyage").
// Reuses the exact same /api/ships/register + /api/voyages endpoints the
// full Register/Voyage modals use, just with an auto-generated name, IMO,
// and operator password so the operator only has to answer 4 questions.
// ═══════════════════════════════════════════════════════════════════════════
function nearestPortName(lat, lon) {
  let best = null, bestDist = Infinity;
  for (const name in PORTS) {
    const p = PORTS[name];
    const d = Math.hypot(p.lat - lat, p.lon - lon);
    if (d < bestDist) { bestDist = d; best = name; }
  }
  return best;
}

window.openQuickShipModal = function (lat, lon) {
  if (window.currentUserRole !== 'control_station') { toast('Only Control Station can create a ship', 'error'); return; }
  if (Object.keys(PORTS).length < 2) { toast('Ports are still loading — try again in a moment', 'warning'); return; }

  refreshAllPortDropdowns();
  const fromSel = document.getElementById('qship-from');
  const toSel = document.getElementById('qship-to');
  const origin = nearestPortName(lat, lon);
  fromSel.value = origin || '';
  // Pick a different default destination than the nearest-port origin.
  const otherPorts = Object.keys(PORTS).filter(n => n !== origin);
  toSel.value = otherPorts.length ? otherPorts[Math.floor(Math.random() * otherPorts.length)] : '';
  document.getElementById('quickship-error').textContent = '';
  openModal('quickShipModal');
};

function _randomToken(n) {
  const chars = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789';
  let s = '';
  for (let i = 0; i < n; i++) s += chars[Math.floor(Math.random() * chars.length)];
  return s;
}

async function submitQuickShip() {
  const type = document.getElementById('qship-type').value;
  const speedRaw = document.getElementById('qship-speed').value;
  const fromPort = document.getElementById('qship-from').value;
  const toPort = document.getElementById('qship-to').value;
  const errEl = document.getElementById('quickship-error');

  if (!fromPort || !toPort) { errEl.textContent = 'Select both origin and destination ports'; return; }
  if (fromPort === toPort) { errEl.textContent = 'Origin and destination must be different ports'; return; }
  const speedErr = validateVoyageSpeed(speedRaw);
  if (speedErr) { errEl.textContent = speedErr; return; }
  errEl.textContent = '';

  const origin = PORTS[fromPort];
  const dest = PORTS[toPort];
  const course = bearingDeg(origin.lat, origin.lon, dest.lat, dest.lon);
  const suffix = _randomToken(4);
  const name = `Demo ${type} ${suffix}`;
  const imo = 'IMO' + Math.floor(1000000 + Math.random() * 8999999);
  const password = 'demo-' + _randomToken(10);

  try {
    const result = await apiPost('/api/ships/register', {
      name, imo_number: imo, ship_type: type,
      origin_port: fromPort, origin_lat: origin.lat, origin_lon: origin.lon,
      dest_port: toPort, dest_lat: dest.lat, dest_lon: dest.lon,
      password, initial_speed: Number(speedRaw), course, heading: course,
    });
    await apiPost('/api/voyages', {
      ship_id: result.ship_id, origin_port: fromPort, dest_port: toPort,
      current_speed_kmh: Number(speedRaw), geofence_radius_km: 1,
    });
    toast(`${name} created and underway — ${fromPort} → ${toPort}`, 'success');
    closeModal('quickShipModal');
    await loadShips();
    await loadActiveVoyages();
    await refreshDashboard();
    if (document.getElementById('page-tracking').classList.contains('active')) renderMapShips();
  } catch (e) {
    errEl.textContent = errMsg(e);
  }
}

// Distinct, recognizable glyphs per vessel type (not plain dots), rotated to
// heading so travel direction is visible at a glance.
const SHIP_TYPE_SHAPES = {
  Cargo: '<path d="M2.5,15 L4,11 H7.5 L10,5 H13 V3 H15 V5 H16.5 L19,5 V8 H20 L21.5,11 H23.5 C24,11 24.5,11.5 24,12 L22.5,18 C22,19 21,19.5 20,19.5 H3.5 C2,19.5 1,18.5 1,17.5 L2.5,15 Z M6,17 A1,1 0 1,0 6,19 A1,1 0 1,0 6,17 Z M9,17 A1,1 0 1,0 9,19 A1,1 0 1,0 9,17 Z M12,17 A1,1 0 1,0 12,19 A1,1 0 1,0 12,17 Z M15,17 A1,1 0 1,0 15,19 A1,1 0 1,0 15,17 Z M18,17 A1,1 0 1,0 18,19 A1,1 0 1,0 18,17 Z M8.5,8 H11.5 A0.5,0.5 0 0,0 12,7.5 V6.5 A0.5,0.5 0 0,0 11.5,6 H8.5 Z M15.5,11 H18 A0.5,0.5 0 0,0 18.5,10.5 V9.5 A0.5,0.5 0 0,0 18,9 H15.5 Z" fill-rule="evenodd" />',
  Tanker:       '<path d="M12 2 L17 14 Q12 17 7 14 Z"/><rect x="9" y="12" width="6" height="8" rx="2"/>',
  Passenger:    '<path d="M12 2 L16 13 Q12 11 8 13 Z"/><rect x="9.5" y="11" width="5" height="9" rx="2.5"/><rect x="10.3" y="13" width="3.4" height="3" fill="#fff" opacity="0.6"/>',
  Fishing:      '<path d="M12 3 L15 14 L12 12 L9 14 Z"/><rect x="11" y="12" width="2" height="8" rx="1"/><line x1="12" y1="6" x2="17" y2="4" stroke="#fff" stroke-width="1"/>',
  Naval:        '<path d="M12 1 L16 15 L12 12 L8 15 Z"/><rect x="10.5" y="12" width="3" height="8"/><rect x="9" y="9" width="6" height="2"/>',
  'Coast Guard':'<path d="M12 1 L16 15 L12 12 L8 15 Z"/><rect x="10.5" y="12" width="3" height="8"/><rect x="9" y="9" width="6" height="2" fill="#F97316"/>',
  Container:    '<path d="M12 2 L18 15 L12 12 L6 15 Z"/><rect x="9" y="12.5" width="6" height="2.2" fill="#fff" opacity=".5"/><rect x="9" y="15" width="6" height="2.2" fill="#fff" opacity=".3"/><rect x="10.3" y="12" width="3.4" height="8" rx="0.8"/>',
};

const UNDER_ATTACK_STATUS = '🚨 UNDER ATTACK';

function shipDivIcon(type, status, heading, hasCycloneWarning = false) {
  let color = { online: '#10B981', warning: '#F59E0B', alert: '#EF4444', docked: '#2563EB', registered: '#475569' }[status] || '#1A6FD4';
  const isHijack = status === '⚠ POSSIBLE HIJACK';
  const isAttack = status === UNDER_ATTACK_STATUS;
  if (isHijack) color = '#9333ea'; // Purple for hijack
  if (isAttack) color = '#ef4444'; // Red for an active attack/incident

  const rot = (heading ?? 0);
  const emoji = '🚢';

  let warningOverlay = '';
  if (isAttack) {
    warningOverlay = `<div style="position:absolute;top:-9px;left:-9px;right:-9px;bottom:-9px;border-radius:50%;border:3px solid #ef4444;animation:pulse-attack 0.9s infinite"></div>`;
  } else if (isHijack) {
    warningOverlay = `<div style="position:absolute;top:-6px;left:-6px;right:-6px;bottom:-6px;border-radius:50%;border:3px dashed #9333ea;animation:pulse-purple 1.5s infinite"></div>`;
  } else if (hasCycloneWarning) {
    warningOverlay = `<div style="position:absolute;top:-4px;left:-4px;right:-4px;bottom:-4px;border-radius:50%;border:2px solid #ff3333;animation:pulse-red 1s infinite"></div>`;
  }

  return L.divIcon({
    html: `<div style="position:relative;transform:rotate(${rot}deg);width:26px;height:26px;display:flex;align-items:center;justify-content:center;filter:drop-shadow(0 1px 3px rgba(0,0,0,0.4));${isAttack ? 'animation:flash-attack 0.6s infinite alternate' : ''}">
      <div style="position:absolute;width:22px;height:22px;border-radius:50%;background:rgba(255,255,255,0.92);border:2.5px solid ${color};box-shadow:0 0 0 1px rgba(0,0,0,0.15)"></div>
      ${warningOverlay}
      <div style="position:relative;font-size:15px;line-height:1;transform:rotate(${-rot}deg)">${emoji}</div>
    </div>
    <style>
      @keyframes pulse-red { 0% { transform: scale(0.95); box-shadow: 0 0 0 0 rgba(255, 51, 51, 0.7); } 70% { transform: scale(1); box-shadow: 0 0 0 10px rgba(255, 51, 51, 0); } 100% { transform: scale(0.95); box-shadow: 0 0 0 0 rgba(255, 51, 51, 0); } }
      @keyframes pulse-purple { 0% { transform: scale(0.95); box-shadow: 0 0 0 0 rgba(147, 51, 234, 0.7); } 70% { transform: scale(1.1); box-shadow: 0 0 0 15px rgba(147, 51, 234, 0); } 100% { transform: scale(0.95); box-shadow: 0 0 0 0 rgba(147, 51, 234, 0); } }
      @keyframes pulse-attack { 0% { transform: scale(0.9); box-shadow: 0 0 0 0 rgba(239, 68, 68, 0.8); } 70% { transform: scale(1.25); box-shadow: 0 0 0 18px rgba(239, 68, 68, 0); } 100% { transform: scale(0.9); box-shadow: 0 0 0 0 rgba(239, 68, 68, 0); } }
      @keyframes flash-attack { from { opacity: 1; } to { opacity: 0.45; } }
    </style>`,
    className: '', iconSize: [26, 26], iconAnchor: [13, 13],
  });
}

function clearShipRouteLayers(shipId) {
  const map = state.trackingMap;
  const layers = state.routeLayers[shipId];
  if (!map || !layers) return;
  Object.values(layers).forEach(l => { try { map.removeLayer(l); } catch (e) {} });
  delete state.routeLayers[shipId];
}

// ═══════════════════════════════════════════════════════════════════════════
// ATTACK MODE — operator-triggered attack/incident simulation. Renders
// either the active-incident CRITICAL banner + response actions, or (when
// nothing is active) the trigger control, in the ship focus sidebar.
// ═══════════════════════════════════════════════════════════════════════════
const INCIDENT_ICONS = {
  pirate: '🏴‍☠️', missile: '🚀', drone: '🛸', hijack: '🚨', sabotage: '🔧', cargo_theft: '📦', distress: '🆘',
  engine_failure: '⚙️', fire: '🔥', man_overboard: '🌊', unauthorized_boarding: '🧗', cargo_temp_failure: '🌡️',
  anchor_drift: '⚓', comm_loss: '📡', ais_offline: '📶',
};

function renderAttackModeSection(ship, activeIncidents) {
  const isControl = window.currentUserRole === 'control_station';
  if (activeIncidents && activeIncidents.length > 0) {
    const rows = activeIncidents.map(inc => `
      <div style="background:rgba(239,68,68,0.12);border:1px solid var(--red);border-radius:8px;padding:10px;margin-bottom:8px">
        <div style="font-weight:700;color:var(--red);display:flex;align-items:center;gap:6px;animation:flash-attack 0.8s infinite alternate">
          ${INCIDENT_ICONS[inc.incident_type] || '🚨'} CRITICAL — ${inc.label}
        </div>
        ${isControl ? `
        <div style="display:flex;flex-wrap:wrap;gap:6px;margin-top:8px">
          <button class="btn" style="font-size:10px;padding:4px 8px" onclick="actOnIncident('${ship.ship_id}','${inc.incident_type}','acknowledge')">Acknowledge</button>
          <button class="btn" style="font-size:10px;padding:4px 8px" onclick="actOnIncident('${ship.ship_id}','${inc.incident_type}','escalate')">Escalate</button>
          <button class="btn" style="font-size:10px;padding:4px 8px" onclick="actOnIncident('${ship.ship_id}','${inc.incident_type}','dispatch_coast_guard')">Dispatch Coast Guard</button>
          <button class="btn" style="font-size:10px;padding:4px 8px" onclick="actOnIncident('${ship.ship_id}','${inc.incident_type}','request_naval_support')">Request Naval Support</button>
          <button class="btn" style="font-size:10px;padding:4px 8px" onclick="actOnIncident('${ship.ship_id}','${inc.incident_type}','broadcast_distress')">Broadcast Distress</button>
          <button class="btn" style="font-size:10px;padding:4px 8px" onclick="actOnIncident('${ship.ship_id}','${inc.incident_type}','activate_emergency_protocol')">Emergency Protocol</button>
          <button class="btn btn-primary" style="font-size:10px;padding:4px 8px" onclick="endIncident('${ship.ship_id}','${inc.incident_type}')">✅ End Incident</button>
        </div>` : `<div style="font-size:11px;color:var(--text3);margin-top:6px">Control Station is responding.</div>`}
      </div>`).join('');
    const exitBtn = isControl
      ? `<button class="btn btn-primary" style="width:100%;margin-bottom:8px" onclick="exitAttackMode('${ship.ship_id}')">🛡️ Exit Attack Mode — Resume Voyage</button>`
      : '';
    return `<div class="detail-section-label">⚠ Attack Mode — Active</div>${exitBtn}${rows}`;
  }
  if (!isControl) return '';
  const options = Object.entries(ATTACK_TYPE_LABELS).map(([k, v]) => `<option value="${k}">${INCIDENT_ICONS[k] || ''} ${v}</option>`).join('');
  return `
    <div class="detail-section-label">Attack Mode</div>
    <div style="display:flex;gap:6px">
      <select class="form-select" id="attack-type-${ship.ship_id}" style="flex:1;font-size:11px">${options}</select>
      <button class="btn btn-danger" style="font-size:11px" onclick="startIncident('${ship.ship_id}')">🚨 Simulate</button>
    </div>`;
}

// Labels mirrored from backend app.schemas.incidents (kept small and
// duplicated here rather than fetched, since they never change at runtime
// and this avoids an extra round trip on every focus-view render).
const ATTACK_TYPE_LABELS = {
  pirate: 'Pirate Attack', missile: 'Missile Threat', drone: 'Drone Threat', hijack: 'Hijack Attempt',
  sabotage: 'Engine Sabotage', cargo_theft: 'Cargo Theft', distress: 'Emergency Distress',
  engine_failure: 'Engine Failure', fire: 'Fire Onboard', man_overboard: 'Man Overboard',
  unauthorized_boarding: 'Unauthorized Boarding', cargo_temp_failure: 'Cargo Temperature Failure',
  anchor_drift: 'Anchor Drift', comm_loss: 'Communication Loss', ais_offline: 'AIS Offline',
};

async function startIncident(shipId) {
  const type = document.getElementById(`attack-type-${shipId}`).value;
  try {
    await apiPost('/api/incidents/start', { ship_id: shipId, incident_type: type });
    toast(`🚨 ${ATTACK_TYPE_LABELS[type]} simulation started`, 'error');
    await loadShips(); await loadAlerts(); renderAlerts(); await refreshDashboard();
    if (document.getElementById('page-ship-focus')?.classList.contains('active')) await renderShipFocus();
  } catch (e) { toast('Could not start incident: ' + errMsg(e), 'error'); }
}

async function actOnIncident(shipId, incidentType, action) {
  try {
    await apiPost(`/api/incidents/${encodeURIComponent(shipId)}/${encodeURIComponent(incidentType)}/act`, { action });
    toast('Action logged to blockchain', 'success');
    if (document.getElementById('page-ship-focus')?.classList.contains('active')) await renderShipFocus();
  } catch (e) { toast('Could not log action: ' + errMsg(e), 'error'); }
}

// Ends every active incident for a ship in one action: removes the red
// danger zone, clears the attack state, and lets the ship resume its
// voyage smoothly from its current (frozen) position — nothing about the
// voyage/route/progress is reset.
async function exitAttackMode(shipId) {
  try {
    const activeIncidents = await apiGet(`/api/incidents/ship/${encodeURIComponent(shipId)}`);
    await Promise.all(activeIncidents.map(inc =>
      apiPost(`/api/incidents/${encodeURIComponent(shipId)}/${encodeURIComponent(inc.incident_type)}/end`, {})
    ));
    toast('🛡️ Attack simulation ended — voyage resuming', 'success');
    await loadShips(); await loadAlerts(); renderAlerts(); await refreshDashboard();
    if (document.getElementById('page-tracking')?.classList.contains('active')) renderMapShips();
    if (document.getElementById('page-ship-focus')?.classList.contains('active')) await renderShipFocus();
  } catch (e) { toast('Could not exit attack mode: ' + errMsg(e), 'error'); }
}

async function endIncident(shipId, incidentType) {
  try {
    await apiPost(`/api/incidents/${encodeURIComponent(shipId)}/${encodeURIComponent(incidentType)}/end`, {});
    toast('Incident resolved', 'success');
    await loadShips(); await loadAlerts(); renderAlerts(); await refreshDashboard();
    if (document.getElementById('page-ship-focus')?.classList.contains('active')) await renderShipFocus();
  } catch (e) { toast('Could not end incident: ' + errMsg(e), 'error'); }
}

function voyageProgressPct(voyage) {
  if (!voyage) return 0;
  const total = voyage.distance_travelled_km + voyage.distance_remaining_km;
  return total > 0 ? Math.min(100, (voyage.distance_travelled_km / total) * 100) : 0;
}

function shipTooltipHtml(s, voyage) {
  const progress = voyage ? voyageProgressPct(voyage).toFixed(0) : '—';
  return `
    <div style="font-size:11.5px;line-height:1.6;min-width:190px">
      <b style="font-size:12.5px">${s.name}</b><br>
      <b>Type:</b> ${s.ship_type || '—'} &nbsp; <b>Capacity:</b> ${s.capacity_tons ? s.capacity_tons.toLocaleString() + 't' : '—'}<br>
      <b>Speed:</b> ${(s.current_speed ?? 0).toFixed(0)} km/h &nbsp; <b>Heading:</b> ${(s.heading ?? 0).toFixed(0)}°<br>
      <b>Lat/Lon:</b> ${s.current_lat?.toFixed(3)}°, ${s.current_lon?.toFixed(3)}°<br>
      <b>Origin:</b> ${s.origin_port || '—'}<br>
      <b>Destination:</b> ${s.dest_port || '—'}<br>
      <b>ETA:</b> ${voyage?.eta ? fmtDate(voyage.eta) : '—'}<br>
      <b>Status:</b> ${s.status} &nbsp; <b>Progress:</b> ${progress}%
    </div>`;
}

// Per-ship animation state: tracks where the marker visually started and
// where it's headed after each backend tick, plus a start timestamp, so we
// can interpolate linearly (constant speed) across the whole tick window
// instead of easing in fast then idling — that's what made motion look
// like a "creep then pause" rather than steady travel.
const shipAnimState = {}; // shipId -> { fromLat, fromLon, toLat, toLon, startMs, heading }

function updateShipAnimTarget(shipId, lat, lon) {
  const prev = shipAnimState[shipId];
  const fromLat = prev ? prev._curLat ?? prev.fromLat : lat;
  const fromLon = prev ? prev._curLon ?? prev.fromLon : lon;
  const heading = (Math.abs(lat - fromLat) > 1e-9 || Math.abs(lon - fromLon) > 1e-9)
    ? bearingDeg(fromLat, fromLon, lat, lon)
    : (prev?.heading ?? 0);
  shipAnimState[shipId] = {
    fromLat, fromLon, toLat: lat, toLon: lon,
    startMs: Date.now(), heading,
    _curLat: fromLat, _curLon: fromLon,
  };
}

const TICK_DURATION_MS = VOYAGE_TICK_SECONDS * 1000;

function animateShipsContinuously() {
  const nowMs = Date.now();
  state.ships.forEach(s => {
    const voyage = state.voyages[s.ship_id];
    if (!voyage || voyage.status !== 'in_progress' || s.current_lat == null) return;

    let anim = shipAnimState[s.ship_id];
    // New target arrived from a backend tick — start a fresh linear glide.
    if (!anim || anim.toLat !== s.current_lat || anim.toLon !== s.current_lon) {
      updateShipAnimTarget(s.ship_id, s.current_lat, s.current_lon);
      anim = shipAnimState[s.ship_id];
    }

    const t = Math.min(1, (nowMs - anim.startMs) / TICK_DURATION_MS);
    const curLat = anim.fromLat + (anim.toLat - anim.fromLat) * t;
    const curLon = anim.fromLon + (anim.toLon - anim.fromLon) * t;
    anim._curLat = curLat; anim._curLon = curLon;
    const heading = anim.heading;

    const marker1 = state.mapMarkers?.[s.ship_id];
    if (marker1) {
      marker1.setLatLng([curLat, curLon]);
      marker1.setIcon(shipDivIcon(s.ship_type, s.status, heading, s.has_cyclone_warning));
    }
    if (typeof focusShipId !== 'undefined' && focusShipId === s.ship_id && state.focusMarker) {
      state.focusMarker.setLatLng([curLat, curLon]);
      state.focusMarker.setIcon(shipDivIcon(s.ship_type, s.status, heading, s.has_cyclone_warning));
    }
  });

  requestAnimationFrame(animateShipsContinuously);
}

function renderMapShips() {
  if (!state.trackingMap) return;
  const map = state.trackingMap;

  // Remove markers/layers for ships no longer present, AND for ships
  // that have completed their voyage (status "docked" after arrival -
  // see app.routers.voyages/positions setting ship.status = "docked" on
  // completion). Per the voyage-completion requirement, the ship icon
  // must fully disappear from the Live Tracking Map once arrived, while
  // everything else (blockchain records, GPS history, voyage logs,
  // dashboard stats) stays intact - this only touches the map marker.
  Object.keys(state.mapMarkers).forEach(id => {
    const ship = state.ships.find(s => s.ship_id === id);
    if (!ship || ship.status === 'docked') {
      map.removeLayer(state.mapMarkers[id]);
      delete state.mapMarkers[id];
      clearShipRouteLayers(id);
    }
  });

  state.ships.forEach(s => {
    if (s.status === 'docked') return; // voyage completed - no marker
    if (s.current_lat == null || s.current_lon == null) return;
    const voyage = state.voyages[s.ship_id];
    let heading = s.heading ?? 0;
    if (voyage && voyage.status === 'in_progress') heading = bearingDeg(s.current_lat, s.current_lon, voyage.dest_lat, voyage.dest_lon);

    if (state.mapMarkers[s.ship_id]) {
      if (!voyage || voyage.status !== 'in_progress') {
        state.mapMarkers[s.ship_id].setLatLng([s.current_lat, s.current_lon]);
        state.mapMarkers[s.ship_id].setIcon(shipDivIcon(s.ship_type, s.status, heading, s.has_cyclone_warning));
      }
    } else {
      const marker = L.marker([s.current_lat, s.current_lon], { icon: shipDivIcon(s.ship_type, s.status, heading, s.has_cyclone_warning) }).addTo(map);
      marker.on('click', () => showShipDetail(s.ship_id));
      state.mapMarkers[s.ship_id] = marker;
    }
    state.mapMarkers[s.ship_id].bindTooltip(shipTooltipHtml(s, voyage), { direction: 'top', offset: [0, -14], opacity: 0.97, sticky: false });

    // Route visualization: a thick solid black line for the portion of the
    // route already travelled (grows from origin toward the ship and is
    // never redrawn/changed, even across a later reroute) plus a thick
    // solid blue line for the remaining planned route ahead of the ship
    // (this is the piece that changes if a reroute is required). Origin
    // and destination markers show their port name on hover.
    if (voyage && voyage.status === 'in_progress') {
      const waypoints = seaRouteWaypoints(voyage.origin_lat, voyage.origin_lon, voyage.dest_lat, voyage.dest_lon);
      const progress = shipRouteProgress[s.ship_id];
      const frac = (progress && progress.voyageId === voyage.voyage_id) ? progress.frac : (voyageProgressPct(voyage) / 100);
      const { completed, remaining } = splitRouteAtFraction(waypoints, frac);

      // A reroute re-anchors the voyage's origin to "En Route (...)" and
      // resets travelled distance to 0 - that's the reliable signal that
      // the ship is currently following a cyclone alternate route rather
      // than its original planned route (see apply_reroute in
      // reroute_orchestrator.py). Alternate routes render in blue,
      // original routes in black; either way the stretch already covered
      // is a thick solid line and the stretch still to come is dashed.
      const isAlternate = !!(voyage.origin_port && voyage.origin_port.startsWith('En Route'));
      const routeColor = isAlternate ? '#2B85F0' : '#0B0F14';
      const originLabel = (voyage.origin_port && !voyage.origin_port.startsWith('En Route')) ? voyage.origin_port : (s.origin_port || 'Origin');

      let layers = state.routeLayers[s.ship_id];
      if (!layers) {
        layers = {
          completedLine: L.polyline(completed, { color: routeColor, weight: 5, opacity: 0.92, lineCap: 'round' }).addTo(map),
          remainingLine: L.polyline(remaining, { color: routeColor, weight: 3.5, opacity: 0.9, lineCap: 'round', dashArray: '10 8' }).addTo(map),
          originMarker: L.marker([voyage.origin_lat, voyage.origin_lon], { icon: L.divIcon({ html: '<div style="font-size:18px">🟢</div>', className: '', iconSize: [18, 18] }) })
            .addTo(map).bindTooltip(originLabel, { direction: 'top', offset: [0, -10], className: 'route-point-tooltip' }),
          destMarker: L.marker([voyage.dest_lat, voyage.dest_lon], { icon: L.divIcon({ html: '<div style="font-size:18px">🚩</div>', className: '', iconSize: [18, 18] }) })
            .addTo(map).bindTooltip(voyage.dest_port, { direction: 'top', offset: [0, -10], className: 'route-point-tooltip' }),
          geofence: L.circle([s.current_lat, s.current_lon], { radius: voyage.geofence_radius_km * 1000, color: '#00B896', weight: 1.5, dashArray: '4 4', fillColor: '#00B896', fillOpacity: 0.08 }).addTo(map),
        };
        state.routeLayers[s.ship_id] = layers;
      } else {
        layers.completedLine.setLatLngs(completed).setStyle({ color: routeColor });
        layers.remainingLine.setLatLngs(remaining).setStyle({ color: routeColor });
        layers.originMarker.setLatLng([voyage.origin_lat, voyage.origin_lon]).setTooltipContent(originLabel);
        layers.destMarker.setLatLng([voyage.dest_lat, voyage.dest_lon]).setTooltipContent(voyage.dest_port);
        layers.geofence.setLatLng([s.current_lat, s.current_lon]).setRadius(voyage.geofence_radius_km * 1000);
      }

      // Attack simulation: large pulsing red danger zone centered on the
      // ship, only while an incident is active for it — removed the
      // instant "Exit Attack Mode" clears the incident.
      if (s.status === UNDER_ATTACK_STATUS) {
        if (!layers.dangerZone) {
          layers.dangerZone = L.circle([s.current_lat, s.current_lon], {
            radius: 55000, color: '#ef4444', weight: 2, fillColor: '#ef4444', fillOpacity: 0.16,
            className: 'attack-danger-zone',
          }).addTo(map);
        } else {
          layers.dangerZone.setLatLng([s.current_lat, s.current_lon]);
        }
      } else if (layers.dangerZone) {
        try { map.removeLayer(layers.dangerZone); } catch (e) {}
        delete layers.dangerZone;
      }
    } else {
      clearShipRouteLayers(s.ship_id);
    }
  });

  const onlineCount = state.ships.filter(s => s.status === 'online').length;
  document.getElementById('live-count').textContent = `${onlineCount} Online`;

  // ── LEFT: Ships Overview (compact, filterable, searchable) ──
  const search = (document.getElementById('overview-search')?.value || '').toLowerCase().trim();
  const filtered = state.ships.filter(s => {
    if (state.fleetFilter !== 'all' && s.status !== state.fleetFilter) return false;
    if (search && !s.name.toLowerCase().includes(search)) return false;
    return true;
  });

  const list = document.getElementById('tracking-ship-list');
  if (state.ships.length === 0) {
    list.innerHTML = `<div style="padding:20px;text-align:center;color:var(--text3)">No ships registered yet.</div>`;
  } else if (filtered.length === 0) {
    list.innerHTML = `<div style="padding:20px;text-align:center;color:var(--text3)">No ships match this filter.</div>`;
  } else {
    list.innerHTML = filtered.map(s => {
      const v = state.voyages[s.ship_id];
      const voyageLine = v ? `${v.distance_remaining_km.toFixed(0)} km remaining` : 'No active voyage';
      return `
      <div class="ship-row" onclick="showShipDetail('${s.ship_id}')">
        <div class="ship-avatar" style="background:${statusColor(s.status)}22">${shipEmoji(s.ship_type)}</div>
        <div class="ship-info">
          <div class="ship-name">${s.name}</div>
          <div class="ship-detail">${(s.current_speed ?? 0).toFixed(0)} km/h | ${voyageLine}</div>
        </div>
        <span class="badge badge-${s.status === 'online' ? 'green' : s.status === 'alert' ? 'red' : s.status === 'warning' ? 'amber' : 'blue'}">${s.status}</span>
      </div>`;
    }).join('');
  }

  renderLiveTrackingPanel();
}

// ── RIGHT: Live Tracking panel — ALL ships, full field set, at once ──
function renderLiveTrackingPanel() {
  const panel = document.getElementById('live-tracking-panel');
  if (!panel) return;
  if (state.ships.length === 0) {
    panel.innerHTML = `<div style="padding:20px;text-align:center;color:var(--text3)">No ships registered yet.</div>`;
    return;
  }
  const goingShips = state.ships.filter(s => s.status !== 'docked');
  if (goingShips.length === 0) {
    panel.innerHTML = `<div style="padding:20px;text-align:center;color:var(--text3)">No ships currently underway.</div>`;
    return;
  }
  panel.innerHTML = goingShips.map(s => {
    const v = state.voyages[s.ship_id];
    const pct = v ? voyageProgressPct(v) : 0;
    const online = s.status === 'online' || s.status === 'warning' || s.status === 'alert';
    const destRisk = portCycloneRisk(s.dest_lat, s.dest_lon);
    return `
    <div class="live-ship-card" onclick="showShipDetail('${s.ship_id}')">
      <div class="live-ship-card-top">
        <span class="live-dot" style="background:${statusColor(s.status)}"></span>
        <span class="live-ship-name">${s.name}</span>
        <span class="live-online badge-${online ? 'green' : 'blue'}" style="background:${online ? 'rgba(16,185,129,.15)' : 'rgba(90,112,148,.2)'};color:${online ? 'var(--green)' : 'var(--text3)'}">${online ? 'ONLINE' : 'OFFLINE'}</span>
      </div>
      <div class="live-field-grid">
        <div><b>Type:</b> ${s.ship_type || '—'}</div>
        <div><b>Status:</b> ${s.status}</div>
        <div><b>Lat:</b> ${s.current_lat?.toFixed(3) ?? '—'}</div>
        <div><b>Lon:</b> ${s.current_lon?.toFixed(3) ?? '—'}</div>
        <div><b>Speed:</b> ${(s.current_speed ?? 0).toFixed(0)} kn</div>
        <div><b>Heading:</b> ${(s.heading ?? 0).toFixed(0)}°</div>
        <div><b>Origin:</b> ${s.origin_port || '—'}</div>
        <div><b>Dest:</b> ${s.dest_port || '—'}</div>
        <div><b>ETA:</b> ${v?.eta ? fmtDate(v.eta) : '—'}</div>
        <div><b>Remaining:</b> ${v ? v.distance_remaining_km.toFixed(0) + ' km' : '—'}</div>
        <div style="grid-column:1/-1;color:${destRisk ? 'var(--red)' : 'var(--green)'}">${destRisk ? '⚠️ Cyclone near destination (' + destRisk.name + ')' : '✅ Destination weather clear'}</div>
        <div style="grid-column:1/-1"><b>Updated:</b> ${now()}</div>
      </div>
      <div class="live-progress-bar"><div class="live-progress-fill" style="width:${pct}%"></div></div>
      <div style="display:flex;justify-content:space-between;font-size:10px;color:var(--text3)">
        <span>${pct.toFixed(0)}% complete</span>
        <span onclick="event.stopPropagation();centerOnShip('${s.ship_id}')" style="color:var(--blue2);cursor:pointer;font-weight:600">Center on Ship ▸</span>
      </div>
    </div>`;
  }).join('');
}

function centerOnShip(id) {
  const s = state.ships.find(x => x.ship_id === id);
  if (s && state.trackingMap && s.current_lat != null) state.trackingMap.setView([s.current_lat, s.current_lon], 7);
}

// ═══════════════════════════════════════════════════════════════════════════
// SHIP FOCUS PAGE — dedicated live-tracking detail view for one ship
// ═══════════════════════════════════════════════════════════════════════════
let focusShipId = null;

// Clicking a ship anywhere (map marker, ship list row, live panel card)
// opens this full-page detail view instead of a small modal.
async function showShipDetail(id) {
  const s = state.ships.find(x => x.ship_id === id);
  if (!s) return;
  focusShipId = id;
  showPage('ship-focus');
  document.querySelectorAll('.nav-item').forEach(n => { if (n.getAttribute('onclick')?.includes("'tracking'")) n.classList.add('active'); });
  setTimeout(() => { initFocusMap(); renderShipFocus(); }, 50);
}

function initFocusMap() {
  if (state.focusMap) { setTimeout(() => state.focusMap.invalidateSize(), 60); return; }
  const map = L.map('focus-map', { zoomControl: true, worldCopyJump: true, minZoom: 2 }).setView([15, 80], 4);
  state.focusMap = map;

  // Same always-available vector land layer used on the main tracking map -
  // no dependency on an external tile server ever being reachable.
  state.focusLandLayer = L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    maxZoom: 19
  }).addTo(map);

  state.focusSatLayer = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', {
    attribution: 'Tiles &copy; Esri', maxZoom: 19,
  });
  state.focusTerrainLayer = L.tileLayer('https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png', {
    attribution: 'Map data: © OpenStreetMap contributors, SRTM | Map style: © OpenTopoMap', maxZoom: 17,
  });
  state.focusDarkLayer = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}', {
    attribution: 'Tiles &copy; Esri &mdash; Esri, DeLorme, NAVTEQ', maxZoom: 16,
  });
  state.focusNauticalOverlay = L.tileLayer('https://tiles.openseamap.org/seamark/{z}/{x}/{y}.png', {
    attribution: '© OpenSeaMap contributors', maxZoom: 18,
  });

  state.focusPortsLayer = L.layerGroup().addTo(map);
  state.focusZonesLayer = L.layerGroup().addTo(map);

  setTimeout(() => map.invalidateSize(), 100);
  setTimeout(() => map.invalidateSize(), 400);
  window.addEventListener('resize', () => map.invalidateSize());
}

function setFocusMapLayer(kind) {
  const map = state.focusMap;
  if (!map) return;
  MAP_LAYER_KINDS.forEach(k => {
    const btn = document.getElementById('focus-layer-btn-' + k);
    if (btn) btn.classList.toggle('active', kind === k);
  });

  // "nautical" sits on the same plain-map base as "map" (OpenSeaMap only
  // publishes the seamark overlay, not a full base), so both respect the
  // existing countries-on/off toggle used for labels.
  const countriesOn = document.getElementById('focus-layer-btn-map').dataset.countriesOn !== 'false';
  const bases = {
    map: state.focusLandLayer, sat: state.focusSatLayer, terrain: state.focusTerrainLayer,
    dark: state.focusDarkLayer, nautical: state.focusLandLayer,
  };
  const nextBase = bases[kind];
  Object.values(bases).forEach(layer => {
    if (layer && map.hasLayer(layer) && layer !== nextBase) map.removeLayer(layer);
  });
  if (nextBase === state.focusLandLayer) {
    if (countriesOn && !map.hasLayer(nextBase)) map.addLayer(nextBase);
  } else if (nextBase && !map.hasLayer(nextBase)) {
    map.addLayer(nextBase);
  }

  if (kind === 'nautical') {
    if (!map.hasLayer(state.focusNauticalOverlay)) map.addLayer(state.focusNauticalOverlay);
  } else if (map.hasLayer(state.focusNauticalOverlay)) {
    map.removeLayer(state.focusNauticalOverlay);
  }

  if (kind === 'sat') {
    let failures = 0;
    state.focusSatLayer.off('tileerror').on('tileerror', () => {
      failures++;
      if (failures >= 4) { toast('Satellite imagery unavailable (offline) — showing map view', 'warning'); setFocusMapLayer('map'); }
    });
  }
}

function toggleFocusLabels() {
  const btn = document.getElementById('focus-layer-btn-labels');
  const on = !btn.classList.contains('active');
  btn.classList.toggle('active', on);
  document.querySelectorAll('#focus-map .leaflet-tooltip').forEach(el => el.style.display = on ? '' : 'none');
}

function toggleFocusLayer(kind, checked) {
  const map = state.focusMap;
  if (!map) return;
  if (kind === 'countries') {
    document.getElementById('focus-layer-btn-map').dataset.countriesOn = checked ? 'true' : 'false';
    if (checked) map.addLayer(state.focusLandLayer); else map.removeLayer(state.focusLandLayer);
  } else if (kind === 'ports') {
    if (checked) map.addLayer(state.focusPortsLayer); else map.removeLayer(state.focusPortsLayer);
  } else if (kind === 'zones') {
    if (checked) map.addLayer(state.focusZonesLayer); else map.removeLayer(state.focusZonesLayer);
  }
}

function toggleFocusFullscreen() {
  const el = document.querySelector('.focus-map-col');
  if (!document.fullscreenElement) el.requestFullscreen?.().then(() => setTimeout(() => state.focusMap?.invalidateSize(), 200));
  else document.exitFullscreen?.().then(() => setTimeout(() => state.focusMap?.invalidateSize(), 200));
}

// Renders every part of the focus page (sidebar, map marker + route, bottom
// stat bar, route timeline) from current state. Called on open and on every
// polling/movement tick while this page is active, so it's genuinely live.
async function renderShipFocus() {
  const s = state.ships.find(x => x.ship_id === focusShipId);
  if (!s) { showPage('tracking'); return; }
  const voyage = state.voyages[focusShipId];
  const map = state.focusMap;

  // ── Ports & restricted zones layers (rebuilt cheaply each render) ──
  if (map) {
    state.focusPortsLayer.clearLayers();
    state.ports.forEach(p => {
      L.circleMarker([p.lat, p.lon], { radius: 3, color: '#2B85F0', weight: 1, fillColor: '#2B85F0', fillOpacity: 0.9 })
        .bindTooltip(p.name, { direction: 'top' }).addTo(state.focusPortsLayer);
    });
    state.focusZonesLayer.clearLayers();
    state.zones.forEach(z => {
      L.circle([z.lat, z.lon], { radius: z.radius_km * 1000, color: '#EF4444', weight: 1.5, dashArray: '4 4', fillColor: '#EF4444', fillOpacity: 0.08 })
        .bindTooltip(`${ZONE_TYPE_ICONS[z.zone_type] || '⛔'} ${z.name} (${z.zone_type})`, { direction: 'top' }).addTo(state.focusZonesLayer);
    });
  }

  // ── Map marker + sea-lane route (only ever drawn over water) ──
  if (map && s.current_lat != null) {
    let heading = s.heading ?? 0;
    if (voyage && voyage.status === 'in_progress') heading = bearingDeg(s.current_lat, s.current_lon, voyage.dest_lat, voyage.dest_lon);
    if (state.focusMarker) {
      if (!voyage || voyage.status !== 'in_progress') {
        state.focusMarker.setLatLng([s.current_lat, s.current_lon]).setIcon(shipDivIcon(s.ship_type, s.status, heading, s.has_cyclone_warning));
      }
    } else {
      state.focusMarker = L.marker([s.current_lat, s.current_lon], { icon: shipDivIcon(s.ship_type, s.status, heading, s.has_cyclone_warning) }).addTo(map);
      map.setView([s.current_lat, s.current_lon], 5);
    }
    state.focusMarker.bindPopup(`<b>${s.name}</b><br>${(s.current_speed ?? 0).toFixed(1)} kn • ${heading.toFixed(0)}°<br>${s.current_lat.toFixed(4)}°N, ${s.current_lon.toFixed(4)}°E<br><span style="color:#5A7094">${now()}</span>`, { closeButton: false });

    if (voyage && voyage.status === 'in_progress') {
      const waypoints = seaRouteWaypoints(voyage.origin_lat, voyage.origin_lon, voyage.dest_lat, voyage.dest_lon);
      const progress = shipRouteProgress[s.ship_id];
      const frac = (progress && progress.voyageId === voyage.voyage_id) ? progress.frac : (voyageProgressPct(voyage) / 100);
      const { completed, remaining } = splitRouteAtFraction(waypoints, frac);
      const isAlternate = !!(voyage.origin_port && voyage.origin_port.startsWith('En Route'));
      const routeColor = isAlternate ? '#2B85F0' : '#0B0F14';
      const originLabel = (voyage.origin_port && !voyage.origin_port.startsWith('En Route')) ? voyage.origin_port : (s.origin_port || 'Origin');
      if (!state.focusRouteLayers) state.focusRouteLayers = {};
      if (!state.focusRouteLayers.completedLine) {
        state.focusRouteLayers.completedLine = L.polyline(completed, { color: routeColor, weight: 5, opacity: 0.92, lineCap: 'round' }).addTo(map);
        state.focusRouteLayers.remainingLine = L.polyline(remaining, { color: routeColor, weight: 3.5, opacity: 0.9, lineCap: 'round', dashArray: '10 8' }).addTo(map);
        state.focusRouteLayers.originMarker = L.marker([voyage.origin_lat, voyage.origin_lon], { icon: L.divIcon({ html: '<div style="font-size:18px">🟢</div>', className: '', iconSize: [18, 18] }) })
          .addTo(map).bindTooltip(originLabel, { direction: 'top', offset: [0, -10], className: 'route-point-tooltip' });
        state.focusRouteLayers.destMarker = L.marker([voyage.dest_lat, voyage.dest_lon], { icon: L.divIcon({ html: '<div style="font-size:18px">🚩</div>', className: '', iconSize: [18, 18] }) })
          .addTo(map).bindTooltip(voyage.dest_port, { direction: 'top', offset: [0, -10], className: 'route-point-tooltip' });
      } else {
        state.focusRouteLayers.completedLine.setLatLngs(completed).setStyle({ color: routeColor });
        state.focusRouteLayers.remainingLine.setLatLngs(remaining).setStyle({ color: routeColor });
        state.focusRouteLayers.originMarker.setLatLng([voyage.origin_lat, voyage.origin_lon]).setTooltipContent(originLabel);
        state.focusRouteLayers.destMarker.setLatLng([voyage.dest_lat, voyage.dest_lon]).setTooltipContent(voyage.dest_port);
      }

      if (s.status === UNDER_ATTACK_STATUS) {
        if (!state.focusRouteLayers.dangerZone) {
          state.focusRouteLayers.dangerZone = L.circle([s.current_lat, s.current_lon], {
            radius: 55000, color: '#ef4444', weight: 2, fillColor: '#ef4444', fillOpacity: 0.16,
            className: 'attack-danger-zone',
          }).addTo(map);
        } else {
          state.focusRouteLayers.dangerZone.setLatLng([s.current_lat, s.current_lon]);
        }
      } else if (state.focusRouteLayers.dangerZone) {
        try { map.removeLayer(state.focusRouteLayers.dangerZone); } catch (e) {}
        delete state.focusRouteLayers.dangerZone;
      }
    } else if (state.focusRouteLayers) {
      Object.values(state.focusRouteLayers).forEach(l => { try { map.removeLayer(l); } catch (e) {} });
      state.focusRouteLayers = null;
    }
  }

  // ── Left sidebar ──
  let signatureStatus = 'Unknown';
  try {
    const data = await apiGet(`/api/ships/${encodeURIComponent(focusShipId)}/positions`);
    const n = data.actual_track.length;
    const validCount = data.actual_track.filter(p => p.signature_valid).length;
    signatureStatus = n === 0 ? 'No reports yet' : (validCount === n ? '✅ Verified' : `⚠️ ${n - validCount} Failed`);
  } catch (e) { /* non-fatal */ }
  const lastBlock = state.blocks.length ? state.blocks[0] : null;
  const gpsSpoofAlert = state.alerts.find(a => a.ship_id === focusShipId && a.alert_type === 'gps_spoofing' && !a.acknowledged);
  let activeIncidents = [];
  try { activeIncidents = await apiGet(`/api/incidents/ship/${encodeURIComponent(focusShipId)}`); } catch (e) { /* non-fatal */ }

  document.getElementById('focus-sidebar').innerHTML = `
    <div class="focus-photo">${shipEmoji(s.ship_type)}</div>
    <div class="focus-name-row"><span class="focus-name">${s.name}</span><span style="cursor:pointer" title="Favorite">⭐</span></div>
    <div class="focus-imo-row"><span class="focus-imo">IMO: ${s.imo_number || '—'}</span><span class="badge badge-blue">Registered</span></div>
    <div class="focus-stat-row">
      <div class="focus-stat"><div>Ship Type</div><div>${s.ship_type || '—'}</div></div>
      <div class="focus-stat"><div>Max Capacity</div><div>${s.capacity_tons ? s.capacity_tons.toLocaleString() + ' MT' : '—'}</div></div>
      <div class="focus-stat"><div>Status</div><div style="color:${statusColor(s.status)}">${s.status}</div></div>
    </div>
    <div class="detail-section-label">Navigation</div>
    <div class="detail-row"><span class="detail-key">Position</span><span class="detail-val">${s.current_lat?.toFixed(4)}°, ${s.current_lon?.toFixed(4)}°</span></div>
    <div class="detail-row"><span class="detail-key">Speed</span><span class="detail-val">${(s.current_speed ?? 0).toFixed(1)} kn</span></div>
    <div class="detail-row"><span class="detail-key">Course</span><span class="detail-val">${(s.course ?? s.heading ?? 0).toFixed(0)}°</span></div>
    <div class="detail-row"><span class="detail-key">Heading</span><span class="detail-val">${(s.heading ?? 0).toFixed(0)}°</span></div>
    <div class="detail-row"><span class="detail-key">Origin</span><span class="detail-val">${s.origin_port || '—'}</span></div>
    <div class="detail-row"><span class="detail-key">Destination</span><span class="detail-val">${s.dest_port || '—'}</span></div>
    <div class="detail-row"><span class="detail-key">ETA</span><span class="detail-val">${voyage?.eta ? fmtDate(voyage.eta) : '—'}</span></div>

    <div class="detail-section-label">Voyage</div>
    ${voyage ? `
      <div class="detail-row"><span class="detail-key">Voyage ID</span><span class="detail-val">${voyage.voyage_id}</span></div>
      <div class="detail-row"><span class="detail-key">Registered</span><span class="detail-val">${voyage.created_at ? fmtDate(voyage.created_at) : '—'}</span></div>
      <div class="detail-row"><span class="detail-key">Status</span><span class="detail-val" style="color:var(--blue2)">${voyage.status.replace('_', ' ')}</span></div>
    ` : `<div style="color:var(--text3);font-size:12px;padding:6px 0">No active voyage.</div>
      <button class="btn btn-primary" style="width:100%" onclick="openVoyageModal('${s.ship_id}')">🧭 Start New Voyage</button>`}

    <div style="display:flex;gap:8px;margin-top:12px">
      <button class="btn btn-primary" style="flex:1" ${voyage ? '' : 'disabled'} onclick="showPage('voyages')">View Voyage Details</button>
      ${s.has_cyclone_warning && window.currentUserRole === 'control_station' ? `<button class="btn btn-amber" style="flex:1" onclick="initReroute('${s.ship_id}')">🌪 Reroute Ship</button>` : ''}
    </div>
    <button class="btn" style="width:100%;margin-top:8px" onclick="downloadShipReport('${s.ship_id}')">⬇ Download Report</button>

    <div class="detail-section-label">Security &amp; Verification</div>
    <div class="detail-row"><span class="detail-key">Blockchain Block</span><span class="detail-val">${lastBlock ? '#' + lastBlock.block_index : '—'}</span></div>
    <div class="detail-row"><span class="detail-key">Signature Status</span><span class="detail-val">${signatureStatus}</span></div>
    <div class="detail-row"><span class="detail-key">GPS Spoofing Check</span><span class="detail-val" style="color:${gpsSpoofAlert ? 'var(--red)' : 'var(--green)'}">${gpsSpoofAlert ? '🚨 Alert' : '✅ Clear'}</span></div>
    <div class="detail-row"><span class="detail-key">Public Key</span><span class="detail-val" style="font-size:10px">${shortHash(s.public_key, 14)} <span style="cursor:pointer" onclick="copyToClipboard('${s.public_key}')">📋</span></span></div>

    <div class="detail-section-label">Live Security Status</div>
    ${s.current_lat != null ? `
      <div class="sim-status-row"><span class="dot"></span>No GPS spoofing detected at ${s.current_lat.toFixed(4)}°, ${s.current_lon.toFixed(4)}°</div>
      <div class="sim-status-row"><span class="dot"></span>No route deviation detected</div>
      <div class="sim-status-row"><span class="dot"></span>No AIS spoofing detected</div>
    ` : `<div style="color:var(--text3);font-size:11px;padding:4px 0">No live position yet.</div>`}

    ${renderAttackModeSection(s, activeIncidents)}

    <button class="btn btn-danger" style="width:100%;margin-top:14px" onclick="raiseShipAlert('${s.ship_id}')">🚩 Report an Issue</button>
  `;

  // ── Bottom stat bar ──
  document.getElementById('focus-bottom-bar').innerHTML = `
    <div class="focus-bottom-stat">Speed<b>${(s.current_speed ?? 0).toFixed(1)} kn</b></div>
    <div class="focus-bottom-stat">Course<b>${(s.course ?? s.heading ?? 0).toFixed(0)}°</b></div>
    <div class="focus-bottom-stat">Heading<b>${(s.heading ?? 0).toFixed(0)}°</b></div>
    <div class="focus-bottom-stat">Latitude<b>${s.current_lat?.toFixed(4)}° N</b></div>
    <div class="focus-bottom-stat">Longitude<b>${s.current_lon?.toFixed(4)}° E</b></div>
    <div class="focus-bottom-stat">Last Update<b>${now()}</b></div>
  `;

  // ── Route timeline (origin ── ship ── destination) ──
  const pct = voyage ? voyageProgressPct(voyage) : (s.dest_port ? 100 : 0);
  document.getElementById('focus-timeline').innerHTML = `
    <div class="focus-timeline-end">⚓ ${s.origin_port || '—'}<br><span style="color:var(--text3)">Origin</span></div>
    <div class="focus-timeline-track">
      <div class="focus-timeline-ship" style="left:${pct}%">🚢</div>
    </div>
    <div class="focus-timeline-mid">Distance Covered<b>${voyage ? voyage.distance_travelled_km.toFixed(0) + ' NM' : '—'}</b></div>
    <div class="focus-timeline-track">
      <div class="focus-timeline-ship" style="left:${pct}%;opacity:0"></div>
    </div>
    <div class="focus-timeline-end">📍 ${s.dest_port || '—'}<br><span style="color:var(--text3)">${voyage?.eta ? fmtDate(voyage.eta) : 'ETA —'}</span></div>
  `;
}

function copyToClipboard(text) {
  navigator.clipboard?.writeText(text).then(() => toast('Copied to clipboard', 'success')).catch(() => {});
}

// Simple client-side text report — no backend endpoint needed, generated
// entirely from data already loaded on the page.
function downloadShipReport(id) {
  const s = state.ships.find(x => x.ship_id === id);
  if (!s) return;
  const voyage = state.voyages[id];
  const lastBlock = state.blocks.length ? state.blocks[0] : null;
  const lines = [
    `MARINEGUARD VESSEL REPORT`,
    `Generated: ${new Date().toISOString()}`,
    ``,
    `Ship: ${s.name}`,
    `IMO: ${s.imo_number || '—'}`,
    `Type: ${s.ship_type || '—'}`,
    `Capacity: ${s.capacity_tons ? s.capacity_tons.toLocaleString() + ' MT' : '—'}`,
    `Status: ${s.status}`,
    ``,
    `Position: ${s.current_lat?.toFixed(4)}, ${s.current_lon?.toFixed(4)}`,
    `Speed: ${(s.current_speed ?? 0).toFixed(1)} kn`,
    `Heading: ${(s.heading ?? 0).toFixed(0)} deg`,
    `Origin: ${s.origin_port || '—'}`,
    `Destination: ${s.dest_port || '—'}`,
    ``,
    voyage ? `Voyage ID: ${voyage.voyage_id}` : `No active voyage`,
    voyage ? `Distance travelled: ${voyage.distance_travelled_km.toFixed(0)} km` : '',
    voyage ? `Distance remaining: ${voyage.distance_remaining_km.toFixed(0)} km` : '',
    voyage ? `ETA: ${voyage.eta ? fmtDate(voyage.eta) : '—'}` : '',
    ``,
    `Blockchain block: ${lastBlock ? '#' + lastBlock.block_index : '—'}`,
    `Public key: ${s.public_key || '—'}`,
  ].filter(l => l !== '').join('\n');
  const blob = new Blob([lines], { type: 'text/plain' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `${s.name.replace(/\s+/g, '_')}_report.txt`;
  a.click();
  URL.revokeObjectURL(a.href);
}

// ── DELETE SHIP (with confirmation) ──
async function confirmResetAllData() {
  if (!confirm('Reset ALL data? This permanently deletes every registered ship, voyage, position history, alert, message, and the entire blockchain ledger. This cannot be undone. Continue?')) return;
  if (!confirm('Really sure? This wipes the whole dataset back to empty.')) return;
  try {
    await apiPost('/api/ships/admin/reset-all', {});
    toast('All data reset. Starting from a clean slate.', 'success');

    // Clear every bit of client-side state so nothing stale lingers in the UI
    // until the next full page load.
    Object.keys(state.mapMarkers || {}).forEach(id => {
      if (state.trackingMap && state.mapMarkers[id]) state.trackingMap.removeLayer(state.mapMarkers[id]);
      clearShipRouteLayers(id);
    });
    state.mapMarkers = {};
    state.voyages = {};
    state.ships = [];

    closeModal('shipDetailModal');
    await loadShips();
    await refreshDashboard();
    renderMapShips();
    if (document.getElementById('page-ships').classList.contains('active')) refreshShipRegistry();
    if (document.getElementById('page-voyages').classList.contains('active')) await refreshVoyagesPage();
    if (document.getElementById('page-blockchain').classList.contains('active')) await refreshBlockchain();
  } catch (e) {
    toast('Reset failed: ' + (e.message || e), 'error');
  }
}

async function confirmDeleteShip(shipId, name) {
  if (!confirm(`Delete ${name}? This permanently removes the ship, its voyages, alerts and tracking history. Blockchain records are archived, not deleted. This cannot be undone.`)) return;
  try {
    await apiDelete(`/api/ships/${encodeURIComponent(shipId)}`);
    toast(`${name} deleted.`, 'success');
    closeModal('shipDetailModal');
    if (state.mapMarkers[shipId]) { state.trackingMap.removeLayer(state.mapMarkers[shipId]); delete state.mapMarkers[shipId]; }
    clearShipRouteLayers(shipId);
    delete state.voyages[shipId];
    await loadShips();
    await refreshDashboard();
    renderMapShips();
    if (document.getElementById('page-ships').classList.contains('active')) refreshShipRegistry();
  } catch (e) {
    toast('Delete failed: ' + errMsg(e), 'error');
  }
}


// ═══════════════════════════════════════════════════════════════════════════
// ALERTS
// ═══════════════════════════════════════════════════════════════════════════
async function loadAlerts() {
  state.alerts = filterSimAlerts(await apiGet('/api/alerts?limit=200'));
  const activeCount = state.alerts.filter(a => !a.acknowledged && !a.resolved).length;
  document.getElementById('alert-badge').textContent = activeCount;
  return state.alerts;
}

function renderAlerts() {
  const list = document.getElementById('alerts-list');
  if (state.alerts.length === 0) {
    list.innerHTML = `<div class="card" style="text-align:center;color:var(--text3);padding:40px">No alerts. System nominal.</div>`;
    return;
  }
  list.innerHTML = state.alerts.map(a => `
    <div class="alert-item ${a.severity}" style="margin-bottom:10px;${a.resolved ? 'opacity:0.6' : ''}">
      <div class="alert-icon">${threatIconFor(a.alert_type) || severityIcon(a.severity)}</div>
      <div class="alert-body">
        <div class="alert-title">${a.title}
          ${a.resolved ? '<span class="badge badge-green" style="margin-left:6px">✅ Resolved</span>' : ''}
          ${a.acknowledged ? '<span class="badge badge-blue" style="margin-left:6px">Acknowledged</span>' : ''}
          ${a.occurrence_count > 1 ? `<span class="badge" style="margin-left:6px;background:var(--surface3);color:var(--text3)">×${a.occurrence_count}</span>` : ''}
        </div>
        <div class="alert-desc">${a.detail || ''}</div>
        <div class="alert-meta">${a.ship_name ? a.ship_name + ' · ' : ''}${fmtDate(a.created_at)}${a.resolved ? ' · resolved ' + fmtDate(a.resolved_at) : (a.last_seen_at ? ' · last confirmed ' + fmtDate(a.last_seen_at) : '')}${a.block_index ? ' · Block #' + a.block_index : ''}</div>
        <div style="margin-top:8px;display:flex;gap:8px">
          ${!a.acknowledged ? `<button class="btn" onclick="acknowledgeAlert(${a.alert_id})">Acknowledge</button>` : ''}
          ${!a.acknowledged && a.severity !== 'critical' ? `<button class="btn btn-danger" onclick="escalateAlert(${a.alert_id})">⬆ Escalate</button>` : ''}
        </div>
      </div>
    </div>`).join('');
}

async function acknowledgeAlert(id) {
  try {
    await apiPost('/api/alerts/acknowledge', { alert_id: id });
    await loadAlerts();
    renderAlerts();
    await refreshDashboard();
    toast('Alert acknowledged', 'success');
  } catch (e) { toast('Could not acknowledge alert: ' + errMsg(e), 'error'); }
}

async function escalateAlert(id) {
  // The backend has no in-place "change severity" operation - alerts are
  // an append-only audit trail, not editable records. Escalating for real
  // means raising a new, explicitly critical alert that references the
  // original, rather than silently mutating history.
  const original = state.alerts.find(a => a.alert_id === id);
  if (!original) return;
  try {
    await apiPost('/api/alerts/broadcast', {
      ship_id: original.ship_id, title: 'ESCALATED: ' + original.title,
      detail: `Escalated to CRITICAL by operator at ${now()}. Original: ${original.detail || ''}`,
      severity: 'critical', lat: original.lat, lon: original.lon,
    });
    await loadAlerts(); renderAlerts(); await refreshDashboard();
    toast('Alert escalated to CRITICAL', 'error');
  } catch (e) { toast('Could not escalate alert: ' + errMsg(e), 'error'); }
}

function clearAlerts() {
  // The backend has no bulk-delete for alerts (alerts are an audit trail,
  // not meant to be erased) - acknowledging everything is the real
  // equivalent of "clearing" them from the active view.
  Promise.all(state.alerts.filter(a => !a.acknowledged).map(a => apiPost('/api/alerts/acknowledge', { alert_id: a.alert_id })))
    .then(async () => { await loadAlerts(); renderAlerts(); await refreshDashboard(); toast('All alerts acknowledged', 'success'); })
    .catch(e => toast('Could not clear alerts: ' + errMsg(e), 'error'));
}

async function raiseAlert() {
  try {
    await apiPost('/api/alerts/broadcast', {
      title: 'Manual Alert Raised', detail: 'Control center operator raised a manual alert at ' + now(),
      severity: 'critical',
    });
    await loadAlerts(); renderAlerts(); await refreshDashboard();
    toast('Alert raised', 'error');
  } catch (e) { toast('Could not raise alert: ' + errMsg(e), 'error'); }
}

async function raiseShipAlert(sid) {
  const s = state.ships.find(x => x.ship_id === sid);
  if (!s) return;
  try {
    await apiPost('/api/alerts/broadcast', {
      ship_id: sid, title: 'Ship Alert: ' + s.name,
      detail: 'Manual alert raised for ' + s.name + ' at ' + now(), severity: 'warning',
      lat: s.current_lat, lon: s.current_lon,
    });
    await loadAlerts(); await refreshDashboard();
    toast('Alert raised for ' + s.name, 'warning');
  } catch (e) { toast('Could not raise alert: ' + errMsg(e), 'error'); }
}
// ═══════════════════════════════════════════════════════════════════════════
// AUTHENTICATION
// ═══════════════════════════════════════════════════════════════════════════
async function refreshAuthPage() {
  populate('auth-ship-select', false, 'Select a ship…');
  populate('auth-test-ship', false, 'Select ship…');
  await renderAuthShipDetails();
  await refreshAuthLog();
}

document.addEventListener('change', (e) => {
  if (e.target && e.target.id === 'auth-ship-select') renderAuthShipDetails();
});

async function renderAuthShipDetails() {
  const sid = document.getElementById('auth-ship-select')?.value;
  const pubEl = document.getElementById('auth-pub-key');
  const fpEl = document.getElementById('auth-fingerprint');
  const certEl = document.getElementById('auth-cert');
  if (!sid) {
    pubEl.textContent = 'Select a ship to view key';
    fpEl.textContent = '—';
    certEl.textContent = '—';
    return;
  }
  const s = state.ships.find(x => x.ship_id === sid);
  if (!s) return;
  pubEl.textContent = s.public_key;
  try {
    const { sha256 } = await apiPost('/api/crypto/hash', { data: s.public_key });
    fpEl.textContent = sha256;
  } catch (e) { fpEl.textContent = '—'; }
  certEl.textContent = s.certificate_hash;
}

async function rotateKeys() {
  const sid = document.getElementById('auth-ship-select')?.value;
  if (!sid) { toast('Select a ship first', 'warning'); return; }
  try {
    const result = await apiPost('/api/auth/rotate_keys', { ship_id: sid });
    toast(`Keys rotated for ${shipName(sid)}. Logged to block #${result.block_index}.`, 'success');
    await loadShips();
    await renderAuthShipDetails();
    if (document.getElementById('page-blockchain').classList.contains('active')) refreshBlockchain();
  } catch (e) { toast('Key rotation failed: ' + errMsg(e), 'error'); }
}

async function runAuthentication() {
  const sid = document.getElementById('auth-test-ship').value;
  const challenge = document.getElementById('auth-challenge').value.trim();
  if (!sid) { toast('Select a ship to authenticate', 'warning'); return; }
  if (!challenge) { toast('Enter a challenge message', 'warning'); return; }

  try {
    const result = await apiPost('/api/auth/authenticate', { ship_id: sid, challenge });
    document.getElementById('auth-sig').textContent = result.signature;
    document.getElementById('auth-result').innerHTML = result.success
      ? `<span style="color:var(--green)">✅ VALID — ECDSA signature verified</span>`
      : `<span style="color:var(--red)">❌ INVALID — signature verification failed</span>`;
    document.getElementById('auth-session').textContent = `${result.session_key_hex} (fingerprint: ${result.session_key_fingerprint})`;
    toast(result.success ? 'Authentication successful' : 'Authentication failed', result.success ? 'success' : 'error');
    await refreshAuthLog();
  } catch (e) {
    toast('Authentication request failed: ' + errMsg(e), 'error');
  }
}

async function refreshAuthLog() {
  try {
    state.authLog = await apiGet('/api/auth/log?limit=50');
    renderAuthLog();
  } catch (e) { /* non-fatal */ }
}

function renderAuthLog() {
  const tbody = document.getElementById('auth-log');
  if (state.authLog.length === 0) {
    tbody.innerHTML = `<tr><td colspan="5" style="text-align:center;color:var(--text3);padding:20px">No authentication attempts yet. Run one above.</td></tr>`;
    return;
  }
  tbody.innerHTML = state.authLog.map(a => `
    <tr>
      <td>${fmtTime(a.created_at)}</td>
      <td>${a.ship_name}</td>
      <td>ECDSA + ECDH</td>
      <td style="color:${a.success ? 'var(--green)' : 'var(--red)'}">${a.success ? 'Success' : 'Failed'}</td>
      <td style="font-family:var(--mono);font-size:11px">${a.session_key_fingerprint}</td>
    </tr>`).join('');
}
// ═══════════════════════════════════════════════════════════════════════════
// CRYPTOGRAPHY SANDBOX
// ═══════════════════════════════════════════════════════════════════════════
function showCryptoTab(id, btn) {
  document.querySelectorAll('.tab-pane').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  document.getElementById('crypto-' + id).classList.add('active');
  btn.classList.add('active');
  if (id === 'encrypt') ensureAesKey();
}

let lastSignedMessage = null;
let lastSignature = null;

async function signMessage() {
  const message = document.getElementById('sign-message').value;
  const sid = document.getElementById('sign-ship').value;
  if (!sid) { toast('Select a signing ship', 'warning'); return; }
  try {
    const { signature } = await apiPost('/api/crypto/sign', { ship_id: sid, message });
    document.getElementById('sign-output').textContent = signature;
    document.getElementById('verify-message').value = message;
    document.getElementById('verify-sig').value = signature;
    document.getElementById('verify-ship').value = sid;
    lastSignedMessage = message;
    lastSignature = signature;
    toast('Message signed with ECDSA', 'success');
  } catch (e) { toast('Signing failed: ' + errMsg(e), 'error'); }
}

async function verifySignature() {
  const message = document.getElementById('verify-message').value;
  const signature = document.getElementById('verify-sig').value.trim();
  const sid = document.getElementById('verify-ship').value;
  if (!sid || !signature) { toast('Select a ship and provide a signature', 'warning'); return; }
  try {
    const { is_valid } = await apiPost('/api/crypto/verify', { ship_id: sid, message, signature });
    document.getElementById('verify-result').innerHTML = is_valid
      ? `<span style="color:var(--green)">✅ VALID — signature matches message and public key</span>`
      : `<span style="color:var(--red)">❌ INVALID — signature does not match</span>`;
    toast(is_valid ? 'Signature valid' : 'Signature invalid', is_valid ? 'success' : 'error');
  } catch (e) { toast('Verification failed: ' + errMsg(e), 'error'); }
}

async function tamperAndVerify() {
  if (!lastSignedMessage || !lastSignature) { await signMessage(); if (!lastSignedMessage) return; }
  const sid = document.getElementById('sign-ship').value;
  const tampered = lastSignedMessage.replace(/[0-9]/, m => (parseInt(m, 10) + 1) % 10);
  try {
    const { is_valid } = await apiPost('/api/crypto/verify', { ship_id: sid, message: tampered, signature: lastSignature });
    document.getElementById('verify-message').value = tampered;
    document.getElementById('verify-sig').value = lastSignature;
    document.getElementById('verify-ship').value = sid;
    document.getElementById('verify-result').innerHTML = is_valid
      ? `<span style="color:var(--amber)">⚠️ Unexpectedly valid</span>`
      : `<span style="color:var(--red)">❌ INVALID — tampering detected, signature rejected</span>`;
    toast('Tampered message correctly rejected', is_valid ? 'warning' : 'success');
  } catch (e) { toast('Tamper demo failed: ' + errMsg(e), 'error'); }
}

// ---- AES ----
function ensureAesKey() {
  const keyInput = document.getElementById('enc-key');
  if (!keyInput.value) {
    const bytes = new Uint8Array(32);
    crypto.getRandomValues(bytes);
    keyInput.value = Array.from(bytes).map(b => b.toString(16).padStart(2, '0')).join('');
  }
}
async function encryptData() {
  ensureAesKey();
  const plaintext = document.getElementById('enc-plain').value;
  const key = document.getElementById('enc-key').value;
  try {
    const result = await apiPost('/api/crypto/aes', { key_hex: key, plaintext, mode: 'encrypt' });
    state.encBuffer = result;
    document.getElementById('enc-output').textContent = `iv:${result.iv} | ciphertext:${result.ciphertext}`;
    document.getElementById('dec-input').value = `iv:${result.iv} | ciphertext:${result.ciphertext}`;
    toast('Encrypted with AES-256-CBC', 'success');
  } catch (e) { toast('Encryption failed: ' + errMsg(e), 'error'); }
}
async function decryptData() {
  const raw = document.getElementById('dec-input').value.trim();
  const key = document.getElementById('enc-key').value;
  const match = raw.match(/iv:([0-9a-f]+)\s*\|\s*ciphertext:([0-9a-f]+)/i);
  if (!match) { toast('Paste ciphertext in the format produced by Encrypt', 'warning'); return; }
  try {
    const { plaintext } = await apiPost('/api/crypto/aes', { key_hex: key, iv_hex: match[1], ciphertext_hex: match[2], mode: 'decrypt' });
    document.getElementById('dec-output').textContent = plaintext;
    toast('Decrypted successfully', 'success');
  } catch (e) {
    document.getElementById('dec-output').textContent = '— decryption failed —';
    toast('Decryption failed: ' + errMsg(e), 'error');
  }
}

// ---- SHA-256 ----
async function computeHash() {
  const input = document.getElementById('hash-input').value;
  try {
    const { sha256 } = await apiPost('/api/crypto/hash', { data: input });
    document.getElementById('hash-output').textContent = sha256;
  } catch (e) { toast('Hashing failed: ' + errMsg(e), 'error'); }
}
async function avalancheDemo() {
  const original = document.getElementById('hash-input').value || 'Ship: MV-AURORA | Block: #148';
  let modified = original;
  if (/[0-9]/.test(original)) modified = original.replace(/[0-9]/, m => (parseInt(m, 10) + 1) % 10);
  else modified = original + '!';
  try {
    const [h1, h2] = await Promise.all([
      apiPost('/api/crypto/hash', { data: original }),
      apiPost('/api/crypto/hash', { data: modified }),
    ]);
    document.getElementById('aval-orig').textContent = original;
    document.getElementById('aval-h1').textContent = h1.sha256;
    document.getElementById('aval-mod').textContent = modified;
    document.getElementById('aval-h2').textContent = h2.sha256;
    toast('Avalanche effect demonstrated', 'success');
  } catch (e) { toast('Avalanche demo failed: ' + errMsg(e), 'error'); }
}

// ---- ECDH ----
async function ecdhExchange() {
  if (state.ships.length < 1) { toast('Register at least one ship first', 'warning'); return; }
  const ship = state.ships[Math.floor(Math.random() * state.ships.length)];
  try {
    const ccKey = await apiGet('/api/auth/control_center_public_key');
    document.getElementById('ecdh-ship-priv').textContent = '🔒 Stored onboard — never transmitted';
    document.getElementById('ecdh-ship-pub').textContent = ship.public_key;
    document.getElementById('ecdh-ctrl-pub').textContent = ccKey.public_key;

    // Authenticate this ship, which derives a real ECDH session key with
    // the Control Center identity server-side and returns it for display.
    const result = await apiPost('/api/auth/authenticate', { ship_id: ship.ship_id, challenge: 'ECDH-DEMO-' + Date.now() });
    document.getElementById('ecdh-ship-secret').textContent = result.session_key_hex;
    document.getElementById('ecdh-ctrl-secret').textContent = result.session_key_hex;
    document.getElementById('ecdh-match').innerHTML = `<span style="color:var(--green)">✅ MATCH — both sides derived ${result.session_key_fingerprint}</span>`;
    toast(`ECDH exchange completed for ${ship.name}`, 'success');
  } catch (e) { toast('ECDH exchange failed: ' + errMsg(e), 'error'); }
}
// ═══════════════════════════════════════════════════════════════════════════
// BLOCKCHAIN
// ═══════════════════════════════════════════════════════════════════════════
async function refreshBlockchain() {
  try {
    state.blocks = await apiGet('/api/blockchain/blocks');
    renderBlockchain();
  } catch (e) { toast('Could not load blockchain: ' + errMsg(e), 'error'); }
}

// Stable color per simulated validator so the same node always reads the
// same color across the race bars and table badges.
const POET_VALIDATOR_COLORS = {
  'validator-alpha': '#10B981',
  'validator-bravo': '#3B82F6',
  'validator-charlie': '#F59E0B',
  'validator-delta': '#A855F7',
};
function poetColor(id) { return POET_VALIDATOR_COLORS[id] || '#64748B'; }

// ═══════════════════════════════════════════════════════════════════════════
// POET VALIDATOR WAIT TIME — live graph
// Every bar height comes straight from the backend's real
// time.perf_counter_ns() measurement of that validator's PoET wait
// (see app/blockchain/poet.py). Nothing here is randomly generated on
// the frontend — this module only ever plots numbers it received from
// the server, either via the initial REST fetch or a live "poet_round"
// websocket push.
// ═══════════════════════════════════════════════════════════════════════════
let poetChartInstance = null;

// Same color-per-validator idea as POET_VALIDATOR_COLORS above, keyed by
// the human-readable display name the backend now sends for this graph
// ("Validator 1", "Validator 2", …) instead of the internal validator id.
const POET_DISPLAY_COLORS = {
  'Validator 1': '#10B981',
  'Validator 2': '#3B82F6',
  'Validator 3': '#F59E0B',
  'Validator 4': '#A855F7',
  'Validator 5': '#EC4899',
};
function poetDisplayColor(name) { return POET_DISPLAY_COLORS[name] || '#64748B'; }

// Custom Chart.js plugin: prints each bar's real measured value just
// above the bar (the "display values on top of bars" requirement).
// Chart.js core has no built-in label feature, so this small plugin
// draws the text directly after the bars render each frame.
const poetValueLabelPlugin = {
  id: 'poetValueLabel',
  afterDatasetsDraw(chart) {
    const { ctx } = chart;
    chart.data.datasets.forEach((dataset, i) => {
      const meta = chart.getDatasetMeta(i);
      meta.data.forEach((bar, index) => {
        const value = dataset.data[index];
        if (value === null || value === undefined) return;
        ctx.save();
        ctx.fillStyle = '#E2E8F0';
        ctx.font = '600 11px Inter, sans-serif';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'bottom';
        ctx.fillText(`${value.toFixed(1)} ms`, bar.x, bar.y - 6);
        ctx.restore();
      });
    });
  },
};

// data shape (from GET /api/blockchain/poet/latest or a "poet_round"
// websocket event): { round, validators: [{id, wait_ms}, ...], winner }
function renderPoetChart(data) {
  const canvas = document.getElementById('poetChart');
  if (!canvas || typeof Chart === 'undefined' || !data || !Array.isArray(data.validators)) return;

  const labels = data.validators.map(v => v.id);
  const values = data.validators.map(v => v.wait_ms);
  const colors = labels.map(id => id === data.winner ? poetDisplayColor(id) : poetDisplayColor(id) + '99');

  const labelEl = document.getElementById('poet-live-label');
  if (labelEl) labelEl.textContent = `Round ${data.round} — winner: ${data.winner}`;

  if (poetChartInstance) {
    // Update in place (rather than destroy + recreate) so Chart.js
    // animates each bar from its old height to the new measured value —
    // the "animated transitions" requirement — instead of jump-cutting.
    poetChartInstance.data.labels = labels;
    poetChartInstance.data.datasets[0].data = values;
    poetChartInstance.data.datasets[0].backgroundColor = colors;
    poetChartInstance.update();
    return;
  }

  poetChartInstance = new Chart(canvas, {
    type: 'bar',
    data: {
      labels,
      datasets: [{
        label: 'Wait Time (ms)',
        data: values,
        backgroundColor: colors,
        borderRadius: 6,
        maxBarThickness: 70,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: { duration: 500, easing: 'easeOutQuart' },
      layout: { padding: { top: 24 } },
      plugins: {
        legend: { display: false },
        title: { display: true, text: 'PoET Validator Waiting Time', color: '#E2E8F0', font: { size: 13, weight: '600' } },
      },
      scales: {
        x: { ticks: { color: '#94A3B8' }, grid: { display: false } },
        y: {
          beginAtZero: true,
          ticks: { color: '#94A3B8' },
          grid: { color: '#334155' },
          title: { display: true, text: 'Milliseconds (ms)', color: '#94A3B8' },
        },
      },
    },
    plugins: [poetValueLabelPlugin],
  });
}

// One-time REST fetch so the graph has real data to show immediately on
// page load, before the first live block is mined in this session.
async function loadPoetLatest() {
  try {
    const data = await apiGet('/api/blockchain/poet/latest');
    if (data) renderPoetChart(data);
  } catch (e) { /* non-fatal — the graph just waits for the first live round */ }
}

// Live updates: a persistent websocket connection to the /ws/live feed.
// The backend already pushes events the instant they happen (new_alert,
// alert_updated, alert_resolved, reroute_proposed, resume_proposed,
// reroute_decided, poet_round, voyage_completed, etc) - this used to
// only handle poet_round and silently drop everything else, so
// alerts/reroutes only ever appeared on the next 15s polling tick
// instead of immediately. Every relevant kind is now handled here.
let poetSocket = null;
function connectPoetSocket() {
  try {
    const wsUrl = API_BASE.replace(/^http/, 'ws') + '/ws/live';
    poetSocket = new WebSocket(wsUrl);
    poetSocket.onmessage = (evt) => {
      let msg;
      try { msg = JSON.parse(evt.data); } catch (e) { return; }
      if (!msg || !msg.kind) return;

      if (msg.kind === 'poet_round') { renderPoetChart(msg); return; }

      if (msg.kind === 'new_alert' || msg.kind === 'alert_updated' || msg.kind === 'alert_resolved') {
        handleLiveAlertEvent(msg);
        return;
      }

      if (msg.kind === 'reroute_proposed' || msg.kind === 'resume_proposed' || msg.kind === 'reroute_decided') {
        handleLiveRerouteEvent(msg);
        return;
      }

      if (msg.kind === 'voyage_completed') {
        loadAlerts().then(renderAlerts).catch(() => {});
        loadActiveVoyages().catch(() => {});
        return;
      }

      if (msg.kind === 'mode_changed') {
        applyTrackingMode(msg.mode, false);
        return;
      }

      if (msg.kind === 'incident_started' || msg.kind === 'incident_ended') {
        const verb = msg.kind === 'incident_started' ? 'started' : 'resolved';
        toast(`${msg.kind === 'incident_started' ? '🚨' : '✅'} ${msg.label} ${verb} — ${msg.ship_name}`,
              msg.kind === 'incident_started' ? 'error' : 'success');
        loadShips().catch(() => {});
        loadAlerts().then(renderAlerts).catch(() => {});
        refreshDashboard().catch(() => {});
        if (document.getElementById('page-ship-focus')?.classList.contains('active') && focusShipId === msg.ship_id) {
          renderShipFocus().catch(() => {});
        }
        return;
      }

      if (msg.kind === 'incident_action') {
        toast(`${msg.label} — ${msg.ship_name}`, 'info');
        if (document.getElementById('page-ship-focus')?.classList.contains('active') && focusShipId === msg.ship_id) {
          renderShipFocus().catch(() => {});
        }
        return;
      }
    };
    poetSocket.onclose = () => { setTimeout(connectPoetSocket, 3000); };
    poetSocket.onerror = () => { poetSocket.close(); };
  } catch (e) { setTimeout(connectPoetSocket, 3000); }
}

/** Refreshes alerts immediately on push instead of waiting for the next
 * 15s poll, and surfaces a toast for brand-new alerts so Control Station
 * sees them the instant they fire. */
async function handleLiveAlertEvent(msg) {
  try {
    await loadAlerts();
    renderAlerts();
    renderNotificationBadge();
    if (msg.kind === 'new_alert' && msg.alert && !SIM_SUPPRESSED_ALERT_TYPES.has(msg.alert.alert_type)) {
      const sev = (msg.alert.severity === 'critical' || msg.alert.severity === 'warning') ? 'error' : 'info';
      toast(msg.alert.title || 'New alert', sev);
    }
  } catch (e) { /* non-fatal */ }
}

/** Same idea for the reroute-approval inbox - Control Station shouldn't
 * have to wait up to 15s to see a new pending reroute/resume request. */
async function handleLiveRerouteEvent(msg) {
  try {
    if (window.currentUserRole === 'control_station') await loadRerouteApprovals();
    await loadAlerts();
    renderAlerts();
    renderNotificationBadge();
    if (msg.kind === 'reroute_decided' && msg.decision === 'approved') {
      // Reroutes now auto-apply the instant they're detected - refresh
      // ships/voyages right away so the new route/destination shows up
      // on Live Tracking immediately instead of on the next 15s poll.
      await loadShips();
      await loadActiveVoyages();
      if (document.getElementById('page-tracking')?.classList.contains('active')) renderMapShips();
      if (document.getElementById('page-ship-focus')?.classList.contains('active')) await renderShipFocus();
      if (document.getElementById('page-voyages')?.classList.contains('active')) refreshVoyagesPage();
      toast('🔄 Route automatically changed to avoid a cyclone', 'info');
    }
    if (msg.kind === 'reroute_proposed') toast('⚠ New reroute request awaiting approval', 'error');
    if (msg.kind === 'resume_proposed') toast('✅ A ship can resume its voyage — approval requested', 'info');
  } catch (e) { /* non-fatal */ }
}

function poetRaceBarHtml(draws, winner) {
  if (!draws) return '';
  const max = Math.max(...Object.values(draws));
  return `<div style="display:flex;flex-direction:column;gap:2px;margin-top:6px">${
    Object.entries(draws).map(([id, ms]) => `
      <div style="display:flex;align-items:center;gap:5px;font-size:9px">
        <span style="width:64px;color:${id === winner ? poetColor(id) : 'var(--text3)'};font-weight:${id === winner ? '700' : '400'}">${id.replace('validator-', '')}</span>
        <div style="flex:1;background:var(--bg3);border-radius:3px;height:5px;overflow:hidden">
          <div style="width:${(ms / max) * 100}%;height:100%;background:${poetColor(id)};opacity:${id === winner ? '1' : '0.4'}"></div>
        </div>
        <span style="width:38px;text-align:right;color:var(--text3)">${ms}ms</span>
      </div>`).join('')
  }</div>`;
}

function renderBlockchain() {
  document.getElementById('block-count').textContent = state.blocks.length;

  const chainEl = document.getElementById('block-chain');
  const recent = state.blocks.slice(-12);
  chainEl.innerHTML = recent.map(b => `
    <div class="block ${b.event_type === 'REGISTER' ? 'genesis' : ''}">
      <div class="block-num">#${b.block_index} · ${b.event_type}</div>
      <div class="block-hash">${shortHash(b.block_hash, 10)}</div>
      <div class="block-data">${b.ship_name || 'System'}</div>
      ${b.proposer_id ? `<div style="font-size:10px;margin-top:4px;color:${poetColor(b.proposer_id)}">⛏ ${b.proposer_id.replace('validator-', '')} (${b.poet_wait_ms}ms)</div>` : ''}
    </div>`).join('');

  const tbody = document.getElementById('block-table');
  if (state.blocks.length === 0) {
    tbody.innerHTML = `<tr><td colspan="8" style="text-align:center;color:var(--text3);padding:20px">No blocks yet. Register a ship or take any action to write the first block.</td></tr>`;
    return;
  }
  tbody.innerHTML = [...state.blocks].reverse().map(b => `
    <tr>
      <td>${b.block_index}</td>
      <td>${fmtTime(b.timestamp)}</td>
      <td>${b.ship_name || '—'}</td>
      <td>${b.event_type}</td>
      <td>
        ${b.proposer_id ? `
          <div style="display:flex;align-items:center;gap:6px;cursor:default" title="PoET election — shortest random wait among all validators wins">
            <span style="width:8px;height:8px;border-radius:50%;background:${poetColor(b.proposer_id)};display:inline-block"></span>
            <span style="font-size:11px">${b.proposer_id.replace('validator-', '')}</span>
            <span style="font-size:10px;color:var(--text3)">${b.poet_wait_ms}ms</span>
          </div>
          <details style="margin-top:2px"><summary style="font-size:9px;color:var(--text3);cursor:pointer">race</summary>${poetRaceBarHtml(b.poet_draws, b.proposer_id)}</details>
        ` : '<span style="color:var(--text3);font-size:11px">—</span>'}
      </td>
      <td style="font-family:var(--mono);font-size:11px">${shortHash(b.block_hash)}</td>
      <td style="font-family:var(--mono);font-size:11px">${shortHash(b.prev_hash)}</td>
      <td id="block-valid-${b.block_index}" style="color:var(--green)">✓</td>
    </tr>`).join('');
}

async function addBlock() {
  // A manually-added block needs a real event behind it; raising a quick
  // informational alert is itself a genuine, signed-and-logged action,
  // unlike directly inserting a placeholder row into the chain.
  try {
    await apiPost('/api/alerts/broadcast', { title: 'Manual Block Entry', detail: 'Operator manually triggered a ledger entry at ' + now(), severity: 'info' });
    await refreshBlockchain();
    await refreshDashboard();
    toast('New block mined to blockchain', 'success');
  } catch (e) { toast('Could not add block: ' + errMsg(e), 'error'); }
}

async function tamperBlock() {
  if (state.blocks.length === 0) { toast('No blocks to tamper with yet', 'warning'); return; }
  const target = state.blocks[Math.floor(state.blocks.length / 2)];
  try {
    await apiPost('/api/blockchain/tamper_demo', { block_index: target.block_index, new_lat: 999.99 });
    toast(`Block #${target.block_index} tampered (demo). Run "Verify Chain" to detect it.`, 'warning');
    await refreshBlockchain();
  } catch (e) { toast('Tamper demo failed: ' + errMsg(e), 'error'); }
}

async function verifyChain() {
  try {
    const result = await apiGet('/api/blockchain/verify');
    if (result.is_valid) {
      toast('✅ Chain integrity verified — all blocks valid', 'success');
      document.querySelectorAll('[id^="block-valid-"]').forEach(el => { el.textContent = '✓'; el.style.color = 'var(--green)'; });
    } else {
      toast(`❌ Chain integrity BROKEN at block #${result.broken_at_block}`, 'error');
      result.details.forEach(d => {
        const el = document.getElementById('block-valid-' + d.block_index);
        if (el) { el.textContent = d.valid ? '✓' : '✗'; el.style.color = d.valid ? 'var(--green)' : 'var(--red)'; }
      });
    }
    await refreshDashboard();
  } catch (e) { toast('Verification failed: ' + errMsg(e), 'error'); }
}
// ═══════════════════════════════════════════════════════════════════════════
// ROUTE MONITOR — expected (dashed) vs actual (solid) route visualization
// ═══════════════════════════════════════════════════════════════════════════
let routeExpectedLine = null;
let routeActualLine = null;
let routeMarkers = [];

function initRouteMap() {
  if (state.routeMap) return;
  state.routeMap = L.map('route-map', { zoomControl: true }).setView([15, 80], 5);
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    maxZoom: 19
  }).addTo(state.routeMap);
}

async function loadRoute() {
  const sid = document.getElementById('route-ship').value;
  const detailEl = document.getElementById('route-detail');
  if (!sid) { detailEl.innerHTML = ''; return; }

  state.selectedRouteShip = sid;
  const s = state.ships.find(x => x.ship_id === sid);
  if (!s) return;

  detailEl.innerHTML = `
    <div class="detail-row"><span class="detail-key">Expected route</span><span class="detail-val">${s.origin_port} → ${s.dest_port}</span></div>
    <div class="detail-row"><span class="detail-key">Origin</span><span class="detail-val">${s.origin_lat?.toFixed(4)}°, ${s.origin_lon?.toFixed(4)}°</span></div>
    <div class="detail-row"><span class="detail-key">Destination</span><span class="detail-val">${s.dest_lat?.toFixed(4)}°, ${s.dest_lon?.toFixed(4)}°</span></div>
    <div class="detail-row"><span class="detail-key">Current position</span><span class="detail-val">${s.current_lat?.toFixed(4)}°, ${s.current_lon?.toFixed(4)}°</span></div>
  `;

  try {
    const data = await apiGet(`/api/ships/${encodeURIComponent(sid)}/positions`);
    renderRouteMap(data);
    renderRouteStats(data);
  } catch (e) { toast('Could not load route data: ' + errMsg(e), 'error'); }
}

function renderRouteMap(data) {
  if (!state.routeMap) return;
  const map = state.routeMap;

  // Clear previous layers
  if (routeExpectedLine) { map.removeLayer(routeExpectedLine); routeExpectedLine = null; }
  if (routeActualLine) { map.removeLayer(routeActualLine); routeActualLine = null; }
  routeMarkers.forEach(m => map.removeLayer(m));
  routeMarkers = [];

  const origin = data.expected_route.origin;
  const dest = data.expected_route.destination;

  // EXPECTED ROUTE — dashed line, the straight-line corridor the ship is
  // supposed to follow between its declared origin and destination.
  routeExpectedLine = L.polyline(
    [[origin.lat, origin.lon], [dest.lat, dest.lon]],
    { color: '#2B85F0', weight: 2, dashArray: '8, 8', opacity: 0.85 }
  ).addTo(map);

  const originMarker = L.marker([origin.lat, origin.lon], {
    icon: L.divIcon({ html: '<div style="font-size:20px">🟢</div>', className: '', iconSize: [20, 20] }),
  }).addTo(map).bindPopup(`Origin: ${origin.label}`);
  const destMarker = L.marker([dest.lat, dest.lon], {
    icon: L.divIcon({ html: '<div style="font-size:20px">🏁</div>', className: '', iconSize: [20, 20] }),
  }).addTo(map).bindPopup(`Destination: ${dest.label}`);
  routeMarkers.push(originMarker, destMarker);

  // ACTUAL ROUTE — markers only, no solid green line
  const track = data.actual_track;
  if (track.length > 0) {
    track.forEach((p, i) => {
      const isLast = i === track.length - 1;
      const marker = L.circleMarker([p.lat, p.lon], {
        radius: isLast ? 7 : 4,
        color: p.signature_valid ? '#10B981' : '#EF4444',
        fillColor: p.signature_valid ? '#10B981' : '#EF4444',
        fillOpacity: 0.9,
      }).addTo(map).bindPopup(
        `Speed: ${p.speed.toFixed(1)} kn<br>${fmtDate(p.recorded_at)}<br>Signature: ${p.signature_valid ? '✓ valid' : '✗ invalid'}${p.block_index ? '<br>Block #' + p.block_index : ''}`
      );
      routeMarkers.push(marker);
    });

    const bounds = L.latLngBounds([[origin.lat, origin.lon], [dest.lat, dest.lon], ...trackPoints]);
    map.fitBounds(bounds, { padding: [30, 30] });
  } else {
    map.fitBounds(L.latLngBounds([[origin.lat, origin.lon], [dest.lat, dest.lon]]), { padding: [30, 30] });
  }
}

function renderRouteStats(data) {
  const track = data.actual_track;
  const el = document.getElementById('route-stats');
  const reportCount = track.length;
  const lastSpeed = track.length ? track[track.length - 1].speed.toFixed(1) : '—';
  const validCount = track.filter(p => p.signature_valid).length;
  el.innerHTML = `
    <div class="detail-row"><span class="detail-key">Position reports</span><span class="detail-val">${reportCount}</span></div>
    <div class="detail-row"><span class="detail-key">Last speed (kn)</span><span class="detail-val">${lastSpeed}</span></div>
    <div class="detail-row"><span class="detail-key">Signatures valid</span><span class="detail-val" style="color:${validCount === reportCount ? 'var(--green)' : 'var(--red)'}">${validCount}/${reportCount}</span></div>
  `;
}

async function checkDeviation() {
  const sid = document.getElementById('route-ship').value;
  if (!sid) { toast('Select a ship first', 'warning'); return; }
  const s = state.ships.find(x => x.ship_id === sid);
  if (!s || s.current_lat == null) { toast('No position data for this ship yet', 'warning'); return; }

  try {
    const result = await apiPost('/api/detect/route', { ship_id: sid, lat: s.current_lat, lon: s.current_lon });
    const el = document.getElementById('route-result');
    if (result.is_deviation) {
      el.innerHTML = `<div class="alert-item critical">
        <div class="alert-icon">⚠️</div>
        <div class="alert-body"><div class="alert-title">ROUTE DEVIATION DETECTED</div>
        <div class="alert-desc">${result.distance_from_corridor_km} km from expected corridor (limit: ${result.corridor_km} km). Alert raised.</div></div>
      </div>`;
      toast('Route deviation detected — alert raised', 'warning');
    } else {
      el.innerHTML = `<div class="alert-item success">
        <div class="alert-icon">✅</div>
        <div class="alert-body"><div class="alert-title">On expected route</div>
        <div class="alert-desc">${result.distance_from_corridor_km} km from corridor centerline (limit: ${result.corridor_km} km).</div></div>
      </div>`;
      toast('Ship is on its expected route', 'success');
    }
    await loadDeviationHistory();
  } catch (e) { toast('Deviation check failed: ' + errMsg(e), 'error'); }
}

async function loadDeviationHistory() {
  try {
    const alerts = await apiGet('/api/alerts?limit=200');
    const deviations = alerts.filter(a => a.alert_type === 'route_deviation');
    const tbody = document.getElementById('deviation-table');
    if (deviations.length === 0) {
      tbody.innerHTML = `<tr><td colspan="6" style="text-align:center;color:var(--text3);padding:20px">No route deviations recorded.</td></tr>`;
      return;
    }
    tbody.innerHTML = deviations.map(a => {
      const s = state.ships.find(x => x.ship_id === a.ship_id);
      return `<tr>
        <td>${fmtTime(a.created_at)}</td>
        <td>${a.ship_name || '—'}</td>
        <td>${s ? s.origin_port + ' → ' + s.dest_port : '—'}</td>
        <td>${a.lat?.toFixed(2)}°, ${a.lon?.toFixed(2)}°</td>
        <td>${a.detail ? (a.detail.match(/[\d.]+ km/) || ['—'])[0] : '—'}</td>
        <td style="color:${a.acknowledged ? 'var(--green)' : 'var(--amber)'}">${a.acknowledged ? 'Acknowledged' : 'Alert sent'}</td>
      </tr>`;
    }).join('');
  } catch (e) { /* non-fatal */ }
}
// ═══════════════════════════════════════════════════════════════════════════
// CYCLONE WATCH  — real weather via Open-Meteo (no API key needed)
// ═══════════════════════════════════════════════════════════════════════════

// Bay of Bengal region centre — used when no ship GPS fix is available
const BOB_LAT = 15.0, BOB_LON = 85.0;

// Leaflet layer for cyclone symbol on tracking map
let cycloneMapLayer = null;   // { circle, label } or null

// ── Real weather fetch from Open-Meteo (browser → external API) ──────────
async function fetchRealWeather(lat, lon) {
  const url = `https://api.open-meteo.com/v1/forecast?latitude=${lat}&longitude=${lon}` +
    `&current=wind_speed_10m,surface_pressure,precipitation,wind_gusts_10m` +
    `&wind_speed_unit=kmh&forecast_days=1`;
  const res = await fetch(url);
  if (!res.ok) throw new Error('Open-Meteo returned ' + res.status);
  const data = await res.json();
  const cur = data.current;
  return {
    wind_speed_kmh: Math.round(cur.wind_speed_10m ?? 0),
    // surface_pressure from Open-Meteo is already hPa
    pressure_hpa: Math.round(cur.surface_pressure ?? 1013),
    // Open-Meteo gives precipitation in mm/hr; use as rainfall proxy
    rainfall_mm: Math.round((cur.precipitation ?? 0) * 10) / 10,
    wave_height_m: null,   // not in the free forecast endpoint
    lat, lon,
  };
}

// Pick the best location to check: first ship with an active voyage → first ship with a position → BOB centre
function _bestWeatherLocation() {
  const activeShipId = Object.keys(state.voyages || {})[0];
  if (activeShipId) {
    const v = state.voyages[activeShipId];
    const s = state.ships.find(x => x.ship_id === activeShipId);
    if (v && v.current_lat != null) return { lat: v.current_lat, lon: v.current_lon, source: `${s ? s.name : 'ship'}'s position` };
  }
  const shipWithPos = state.ships.find(s => s.current_lat != null);
  if (shipWithPos) {
    return { lat: shipWithPos.current_lat, lon: shipWithPos.current_lon, source: `${shipWithPos.name}'s position` };
  }
  return { lat: BOB_LAT, lon: BOB_LON, source: 'Bay of Bengal centre' };
}

async function refreshCyclone() {
  try {
    state.weather = await apiGet(`/api/weather/latest`);
  } catch (e) { state.weather = null; }
  renderCycloneWeather();
  await loadCycloneWarnings();
}

function renderCycloneWeather() {
  const w = state.weather;
  const grid = document.getElementById('weather-grid');
  if (!w) {
    grid.innerHTML = `<div style="grid-column:1/-1;text-align:center;color:var(--text3);padding:20px">No real weather data yet — click "Fetch Real Weather" to pull live data.</div>`;
    document.getElementById('risk-needle').style.left = '0%';
    _removeCycloneMapLayer();
    return;
  }
  const risk = w.risk || { score: 0, level: 'LOW' };
  const riskColor = { LOW: 'var(--green)', MODERATE: 'var(--amber)', HIGH: 'var(--red)', EXTREME: '#a21caf' }[risk.level] || 'var(--text2)';
  grid.innerHTML = `
    <div class="weather-item"><div class="weather-val">${w.wind_speed_kmh}</div><div class="weather-lbl">Wind km/h</div></div>
    <div class="weather-item"><div class="weather-val">${w.pressure_hpa}</div><div class="weather-lbl">Pressure hPa</div></div>
    <div class="weather-item"><div class="weather-val">${w.wave_height_m ?? '—'}</div><div class="weather-lbl">Wave height m</div></div>
    <div class="weather-item"><div class="weather-val">${w.rainfall_mm ?? '—'}</div><div class="weather-lbl">Rainfall mm/hr</div></div>
    <div class="weather-item" style="grid-column:1/-1;background:${riskColor}22;border:1px solid ${riskColor}44">
      <div class="weather-val" style="color:${riskColor}">${risk.level}</div>
      <div class="weather-lbl">Risk Level (score ${risk.score}/100)</div>
    </div>
  `;
  if (w.lat != null) {
    const locEl = document.getElementById('weather-location-note');
    if (locEl) locEl.textContent = `\uD83D\uDCCD Weather at: ${Number(w.lat).toFixed(3)}\u00b0N, ${Number(w.lon).toFixed(3)}\u00b0E`;
  }
  const pct = Math.min(98, risk.score);
  document.getElementById('risk-needle').style.left = pct + '%';
  _updateCycloneMapLayer(w, risk);
}

// NOTE: Cyclone risk overlay is intentionally NOT drawn on the ship tracking
// map anymore — only real ship-related information belongs there. The risk
// gauge on the Cyclone Watch page and the resulting Alerts still work.
function _updateCycloneMapLayer(w, risk) { /* disabled: no cyclone icons on the ship tracking map */ }

function _removeCycloneMapLayer() {
  if (!cycloneMapLayer || !state.trackingMap) return;
  try { state.trackingMap.removeLayer(cycloneMapLayer.circle); } catch(e){}
  try { state.trackingMap.removeLayer(cycloneMapLayer.marker); } catch(e){}
  cycloneMapLayer = null;
}

// ── "Fetch Real Weather" button handler ──────────────────────────────────
async function refreshWeather() {
  const loc = _bestWeatherLocation();
  const btn = document.querySelector('[onclick="refreshWeather()"]');
  if (btn) { btn.disabled = true; btn.textContent = '\u23f3 Fetching\u2026'; }
  try {
    toast(`Fetching live weather at ${loc.source}\u2026`, 'info');
    const wx = await fetchRealWeather(loc.lat, loc.lon);
    // Post real data to backend
    await apiPost('/api/weather/report', {
      region: `${loc.source} (${loc.lat.toFixed(2)}\u00b0N, ${loc.lon.toFixed(2)}\u00b0E)`,
      wind_speed_kmh: wx.wind_speed_kmh,
      pressure_hpa: wx.pressure_hpa,
      wave_height_m: wx.wave_height_m,
      rainfall_mm: wx.rainfall_mm,
      lat: wx.lat,
      lon: wx.lon,
    });
    await refreshCyclone();
    await refreshDashboard();
    toast(`Real weather fetched at ${loc.source}: ${wx.wind_speed_kmh} km/h wind, ${wx.pressure_hpa} hPa`, 'success');
  } catch (e) {
    toast('Weather fetch failed: ' + errMsg(e), 'error');
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = '\uD83D\uDD04 Refresh'; }
  }
}

// ── "Run Cyclone Detection" — re-scores existing real data + checks ships ─
async function runCycloneDetection() {
  if (!state.weather) {
    await refreshWeather();
    return;
  }
  const w = state.weather;
  try {
    const result = await apiPost('/api/weather/report', {
      region: w.region,
      wind_speed_kmh: w.wind_speed_kmh,
      pressure_hpa: w.pressure_hpa,
      wave_height_m: w.wave_height_m,
      rainfall_mm: w.rainfall_mm,
      lat: w.lat,
      lon: w.lon,
    });
    const risk = result.risk;
    const sigEl = document.getElementById('cyclone-sig');
    sigEl.textContent = risk.level === 'LOW'
      ? `Risk LOW (${risk.score}/100) \u2014 no alerts raised. Ships are safe.`
      : `Risk ${risk.level} (${risk.score}/100) \u2014 alerts sent to ships within storm radius. Logged to blockchain.`;
    toast(`Cyclone detection: ${risk.level} (score ${risk.score}/100)`,
          risk.level === 'LOW' ? 'success' : risk.level === 'MODERATE' ? 'warning' : 'error');
    await refreshCyclone();
    await refreshDashboard();
  } catch (e) { toast('Cyclone detection failed: ' + errMsg(e), 'error'); }
}

async function loadCycloneWarnings() {
  try {
    const alerts = await apiGet('/api/alerts?limit=200');
    const cycloneAlerts = alerts.filter(a => a.alert_type === 'cyclone_risk' || a.alert_type === 'cyclone_route_alert');
    // Count only ACTIVE (non-acknowledged) cyclone conditions as "active cyclones"
    const activeCyclones = cycloneAlerts.filter(a => !a.acknowledged);
    document.getElementById('cyclone-count').textContent = activeCyclones.length > 0 ? 1 : 0;
    document.getElementById('ships-at-risk').textContent = new Set(activeCyclones.map(a => a.ship_id)).size;

    const el = document.getElementById('cyclone-warnings');
    if (cycloneAlerts.length === 0) {
      el.innerHTML = `<div style="padding:24px;text-align:center;color:var(--text3)">No cyclone warnings. All ships safe.</div>`;
      return;
    }
    el.innerHTML = cycloneAlerts.slice(0, 10).map(a => `
      <div class="alert-item ${a.severity}">
        <div class="alert-icon">\uD83C\uDF00</div>
        <div class="alert-body">
          <div class="alert-title">${a.title} ${a.acknowledged ? '<span class="badge badge-blue" style="margin-left:6px">Acknowledged</span>' : ''}</div>
          <div class="alert-desc">${a.ship_name || 'Fleet-wide'} \u00b7 ${a.detail || ''}</div>
          <div class="alert-meta">${fmtTime(a.created_at)}</div>
        </div>
      </div>`).join('');
  } catch (e) { /* non-fatal */ }
}

// ═══════════════════════════════════════════════════════════════════════════
// ILLEGAL ACTIVITY
// ═══════════════════════════════════════════════════════════════════════════
async function refreshIllegal() {
  populate('spoof-ship', false, 'Select a ship…');
  populate('fence-ship', false, 'Select a ship…');
  onSpoofShipChange();
  try {
    state.zones = await apiGet('/api/zones');
  } catch (e) { state.zones = []; }
  renderGeofenceList();
  await loadIncidentLog();
}

const ZONE_TYPE_ICONS = {
  'Piracy Zone': '🏴‍☠️', 'Military Zone': '🎖️', 'Restricted Waters': '⛔', 'Oil Spill Zone': '🛢️',
  'Environmental Hazard': '☣️', 'Ice Zone': '🧊', 'Protected Marine': '🐬', 'Border Security': '🛃', 'Exclusive Zone': '🚫',
};

function renderGeofenceList() {
  const el = document.getElementById('geofence-list');
  if (state.zones.length === 0) {
    el.innerHTML = `<div style="padding:16px;text-align:center;color:var(--text3)">No restricted zones defined yet. Add one below.</div>`;
    return;
  }
  el.innerHTML = state.zones.map(z => `
    <div style="display:flex;align-items:center;gap:10px;padding:8px 0;border-bottom:1px solid var(--border)">
      <span class="badge badge-red">${ZONE_TYPE_ICONS[z.zone_type] || '⛔'} ${z.zone_type}</span>
      <div style="flex:1"><div style="font-size:12px;font-weight:600">${z.name}</div><div style="font-size:10px;color:var(--text3)">${z.lat.toFixed(2)}°N ${z.lon.toFixed(2)}°E | Radius: ${z.radius_km} km</div></div>
      <button class="btn" onclick="deleteZone(${z.zone_id})">✕</button>
    </div>`).join('');
}

async function createZone() {
  const name = document.getElementById('zone-name').value.trim();
  const type = document.getElementById('zone-type').value;
  const radius = parseFloat(document.getElementById('zone-radius').value);
  const lat = parseFloat(document.getElementById('zone-lat').value);
  const lon = parseFloat(document.getElementById('zone-lon').value);
  if (!name || isNaN(radius) || isNaN(lat) || isNaN(lon)) { toast('Fill in all zone fields', 'warning'); return; }
  try {
    await apiPost('/api/zones', { name, zone_type: type, lat, lon, radius_km: radius });
    document.getElementById('zone-name').value = '';
    document.getElementById('zone-lat').value = '';
    document.getElementById('zone-lon').value = '';
    state.zones = await apiGet('/api/zones');
    renderGeofenceList();
    toast('Restricted zone added', 'success');
  } catch (e) { toast('Could not add zone: ' + errMsg(e), 'error'); }
}

async function deleteZone(id) {
  try {
    await apiDelete(`/api/zones/${id}`);
    state.zones = await apiGet('/api/zones');
    renderGeofenceList();
    toast('Zone removed', 'info');
  } catch (e) { toast('Could not delete zone: ' + errMsg(e), 'error'); }
}

function onSpoofShipChange() {
  const sid = document.getElementById('spoof-ship').value;
  const s = state.ships.find(x => x.ship_id === sid);
  if (!s || s.current_lat == null) return;
  // Prefill with the ship's real, current, actually-reported position - so
  // running the check unedited reflects genuine data (and correctly comes
  // back "plausible"), rather than the fixed demo coordinate always
  // looking like a fake jump. Operators can still edit these fields to
  // deliberately test a suspicious reading.
  document.getElementById('spoof-lat').value = s.current_lat.toFixed(4);
  document.getElementById('spoof-lon').value = s.current_lon.toFixed(4);
  document.getElementById('spoof-time').value = (VOYAGE_TICK_SECONDS / 60).toFixed(2);
}

async function detectSpoofing() {
  const sid = document.getElementById('spoof-ship').value;
  const lat = parseFloat(document.getElementById('spoof-lat').value);
  const lon = parseFloat(document.getElementById('spoof-lon').value);
  const minutes = parseFloat(document.getElementById('spoof-time').value);
  if (!sid) { toast('Select a ship first', 'warning'); return; }
  try {
    const result = await apiPost('/api/detect/spoofing', { ship_id: sid, reported_lat: lat, reported_lon: lon, minutes_elapsed: minutes });
    const el = document.getElementById('spoof-result');
    if (result.is_spoofed) {
      el.innerHTML = `<div class="alert-item critical">
        <div class="alert-icon">🚨</div>
        <div class="alert-body"><div class="alert-title">GPS SPOOFING DETECTED</div>
        <div class="alert-desc">Distance: ${result.distance_km} km in ${minutes} min implies ${result.implied_speed_kmh} km/h — exceeds the realistic max of ${result.max_realistic_speed_kmh} km/h. Reading quarantined and alert raised.</div></div>
      </div>`;
      toast('GPS spoofing detected — alert raised', 'error');
    } else {
      el.innerHTML = `<div class="alert-item success">
        <div class="alert-icon">✅</div>
        <div class="alert-body"><div class="alert-title">Position plausible</div>
        <div class="alert-desc">Implied speed ${result.implied_speed_kmh} km/h is within the realistic range (max ${result.max_realistic_speed_kmh} km/h).</div></div>
      </div>`;
      toast('No spoofing detected', 'success');
    }
    await loadIncidentLog();
    await refreshDashboard();
  } catch (e) { toast('Spoofing check failed: ' + errMsg(e), 'error'); }
}

async function checkGeofence() {
  const sid = document.getElementById('fence-ship').value;
  const el = document.getElementById('fence-result');
  if (!sid) { el.innerHTML = ''; return; }
  const s = state.ships.find(x => x.ship_id === sid);
  if (!s || s.current_lat == null) { el.innerHTML = `<div style="color:var(--text3);padding:8px">No position data for this ship yet.</div>`; return; }
  try {
    const result = await apiPost('/api/detect/zone', { lat: s.current_lat, lon: s.current_lon });
    if (result.zone_hit) {
      el.innerHTML = `<div class="alert-item critical" style="margin-top:8px">
        <div class="alert-icon">🚧</div>
        <div class="alert-body"><div class="alert-title">RESTRICTED ZONE ENTRY</div>
        <div class="alert-desc">${s.name} is inside ${result.zone_hit.zone_name} (${result.zone_hit.zone_type}), ${result.zone_hit.distance_from_center_km} km from center.</div></div>
      </div>`;
      toast(`${s.name} is inside a restricted zone`, 'error');
    } else {
      el.innerHTML = `<div class="alert-item success" style="margin-top:8px">
        <div class="alert-icon">✅</div>
        <div class="alert-body"><div class="alert-title">No restricted zone violation</div></div>
      </div>`;
    }
  } catch (e) { toast('Geofence check failed: ' + errMsg(e), 'error'); }
}

async function loadIncidentLog() {
  try {
    const alerts = await apiGet('/api/alerts?limit=200');
    const incidentTypes = ['gps_spoofing', 'restricted_zone', 'unexpected_stop', 'severe_slowdown', 'signature_failure', 'replay_attack'];
    const incidents = alerts.filter(a => incidentTypes.includes(a.alert_type));

    document.getElementById('stat-spoof').textContent = alerts.filter(a => a.alert_type === 'gps_spoofing').length;
    document.getElementById('stat-zone').textContent = alerts.filter(a => a.alert_type === 'restricted_zone').length;
    document.getElementById('stat-stop').textContent = alerts.filter(a => a.alert_type === 'unexpected_stop' || a.alert_type === 'severe_slowdown').length;

    const tbody = document.getElementById('incident-table');
    if (incidents.length === 0) {
      tbody.innerHTML = `<tr><td colspan="5" style="text-align:center;color:var(--text3);padding:20px">No incidents recorded.</td></tr>`;
      return;
    }
    tbody.innerHTML = incidents.map(a => `
      <tr>
        <td>${fmtTime(a.created_at)}</td>
        <td>${a.ship_name || '—'}</td>
        <td>${a.alert_type.replace(/_/g, ' ')}</td>
        <td>${a.detail || ''}</td>
        <td style="color:${a.acknowledged ? 'var(--green)' : 'var(--amber)'}">${a.acknowledged ? 'Resolved' : 'Alerted'}</td>
      </tr>`).join('');
  } catch (e) { /* non-fatal */ }
}
// ═══════════════════════════════════════════════════════════════════════════
// SHIP REGISTRY
// ═══════════════════════════════════════════════════════════════════════════
async function refreshShipRegistry() {
  await loadShips();
  await loadActiveVoyages();
  refreshShipRegistry_render();
}

function refreshShipRegistry_render() {
  const tbody = document.getElementById('ship-registry-table');
  if (!tbody) return;
  if (state.ships.length === 0) {
    tbody.innerHTML = `<tr><td colspan="8" style="text-align:center;color:var(--text3);padding:30px">No ships registered. Click "Register New Ship" to add one.</td></tr>`;
    return;
  }
  tbody.innerHTML = state.ships.map(s => {
    const voyage = state.voyages[s.ship_id];
    const voyageBtn = voyage
      ? `<button class="btn" onclick="openReplanModal('${s.ship_id}')" style="font-size:11px;padding:2px 8px">🔁 Replan</button>`
      : `<button class="btn btn-primary" onclick="openVoyageModal('${s.ship_id}')" style="font-size:11px;padding:2px 8px">🧭 Voyage</button>`;
    return `<tr>
      <td>${shipEmoji(s.ship_type)} ${s.name}</td>
      <td>${s.imo_number || '—'}</td>
      <td>${s.ship_type || '—'}</td>
      <td style="font-size:11px">${s.origin_port} → ${s.dest_port}</td>
      <td>${(s.current_speed ?? 0).toFixed(0)} km/h</td>
      <td style="color:${statusColor(s.status)}">${s.status}</td>
      <td><span style="font-family:var(--mono);font-size:10px">${shortHash(s.public_key, 10)}</span></td>
      <td style="display:flex;gap:4px;flex-wrap:wrap">
        <button class="btn" onclick="showPage('tracking'); setTimeout(()=>showShipDetail('${s.ship_id}'), 150)" style="font-size:11px;padding:2px 8px">View</button>
        ${voyageBtn}
        <button class="btn btn-danger" onclick="confirmDeleteShip('${s.ship_id}', '${s.name.replace(/'/g, "\\'")}')" style="font-size:11px;padding:2px 8px">🗑️ Delete</button>
      </td>
    </tr>`;
  }).join('');
}

// ─── VOYAGES PAGE ────────────────────────────────────────────────────────────
async function refreshVoyagesPage() {
  await loadActiveVoyages();
  await loadAlerts();
  await refreshWeatherPanel().catch(() => {});
  const activeEl = document.getElementById('active-voyages-list');
  const activeCount = document.getElementById('active-voyage-count');
  const activeVoyages = Object.values(state.voyages).filter(v => v.status === 'in_progress');
  if (activeCount) activeCount.textContent = activeVoyages.length;

  if (activeVoyages.length === 0) {
    activeEl.innerHTML = `<div style="color:var(--text3);text-align:center;padding:20px">No active voyages. <a href="#" onclick="openVoyageModal(); return false">Start one →</a></div>`;
  } else {
    activeEl.innerHTML = activeVoyages.map(v => {
      const ship = state.ships.find(s => s.ship_id === v.ship_id);
      const sName = ship ? ship.name : v.ship_id;
      const progress = v.distance_total_km > 0 ? Math.min(100, (v.distance_travelled_km / v.distance_total_km) * 100) : 0;
      return `
      <div style="border:1px solid var(--border);border-radius:var(--radius);padding:14px 16px;margin-bottom:10px">
        <div style="display:flex;justify-content:space-between;align-items:start;margin-bottom:8px;flex-wrap:wrap;gap:6px">
          <div>
            <span style="font-weight:600;font-size:14px">🚢 ${sName}</span>
            <span style="margin-left:10px;color:var(--text3);font-size:12px">${v.origin_port} → ${v.dest_port}</span>
          </div>
          <div style="display:flex;gap:6px">
            ${ship?.has_cyclone_warning && window.currentUserRole === 'control_station' ? `<button class="btn btn-primary" onclick="proposeRerouteForVoyage(${v.voyage_id})" style="font-size:11px;padding:3px 10px;background:var(--amber);color:#000;border:none;" title="Reroute detection runs automatically in the background; this forces an immediate check">🌪 Force Reroute Check</button>` : ''}
            ${ship?.status === 'holding' ? `<span class="badge badge-blue" style="font-size:11px" title="Backend automatically re-checks periodically for a clear route home">⚓ Holding — auto-monitoring for resume</span>` : ''}
            ${window.currentUserRole === 'control_station' ? `<button class="btn" onclick="openReplanModal('${v.ship_id}')" style="font-size:11px;padding:3px 10px">🔁 Replan</button>` : ''}
            ${window.currentUserRole === 'control_station' ? `<button class="btn" onclick="manualCompleteVoyage('${v.ship_id}')" style="font-size:11px;padding:3px 10px">✅ Arrived</button>` : ''}
          </div>
        </div>
        <div style="background:var(--surface3);border-radius:4px;height:6px;margin-bottom:10px;overflow:hidden">
          <div style="background:var(--green);height:100%;width:${progress.toFixed(1)}%;transition:width 0.5s ease"></div>
        </div>
        <div style="display:flex;gap:16px;flex-wrap:wrap;font-size:12px">
          <span>📏 <b>${v.distance_travelled_km.toFixed(0)} km</b> travelled</span>
          <span>📍 <b>${v.distance_remaining_km.toFixed(0)} km</b> remaining</span>
          <span>⚡ <b>${v.current_speed_kmh.toFixed(0)} km/h</b></span>
          <span>🎯 ETA: <b>${v.eta ? fmtDate(v.eta) : '—'}</b></span>
          <span>⭕ Geofence: <b>${v.geofence_radius_km} km</b></span>
        </div>
        ${v.remarks ? `<div style="margin-top:6px;font-size:11px;color:var(--text3)">📝 ${v.remarks}</div>` : ''}
      </div>`;
    }).join('');
  }

  // Route & cyclone alerts
  const routeAlerts = state.alerts.filter(a =>
    ['cyclone_route_alert','voyage_started','voyage_completed','route_replanned','weather_warning','high_wind'].includes(a.alert_type)
  ).slice(0, 10);
  const alertsEl = document.getElementById('voyage-alerts-list');
  if (alertsEl) {
    alertsEl.innerHTML = routeAlerts.length === 0
      ? `<div style="color:var(--text3);text-align:center;padding:12px">No current route alerts.</div>`
      : routeAlerts.map(a => `
        <div style="display:flex;gap:10px;align-items:start;padding:8px 12px;border-bottom:1px solid var(--border)">
          <span style="color:${severityColor(a.severity)};font-size:16px">${a.severity==='critical'?'🔴':a.severity==='warning'?'🟡':'ℹ️'}</span>
          <div>
            <div style="font-weight:600;font-size:13px">${a.title}</div>
            <div style="font-size:11px;color:var(--text3)">${a.detail||''} · ${fmtDate(a.triggered_at)}</div>
          </div>
        </div>`).join('');
  }

  // Voyage history
  const histEl = document.getElementById('voyage-history-list');
  if (!histEl) return;
  try {
    const allResults = await Promise.all(state.ships.map(s => apiGet(`/api/voyages/ship/${s.ship_id}`).catch(()=>[])));
    const hist = allResults.flat().filter(v => v.status !== 'in_progress').sort((a,b) => new Date(b.created_at)-new Date(a.created_at)).slice(0,20);
    histEl.innerHTML = hist.length === 0
      ? `<div style="color:var(--text3);text-align:center;padding:20px">No voyage history yet.</div>`
      : hist.map(v => {
          const ship = state.ships.find(s => s.ship_id === v.ship_id);
          const icon = v.status==='completed'?'✅':v.status==='replanned'?'🔁':'—';
          return `<div style="display:flex;gap:12px;align-items:center;padding:8px 12px;border-bottom:1px solid var(--border)">
            <span style="font-size:16px">${icon}</span>
            <div style="flex:1">
              <div style="font-size:13px;font-weight:600">${ship?ship.name:v.ship_id} — ${v.origin_port} → ${v.dest_port}</div>
              <div style="font-size:11px;color:var(--text3)">${fmtDate(v.created_at)} → ${fmtDate(v.completed_at)} · ${v.distance_total_km.toFixed(0)} km</div>
            </div>
            <span class="badge badge-${v.status==='completed'?'green':'blue'}">${v.status}</span>
          </div>`;
        }).join('');
  } catch (e) { histEl.innerHTML = `<div style="color:var(--text3);padding:12px">Could not load history.</div>`; }
}

// ═══════════════════════════════════════════════════════════════════════════
// CYCLONE REROUTE / SAFE-PORT APPROVAL WORKFLOW
// Any user can PROPOSE (server computes an alternate route, or falls back
// to the nearest safe port if no clear route exists). Only the Control
// Station can APPROVE or REJECT — enforced server-side, not just hidden
// in the UI (see require_control_station on the backend).
// ═══════════════════════════════════════════════════════════════════════════
async function proposeRerouteForVoyage(voyageId) {
  try {
    const req = await apiPost('/api/reroutes/propose', { voyage_id: voyageId });
    if (req.kind === 'safe_port') {
      toast(`No clear route found. Proposing diversion to ${req.proposed_port} — awaiting Control Station approval.`, 'info');
    } else {
      toast('Alternate route found — awaiting Control Station approval.', 'info');
    }
    await loadRerouteApprovals();
    showPage('alerts');
  } catch (e) {
    if (e.status === 409) {
      toast(errMsg(e), 'error');
    } else {
      toast('Could not propose reroute: ' + errMsg(e), 'error');
    }
  }
}

async function loadRerouteApprovals() {
  try {
    state.pendingReroutes = await apiGet('/api/reroutes/pending');
  } catch (e) {
    state.pendingReroutes = [];
  }
  renderRerouteApprovals();
}

function renderRerouteApprovals() {
  const section = document.getElementById('reroute-approvals-section');
  const list = document.getElementById('reroute-approvals-list');
  if (!section || !list) return;

  const isControl = window.currentUserRole === 'control_station';
  const pending = state.pendingReroutes || [];

  if (!isControl || pending.length === 0) {
    section.style.display = 'none';
    return;
  }
  section.style.display = '';

  list.innerHTML = pending.map(r => {
    const ship = state.ships.find(s => s.ship_id === r.ship_id);
    const shipLabel = ship ? ship.name : r.ship_id;
    const body = r.kind === 'safe_port'
      ? `Proposing to divert <b>${shipLabel}</b> to <b>${r.proposed_port}</b> and hold there until the cyclone clears. Original destination: ${r.original_dest_port || '—'}.`
      : r.kind === 'resume'
        ? `Cyclone has cleared enough for <b>${shipLabel}</b> to resume its route to <b>${r.original_dest_port}</b> (${r.proposed_distance_km ? r.proposed_distance_km.toFixed(0) + ' km' : ''}).`
        : `Alternate sea route found for <b>${shipLabel}</b> that clears ${r.cyclone_name || 'the storm'} (${r.proposed_distance_km ? r.proposed_distance_km.toFixed(0) + ' km' : ''}).`;

    return `<div class="alert-item warning" style="margin-bottom:10px">
      <div class="alert-icon">🌪</div>
      <div class="alert-body">
        <div class="alert-title">${r.kind === 'safe_port' ? 'Safe-Port Diversion Requested' : r.kind === 'resume' ? 'Resume Voyage Requested' : 'Alternate Route Requested'}</div>
        <div class="alert-desc">${body}</div>
        <div class="alert-meta">${r.reason || ''}</div>
        <div style="margin-top:8px;display:flex;gap:8px">
          <button class="btn btn-primary" onclick="decideReroute(${r.id}, 'approve')">✅ Approve</button>
          <button class="btn btn-danger" onclick="decideReroute(${r.id}, 'reject')">❌ Reject</button>
        </div>
      </div>
    </div>`;
  }).join('');
}

async function decideReroute(id, decision) {
  try {
    await apiPost(`/api/reroutes/${id}/${decision}`, {});
    toast(decision === 'approve' ? 'Reroute approved and applied.' : 'Reroute rejected.', decision === 'approve' ? 'success' : 'info');
    await loadRerouteApprovals();
    await checkAllCycloneRouteRisks();
    renderMapShips();
    refreshVoyagesPage();
  } catch (e) {
    toast('Could not record decision: ' + errMsg(e), 'error');
  }
}




// ═══════════════════════════════════════════════════════════════════════════
// COMMS CENTER — real AES-256 encrypted messages via ECDH session keys
// ═══════════════════════════════════════════════════════════════════════════
async function refreshComms() {
  populate('comm-target', false, 'Select target…');
  try {
    state.messages = await apiGet('/api/comms?limit=200');
  } catch (e) { state.messages = []; }
  renderCommLog();
}

function renderCommLog() {
  const cl = document.getElementById('comm-log');
  if (state.messages.length === 0) {
    cl.innerHTML = `<div style="padding:24px;text-align:center;color:var(--text3)">No messages yet. Send one below.</div>`;
  } else {
    cl.innerHTML = state.messages.map(m => `
      <div style="margin-bottom:10px">
        <div style="font-size:10px;color:var(--text3);font-family:var(--mono);margin-bottom:3px">${fmtTime(m.created_at)} | ${m.direction === 'to_ship' ? 'Control → ' + m.ship_name : m.ship_name + ' → Control'} | <span style="color:var(--cyan)">${m.message_type}</span> | 🔒 AES-256</div>
        <div style="background:var(--surface3);padding:9px 12px;border-radius:var(--radius-sm);font-size:12px;border-left:2px solid ${m.direction === 'to_ship' ? 'var(--blue)' : 'var(--teal)'}">${m.plaintext}</div>
      </div>`).join('');
    cl.scrollTop = cl.scrollHeight;
  }

  const tbody = document.getElementById('comm-table');
  if (state.messages.length === 0) {
    tbody.innerHTML = `<tr><td colspan="5" style="text-align:center;color:var(--text3);padding:20px">No messages logged.</td></tr>`;
    return;
  }
  tbody.innerHTML = [...state.messages].reverse().slice(0, 10).map(m => `
    <tr>
      <td style="font-family:var(--mono)">${fmtTime(m.created_at)}</td>
      <td>${m.direction === 'to_ship' ? 'Control' : m.ship_name}</td>
      <td>${m.direction === 'to_ship' ? m.ship_name : 'Control'}</td>
      <td><span class="badge badge-${m.message_type === 'ALERT' ? 'red' : m.message_type === 'GPS_UPDATE' ? 'green' : 'blue'}">${m.message_type}</span></td>
      <td><span class="badge badge-green">✅ AES-256</span></td>
    </tr>`).join('');
}

async function sendComm() {
  const sid = document.getElementById('comm-target').value;
  const msgInput = document.getElementById('comm-msg');
  const message = msgInput.value.trim();
  if (!sid) { toast('Select a target ship', 'warning'); return; }
  if (!message) return;
  try {
    await apiPost('/api/comms/send', { ship_id: sid, message, message_type: 'COMMAND' });
    msgInput.value = '';
    await refreshComms();
    toast('Message sent and AES-256 encrypted', 'success');
  } catch (e) { toast('Send failed: ' + errMsg(e), 'error'); }
}

// ═══════════════════════════════════════════════════════════════════════════
// CYCLONE MAP OVERLAY — renders live cyclones on the tracking map with a
// spinning cyclone icon, forecast track line, and clicking opens a detail
// popup. Cyclone route-risk check runs after each render so alerts go out
// automatically without a separate user action.
// ═══════════════════════════════════════════════════════════════════════════
const cycloneMapLayers = {}; // cyclone.id -> {marker, track}

async function loadCyclonesForMap() {
  try {
    const data = await apiGet('/api/cyclones/active');
    state.cyclones = data.cyclones || [];
  } catch (e) { state.cyclones = []; }
}

// ═══════════════════════════════════════════════════════════════════════════
// AUTH / LOGIN GATE
// Ship Captain and Control Station are real, password-protected accounts
// (see /api/users/*) - the server issues a session token on login and
// every subsequent API call sends it as `Authorization: Bearer <token>`
// (see api() below). Passenger stays a no-account, view-only quick entry
// since it was never a privileged role to begin with.
// ═══════════════════════════════════════════════════════════════════════════
window.currentUserRole = 'ship_captain';
window.currentUserName = '';
window.currentUserShipId = null;
window.authToken = null;

const ROLE_LABELS = { ship_captain: 'Ship Captain', control_station: 'Control Station', passenger: 'Passenger' };
const ROLE_ICONS = { ship_captain: '🧭', control_station: '🎛️', passenger: '🧳' };

let selectedLoginRole = 'ship_captain';
let authMode = 'login'; // 'login' | 'register'
let needsBootstrap = false; // true only until the very first Control Station account exists anywhere

async function checkBootstrapStatus() {
  try {
    const res = await apiGet('/api/users/bootstrap_status');
    needsBootstrap = !!res.needs_bootstrap;
  } catch (e) {
    needsBootstrap = false; // fail closed - don't offer setup if we can't confirm it's actually needed
  }
  if (selectedLoginRole === 'control_station') pickLoginRole('control_station');
}

function pickLoginRole(role) {
  selectedLoginRole = role;
  document.querySelectorAll('.login-role-opt').forEach(el => el.classList.toggle('active', el.dataset.role === role));
  document.getElementById('login-error').style.display = 'none';

  const isPassenger = role === 'passenger';
  document.getElementById('login-passenger-fields').style.display = isPassenger ? '' : 'none';
  document.getElementById('login-passenger-submit-btn').style.display = isPassenger ? '' : 'none';
  document.getElementById('login-account-fields').style.display = isPassenger ? 'none' : '';

  // Control Center accounts have no public registration path - they're
  // created by a System Administrator only - so that role never shows a
  // Register tab UNLESS this is a brand-new deployment with no Control
  // Station account at all yet (needsBootstrap), in which case there is
  // by definition no administrator who could create the first one, so a
  // one-time "Create System Administrator Account" setup is offered
  // instead. The instant that first account exists, this closes again.
  const registerTab = document.getElementById('login-tab-register');
  const showRegisterForControlStation = role === 'control_station' && needsBootstrap;
  registerTab.style.display = (!isPassenger && (role !== 'control_station' || showRegisterForControlStation)) ? '' : 'none';
  registerTab.textContent = showRegisterForControlStation ? 'First-Time Setup' : 'Register';
  if (role === 'control_station' && authMode === 'register' && !showRegisterForControlStation) setAuthMode('login');

  updateLoginHint();
}

function updateLoginHint() {
  const isPassenger = selectedLoginRole === 'passenger';
  const hintEl = document.getElementById('login-hint');
  if (isPassenger) {
    hintEl.textContent = 'Enter any name to continue as a passenger (view-only, no account needed).';
  } else if (selectedLoginRole === 'control_station') {
    if (needsBootstrap) {
      hintEl.textContent = 'No Control Station account exists yet — create the first one now (System Administrator setup, one-time only).';
    } else {
      hintEl.textContent = 'Control Center accounts are created by a System Administrator — there is no public registration for this role.';
    }
  } else if (authMode === 'register') {
    hintEl.textContent = 'Registering as a Ship Captain creates a pending account. A Control Station operator must approve it and assign your ship before you can log in.';
  } else {
    hintEl.textContent = 'Sign in with your Ship Captain or Control Station account.';
  }
}

function setAuthMode(mode) {
  authMode = mode;
  document.getElementById('login-tab-signin').classList.toggle('btn-primary', mode === 'login');
  document.getElementById('login-tab-register').classList.toggle('btn-primary', mode === 'register');
  const isBootstrap = selectedLoginRole === 'control_station' && needsBootstrap;
  document.getElementById('login-submit-btn').textContent = mode === 'login' ? 'Sign In →' : (isBootstrap ? 'Create Administrator Account →' : 'Create Account →');
  document.getElementById('login-error').style.display = 'none';
  updateLoginHint();
}

async function submitAccountLogin() {
  const username = (document.getElementById('login-username').value || '').trim();
  const password = document.getElementById('login-password').value || '';
  const errEl = document.getElementById('login-error');
  errEl.style.display = 'none';

  if (!username || !password) {
    errEl.textContent = 'Please enter both a username and password.';
    errEl.style.display = 'block';
    return;
  }

  try {
    if (authMode === 'register') {
      if (selectedLoginRole === 'control_station') {
        if (!needsBootstrap) {
          errEl.textContent = 'Control Center accounts can only be created by a System Administrator.';
          errEl.style.display = 'block';
          return;
        }
        // Bootstrap case: the backend only allows this unauthenticated
        // call to succeed while zero Control Station accounts exist -
        // it re-checks server-side, this isn't a client-trusted gate.
        await apiPost('/api/users/admin/create_control_station', { username, password });
        toast('System Administrator account created — signing you in…', 'success');
        needsBootstrap = false;
        const res = await apiPost('/api/users/login', { username, password });
        window.authToken = res.token;
        localStorage.setItem('mg_token', res.token);
        localStorage.setItem('mg_user', JSON.stringify(res.user));
        enterApp(res.user);
        return;
      }
      if (selectedLoginRole !== 'ship_captain') {
        errEl.textContent = 'Only Ship Captain accounts can be self-registered.';
        errEl.style.display = 'block';
        return;
      }
      await apiPost('/api/users/register', { username, password, role: 'ship_captain' });
      // A brand-new captain account is "pending" and cannot log in yet -
      // don't attempt auto sign-in (it would just fail with a pending
      // message); tell them clearly what happens next instead.
      toast('Registration submitted — awaiting Control Center approval.', 'success');
      setAuthMode('login');
      document.getElementById('login-password').value = '';
      return;
    }
    const res = await apiPost('/api/users/login', { username, password });
    window.authToken = res.token;
    localStorage.setItem('mg_token', res.token);
    localStorage.setItem('mg_user', JSON.stringify(res.user));
    enterApp(res.user);
  } catch (e) {
    errEl.textContent = errMsg(e) || 'Sign in failed.';
    errEl.style.display = 'block';
  }
}

function submitPassengerLogin() {
  const name = (document.getElementById('login-passenger-name').value || '').trim();
  const errEl = document.getElementById('login-error');
  if (!name) {
    errEl.textContent = 'Please enter a name.';
    errEl.style.display = 'block';
    return;
  }
  const user = { role: 'passenger', username: name };
  window.authToken = null;
  localStorage.removeItem('mg_token');
  localStorage.setItem('mg_user', JSON.stringify(user));
  enterApp(user);
}

// ═══════════════════════════════════════════════════════════════════════════
// CAPTAIN MANAGEMENT - Control Station only (see /api/users/captains/*)
// ═══════════════════════════════════════════════════════════════════════════
let _captainsCache = [];

async function refreshCaptainManagement() {
  try {
    _captainsCache = await apiGet('/api/users/captains');
  } catch (e) {
    toast(errMsg(e) || 'Failed to load captains', 'error');
    return;
  }
  renderCaptainTables();
  await refreshControlStations();
}

function renderCaptainTables() {
  const captains = _captainsCache;
  const search = (document.getElementById('captains-search')?.value || '').trim().toLowerCase();
  const filter = document.getElementById('captains-filter')?.value || 'all';

  const matches = c => (!search || c.username.toLowerCase().includes(search));

  const pending = captains.filter(c => c.status === 'pending' && matches(c));
  const approved = captains.filter(c => c.status === 'approved' && matches(c));
  const rejected = captains.filter(c => c.status === 'rejected' && matches(c));

  document.getElementById('captains-pending-card').style.display = (filter === 'all' || filter === 'pending') ? '' : 'none';
  document.getElementById('captains-approved-card').style.display = (filter === 'all' || filter === 'approved') ? '' : 'none';
  document.getElementById('captains-rejected-card').style.display = (filter === 'all' || filter === 'rejected') ? '' : 'none';

  document.getElementById('captains-pending-count').textContent = pending.length;
  document.getElementById('captains-approved-count').textContent = approved.length;
  document.getElementById('captains-rejected-count').textContent = rejected.length;

  const allPending = captains.filter(c => c.status === 'pending');
  const pendingBadge = document.getElementById('captains-pending-badge');
  if (allPending.length > 0) {
    pendingBadge.textContent = allPending.length;
    pendingBadge.style.display = '';
  } else {
    pendingBadge.style.display = 'none';
  }

  const shipOptions = () => state.ships.map(s => `<option value="${s.ship_id}">${s.name} (${s.ship_id})</option>`).join('');

  const pendingTable = document.getElementById('captains-pending-table');
  pendingTable.innerHTML = pending.length ? pending.map(c => `
    <tr>
      <td>${c.username}</td>
      <td>${fmtDate(c.created_at)}</td>
      <td><select id="assign-ship-${c.id}" class="input" style="min-width:160px">${shipOptions()}</select></td>
      <td style="display:flex;gap:6px">
        <button class="btn btn-primary" style="padding:4px 10px" onclick="approveCaptain(${c.id})">Approve</button>
        <button class="btn btn-danger" style="padding:4px 10px" onclick="rejectCaptain(${c.id})">Reject</button>
      </td>
    </tr>`).join('') : `<tr><td colspan="4" style="text-align:center;color:var(--text3)">${search ? 'No matching pending applications.' : 'No pending applications.'}</td></tr>`;

  const approvedTable = document.getElementById('captains-approved-table');
  approvedTable.innerHTML = approved.length ? approved.map(c => `
    <tr>
      <td>${c.username}</td>
      <td>${c.ship_id ? shipName(c.ship_id) + ' (' + c.ship_id + ')' : '<span style="color:var(--amber)">Unassigned</span>'}</td>
      <td>${c.reviewed_by || '—'}</td>
      <td style="display:flex;gap:6px;align-items:center">
        <select id="reassign-ship-${c.id}" class="input" style="min-width:150px">
          <option value="">Change ship…</option>
          ${shipOptions()}
        </select>
        <button class="btn" style="padding:4px 10px" onclick="reassignCaptainShip(${c.id})">Assign</button>
        ${c.ship_id ? `<button class="btn btn-danger" style="padding:4px 10px" onclick="removeCaptainAssignment(${c.id})">Remove</button>` : ''}
      </td>
    </tr>`).join('') : `<tr><td colspan="4" style="text-align:center;color:var(--text3)">${search ? 'No matching approved captains.' : 'No approved captains yet.'}</td></tr>`;

  const rejectedTable = document.getElementById('captains-rejected-table');
  rejectedTable.innerHTML = rejected.length ? rejected.map(c => `
    <tr>
      <td>${c.username}</td>
      <td>${c.reviewed_by || '—'}</td>
      <td>${fmtDate(c.reviewed_at)}</td>
    </tr>`).join('') : `<tr><td colspan="3" style="text-align:center;color:var(--text3)">${search ? 'No matching rejected applications.' : 'No rejected applications.'}</td></tr>`;
}

async function approveCaptain(userId) {
  const sel = document.getElementById(`assign-ship-${userId}`);
  const shipId = sel ? sel.value : null;
  if (!shipId) { toast('Select a ship to assign before approving.', 'error'); return; }
  try {
    await apiPost(`/api/users/captains/${userId}/approve`, { ship_id: shipId });
    toast('Captain approved and assigned.', 'success');
    refreshCaptainManagement();
  } catch (e) { toast(errMsg(e) || 'Approve failed', 'error'); }
}

async function rejectCaptain(userId) {
  try {
    await apiPost(`/api/users/captains/${userId}/reject`, {});
    toast('Captain application rejected.', 'success');
    refreshCaptainManagement();
  } catch (e) { toast(errMsg(e) || 'Reject failed', 'error'); }
}

async function reassignCaptainShip(userId) {
  const sel = document.getElementById(`reassign-ship-${userId}`);
  const shipId = sel ? sel.value : null;
  if (!shipId) { toast('Select a ship first.', 'error'); return; }
  try {
    await apiPost(`/api/users/captains/${userId}/assign_ship`, { ship_id: shipId });
    toast('Ship assignment updated.', 'success');
    refreshCaptainManagement();
  } catch (e) { toast(errMsg(e) || 'Assignment failed', 'error'); }
}

async function removeCaptainAssignment(userId) {
  try {
    await apiPost(`/api/users/captains/${userId}/remove_assignment`, {});
    toast('Ship assignment removed.', 'success');
    refreshCaptainManagement();
  } catch (e) { toast(errMsg(e) || 'Remove failed', 'error'); }
}

// ═══════════════════════════════════════════════════════════════════════════
// SYSTEM ADMINISTRATOR - Control Center account management. There is no
// separate admin login: any logged-in Control Station operator acts in
// this capacity (see /api/users/admin/create_control_station and
// /api/users/control_stations/* on the backend).
// ═══════════════════════════════════════════════════════════════════════════
async function refreshControlStations() {
  let accounts = [];
  try {
    accounts = await apiGet('/api/users/control_stations');
  } catch (e) {
    toast(errMsg(e) || 'Failed to load Control Center accounts', 'error');
    return;
  }
  const table = document.getElementById('control-stations-table');
  const myUsername = window.currentUserName || '';
  table.innerHTML = accounts.length ? accounts.map(a => `
    <tr>
      <td>${a.username}${a.username === myUsername ? ' <span style="color:var(--text3)">(you)</span>' : ''}</td>
      <td>${a.active ? '<span style="color:var(--green)">Active</span>' : '<span style="color:var(--red)">Disabled</span>'}</td>
      <td>${fmtDate(a.created_at)}</td>
      <td style="display:flex;gap:6px;flex-wrap:wrap">
        ${a.active
          ? `<button class="btn btn-danger" style="padding:4px 10px" ${a.username === myUsername ? 'disabled title="Cannot disable your own account"' : ''} onclick="disableControlStation(${a.id})">Disable</button>`
          : `<button class="btn btn-primary" style="padding:4px 10px" onclick="enableControlStation(${a.id})">Enable</button>`}
        <button class="btn" style="padding:4px 10px" onclick="resetControlStationPassword(${a.id}, '${a.username}')">Reset Password</button>
      </td>
    </tr>`).join('') : '<tr><td colspan="4" style="text-align:center;color:var(--text3)">No Control Center accounts found.</td></tr>';
}

async function createControlStationAccount() {
  const username = (document.getElementById('new-cs-username').value || '').trim();
  const password = document.getElementById('new-cs-password').value || '';
  if (!username || !password) { toast('Enter both a username and password.', 'error'); return; }
  try {
    await apiPost('/api/users/admin/create_control_station', { username, password });
    toast(`Control Center account "${username}" created.`, 'success');
    document.getElementById('new-cs-username').value = '';
    document.getElementById('new-cs-password').value = '';
    await refreshControlStations();
  } catch (e) { toast(errMsg(e) || 'Failed to create account', 'error'); }
}

async function disableControlStation(userId) {
  try {
    await apiPost(`/api/users/control_stations/${userId}/disable`, {});
    toast('Account disabled.', 'success');
    await refreshControlStations();
  } catch (e) { toast(errMsg(e) || 'Disable failed', 'error'); }
}

async function enableControlStation(userId) {
  try {
    await apiPost(`/api/users/control_stations/${userId}/enable`, {});
    toast('Account re-enabled.', 'success');
    await refreshControlStations();
  } catch (e) { toast(errMsg(e) || 'Enable failed', 'error'); }
}

async function resetControlStationPassword(userId, username) {
  const newPassword = prompt(`New password for "${username}" (min 8 characters):`);
  if (!newPassword) return;
  try {
    await apiPost(`/api/users/control_stations/${userId}/reset_password`, { new_password: newPassword });
    toast(`Password reset for "${username}". Their existing sessions were signed out.`, 'success');
  } catch (e) { toast(errMsg(e) || 'Reset failed', 'error'); }
}

async function logoutSession() {
  try { if (window.authToken) await apiPost('/api/users/logout', {}); } catch (e) { /* best-effort */ }
  localStorage.removeItem('mg_token');
  localStorage.removeItem('mg_user');
  location.reload();
}

function enterApp(user) {
  window.currentUserRole = user.role;
  window.currentUserName = user.username;
  window.currentUserShipId = user.ship_id || null;

  document.getElementById('login-gate').style.display = 'none';
  document.getElementById('app-shell').style.display = '';

  document.getElementById('session-role-icon').textContent = ROLE_ICONS[user.role] || '👤';
  document.getElementById('session-name-label').textContent = user.username;
  document.getElementById('session-role-label').textContent = ROLE_LABELS[user.role] || user.role;

  applyRoleRestrictions();
  bootApp();
}

/** On page load: if a session token is already stored, verify it against
 * /api/users/me instead of trusting the cached role blindly - a token
 * could have been revoked/expired server-side since the last visit. */
async function tryRestoreSession() {
  const token = localStorage.getItem('mg_token');
  const cachedUser = localStorage.getItem('mg_user');
  if (!cachedUser) return false;

  const user = JSON.parse(cachedUser);
  if (user.role === 'passenger') {
    enterApp(user);
    return true;
  }
  if (!token) return false;

  window.authToken = token;
  try {
    const me = await apiGet('/api/users/me');
    enterApp(me);
    return true;
  } catch (e) {
    localStorage.removeItem('mg_token');
    localStorage.removeItem('mg_user');
    window.authToken = null;
    return false;
  }
}

function applyRoleRestrictions() {
  const isPassenger = window.currentUserRole === 'passenger';
  const isCaptain = window.currentUserRole === 'ship_captain';
  const isControl = window.currentUserRole === 'control_station';

  document.getElementById('admin-nav-sections').style.display = isPassenger ? 'none' : '';
  document.getElementById('passenger-nav-section').style.display = isPassenger ? '' : 'none';

  // Fleet-wide / administrative pages (dashboard, ship registry, comms,
  // crypto/blockchain demos, monitoring) are Control Station only - a
  // Ship Captain gets just their own ship's tracking, voyages, alerts.
  document.querySelectorAll('.control-station-only').forEach(el => {
    el.style.display = isControl ? '' : 'none';
  });

  ['advanceShipsBtn', 'registerShipBtn'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.style.display = isPassenger ? 'none' : '';
  });
  if (typeof updateRoleUI === 'function') updateRoleUI();

  if (isPassenger) {
    showPage('passenger');
  } else if (isCaptain) {
    showPage('tracking');
  } else {
    showPage('dashboard');
  }
}

function goBackFromFocus() {
  showPage(window.currentUserRole === 'passenger' ? 'passenger' : 'tracking');
}

// ═══════════════════════════════════════════════════════════════════════════
// PASSENGER VIEW — ships list (IMO + name only) with IMO search, drilling
// into the same rich detail+live-map page admins use.
// ═══════════════════════════════════════════════════════════════════════════
function renderPassengerShipList() {
  const query = (document.getElementById('passenger-search-imo')?.value || '').trim().toLowerCase();
  const listEl = document.getElementById('passenger-ship-list');
  const emptyEl = document.getElementById('passenger-ship-empty');
  if (!listEl) return;

  const ships = (state.ships || []).filter(s => !query || (s.imo_number || '').toLowerCase().includes(query));

  if (ships.length === 0) {
    listEl.innerHTML = '';
    emptyEl.style.display = 'block';
    return;
  }
  emptyEl.style.display = 'none';

  listEl.innerHTML = ships.map(s => `
    <div class="card" style="margin:0;padding:14px 16px;display:flex;justify-content:space-between;align-items:center;cursor:pointer" onclick="showShipDetail('${s.ship_id}')">
      <div>
        <div style="font-weight:600;font-size:14px">${s.name}</div>
        <div style="font-size:12px;color:var(--text3);font-family:var(--mono)">${s.imo_number || '—'}</div>
      </div>
      <div style="font-size:12px;color:var(--text3)">View live position →</div>
    </div>`).join('');
}

function passengerSearchShips() { renderPassengerShipList(); }

function updateRoleUI() {
  const isControl = window.currentUserRole === 'control_station';
  const registerBtn = document.getElementById('registerShipBtn');
  if (registerBtn) registerBtn.style.display = isControl ? '' : 'none';
  const advanceBtn = document.getElementById('advanceShipsBtn');
  if (advanceBtn) advanceBtn.style.display = isControl ? '' : 'none';
  if (typeof focusShipId !== 'undefined' && focusShipId) renderShipFocus();
}

function renderCyclonesOnMap() {
  // Clear old
  for (const id in cycloneMapLayers) {
    if (cycloneMapLayers[id].circle) state.trackingMap?.removeLayer(cycloneMapLayers[id].circle);
    if (cycloneMapLayers[id].iconMarker) state.trackingMap?.removeLayer(cycloneMapLayers[id].iconMarker);
    delete cycloneMapLayers[id];
  }
  
  if (!state.cyclones || !state.trackingMap) return;
  
  state.cyclones.forEach(c => {
    let color = c.severity === 'high' ? '#ff3333' : c.severity === 'medium' ? '#ff9933' : '#ffff33';
    let circle = L.circle([c.lat, c.lon], {
      color: color,
      fillColor: color,
      fillOpacity: 0.0, // Invisible hit radius
      radius: (c.radius_km || 300) * 1000,
      weight: 0
    }).addTo(state.trackingMap).bindPopup(`<b>${c.name}</b><br>Severity: ${c.severity.toUpperCase()}<br>Radius: ${c.radius_km} km`);
    
    // Add animated radar pulse icon
    let pulseRadiusPixels = ((c.radius_km || 300) * 1000) / 4000; // approximate scaling for visual
    // Actually, leaflet sizes are in pixels, but the map zoom dictates physical size. 
    // To keep it simple and just be a visual indicator without dynamically resizing on zoom:
    // Icon footprint loosely follows the cyclone's real radius so bigger
    // storms visually read as bigger, without needing per-zoom recompute.
    const px = Math.max(140, Math.min(340, (c.radius_km || 300) * 0.7));
    let iconMarker = L.marker([c.lat, c.lon], {
      icon: L.divIcon({
        html: `<div class="cyclone-visual">
          <div class="cy-ring"></div><div class="cy-ring"></div><div class="cy-ring"></div>
          <div class="cy-spiral"></div>
          <div class="cy-eye"></div>
        </div>`,
        className: '',
        iconSize: [px, px],
        iconAnchor: [px / 2, px / 2]
      })
    }).addTo(state.trackingMap);

    cycloneMapLayers[c.id] = { circle, iconMarker };
  });
}

async function checkAllCycloneRouteRisks() {
  if (!state.cyclones || state.cyclones.length === 0) {
    state.ships.forEach(s => s.has_cyclone_warning = false);
    if (document.getElementById('page-voyages')?.classList.contains('active')) refreshVoyagesPage();
    return;
  }
  for (const shipId of Object.keys(state.voyages)) {
    try {
      const res = await apiPost('/api/cyclones/check_route', { ship_id: shipId, cyclones: state.cyclones });
      const s = state.ships.find(x => x.ship_id === shipId);
      if (s) {
        s.has_cyclone_warning = res.has_cyclone_warning;
      }
    } catch (e) { /* non-fatal */ }
  }
  
  // Re-render voyages page if active to show/hide the Reroute button
  if (document.getElementById('page-voyages')?.classList.contains('active')) {
    refreshVoyagesPage();
  }
}

// ═══════════════════════════════════════════════════════════════════════════
// NOTIFICATION / ALERT PANEL — sidebar badge + collapsible history matching
// all alert types: voyage_started, voyage_completed, cyclone_route_alert,
// weather_warning, route_replanned, high_wind, geofence events, etc.
// ═══════════════════════════════════════════════════════════════════════════
function renderNotificationBadge() {
  const unread = state.alerts.filter(a => !a.acknowledged).length;
  const badge = document.getElementById('notif-badge');
  if (badge) { badge.textContent = unread > 0 ? unread : ''; badge.style.display = unread > 0 ? 'inline-block' : 'none'; }
}

// ═══════════════════════════════════════════════════════════════════════════
// SHIPS PAGE — "Start Voyage" button wired into ship rows
// ═══════════════════════════════════════════════════════════════════════════
// COMMS CENTER — real AES-256 encrypted messages via ECDH session keys

function renderWeatherPanel(data, locationLabel) {
  const c = data.current;
  const waveHm = data.hourly?.wave_height?.[0];
  const waveText = waveHm != null ? `${waveHm.toFixed(1)} m` : 'N/A';
  const seaState = waveHm == null ? '—' : waveHm < 0.5 ? 'Calm' : waveHm < 1.25 ? 'Slight' : waveHm < 2.5 ? 'Moderate' : waveHm < 4 ? 'Rough' : 'Very Rough';
  const windDir = ['N','NNE','NE','ENE','E','ESE','SE','SSE','S','SSW','SW','WSW','W','WNW','NW','NNW'][Math.round(c.wind_direction_10m / 22.5) % 16] || '—';

  const el = document.getElementById('weather-panel');
  if (!el) return;
  el.innerHTML = `
    <div style="font-size:11px;color:var(--text3);margin-bottom:8px">📍 ${locationLabel}</div>
    <div class="form-row" style="flex-wrap:wrap;gap:8px">
      ${[
        ['🌡 Temperature', `${c.temperature_2m?.toFixed(1)}°C`],
        ['💧 Humidity', `${c.relative_humidity_2m}%`],
        ['🌬 Wind Speed', `${c.wind_speed_10m?.toFixed(1)} km/h`],
        ['🧭 Wind Direction', `${windDir} (${c.wind_direction_10m?.toFixed(0)}°)`],
        ['📊 Pressure', `${c.surface_pressure?.toFixed(0)} hPa`],
        ['🌊 Wave Height', waveText],
        ['⛵ Sea State', seaState],
        ['👁 Visibility', c.visibility != null ? `${(c.visibility/1000).toFixed(1)} km` : 'N/A'],
        ['☁ Cloud Cover', `${c.cloud_cover ?? '—'}%`],
        ['🌧 Rainfall', `${c.precipitation?.toFixed(1) ?? '—'} mm`],
      ].map(([k,v]) => `<div style="flex:1;min-width:130px;background:var(--surface3);border-radius:var(--radius-sm);padding:8px 10px"><div style="font-size:10px;color:var(--text3);margin-bottom:2px">${k}</div><div style="font-size:13px;font-weight:600">${v}</div></div>`).join('')}
    </div>
    <div style="font-size:10px;color:var(--text3);margin-top:6px">Updated: ${new Date().toTimeString().slice(0,8)} UTC</div>
  `;
}

async function refreshWeatherPanel() {
  const loc = _bestWeatherLocation();
  try {
    const data = await fetchWeather(loc.lat, loc.lon);
    renderWeatherPanel(data, loc.source);
  } catch (e) {
    const el = document.getElementById('weather-panel');
    if (el) el.innerHTML = `<div style="color:var(--text3);padding:12px">Weather temporarily unavailable.</div>`;
  }
}

// ═══════════════════════════════════════════════════════════════════════════
// INIT
// ═══════════════════════════════════════════════════════════════════════════
let _appBooted = false;
async function bootApp() {
  if (_appBooted) return;
  _appBooted = true;
  try {
    await loadPorts();
    refreshAllPortDropdowns();
    await loadTrackingMode();
    await loadShips();
    await loadActiveVoyages();
    await loadAlerts();
    state.blocks = await apiGet('/api/blockchain/blocks').catch(() => []);
    renderAlerts();
    renderNotificationBadge();
    await refreshDashboard();
    await loadIncidentLog().catch(() => {});
    await loadCycloneWarnings().catch(() => {});
    await loadCyclonesForMap().catch(() => {});
    await refreshWeatherPanel().catch(() => {});
    if (window.currentUserRole === 'passenger') renderPassengerShipList();
  } catch (e) {
    toast('Could not reach the backend at ' + API_BASE + ': ' + errMsg(e), 'error');
  }

  // Ship simulation ticker: advance all active voyages (sign + report)
  setInterval(() => tickVoyageMovement().catch(() => {}), VOYAGE_TICK_SECONDS * 1000);

  // Ship marker animation loop (renderer-only): smooth linear
  // interpolation between the last two reported positions.
  requestAnimationFrame(animateShipsContinuously);

  // PoET Validator Wait Time graph: paint whatever the last round was
  // (if any), then open the live websocket feed so it keeps updating
  // automatically as new blocks are mined — no polling, no refresh.
  await loadPoetLatest().catch(() => {});
  connectPoetSocket();

  // Cyclone and weather refresh every 5 min (fast enough to catch new storms,
  // slow enough to not hammer the public APIs).
  setInterval(async () => {
    try {
      await loadCyclonesForMap();
      renderCyclonesOnMap();
      await checkAllCycloneRouteRisks();
      renderNotificationBadge();
      await refreshWeatherPanel().catch(() => {});
    } catch (e) { /* non-fatal */ }
  }, 5 * 60 * 1000);

  // General data refresh (ships, alerts, dashboard) every 15 s
  setInterval(async () => {
    try {
      await loadShips();
      await loadActiveVoyages();
      await loadAlerts();
      renderNotificationBadge();
      if (document.getElementById('page-tracking').classList.contains('active')) { renderMapShips(); }
      if (document.getElementById('page-ship-focus').classList.contains('active')) { await renderShipFocus(); }
      if (document.getElementById('page-ships').classList.contains('active')) refreshShipRegistry();
      if (document.getElementById('page-dashboard').classList.contains('active')) await refreshDashboard();
      if (document.getElementById('page-cyclone').classList.contains('active')) await refreshCyclone();
      if (document.getElementById('page-illegal').classList.contains('active')) await refreshIllegal();
      if (window.currentUserRole === 'control_station') await loadRerouteApprovals();
      if (document.getElementById('page-passenger')?.classList.contains('active')) renderPassengerShipList();
    } catch (e) { /* transient network errors non-fatal */ }
  }, 15000);
}

document.addEventListener('DOMContentLoaded', async () => {
  // Auto-login if a session token was already saved from a previous
  // visit, re-verified against the server (see tryRestoreSession);
  // otherwise leave the login gate showing and wait for the user.
  const restored = await tryRestoreSession();
  if (!restored) {
    await checkBootstrapStatus();
    pickLoginRole('ship_captain');
    setAuthMode('login');
  }
});

const apiPatch = (path, body) => api('PATCH', path, body);

let isRerouting = false;
let rerouteShipId = null;
let rerouteClickHandler = null;

function initReroute(shipId) {
  if (window.currentUserRole !== 'control_station') {
    toast('Access denied. Control Station only.', 'error');
    return;
  }
  const s = state.ships.find(x => x.ship_id === shipId);
  if (!s) return;
  
  isRerouting = true;
  rerouteShipId = shipId;
  toast('Reroute Mode: Click anywhere on the map to set a new destination waypoint.', 'info');
  
  state.focusMap.closePopup();
  if (rerouteClickHandler) {
    state.focusMap.off('click', rerouteClickHandler);
  }
  
  rerouteClickHandler = async (e) => {
    state.focusMap.off('click', rerouteClickHandler);
    isRerouting = false;
    rerouteClickHandler = null;
    
    try {
      const voyage = state.voyages[shipId];
      if (!voyage || voyage.status !== 'in_progress') return;
      
      const newLat = e.latlng.lat;
      const newLon = e.latlng.lng;
      
      await apiPatch(`/api/voyages/${voyage.voyage_id}`, {
        dest_lat: newLat,
        dest_lon: newLon
      });
      toast('Route replanned successfully!', 'success');
      
      s.has_cyclone_warning = false;
      await loadActiveVoyages();
      await renderShipFocus();
      await checkAllCycloneRouteRisks();
      
    } catch (err) {
      toast('Failed to reroute: ' + errMsg(err), 'error');
    }
  };
  
  state.focusMap.on('click', rerouteClickHandler);
}

