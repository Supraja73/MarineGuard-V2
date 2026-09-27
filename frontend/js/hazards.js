// ═══════════════════════════════════════════════════════════════════════════
// HAZARD PAINTER  +  PoET TIME-SERIES CHART
// Loaded AFTER main.js so it can safely wrap renderPoetChart / renderMapShips.
// ═══════════════════════════════════════════════════════════════════════════
(function () {
  'use strict';

  // ── HAZARD MODEL ────────────────────────────────────────────────────────
  //   kind:   'cyclone' | 'storm' | 'wind'
  //   default radius per kind (km)
  const HAZARD_META = {
    cyclone: { label: 'Cyclone',    icon: '🌀', radiusKm: 280, color: '#ef4444' },
    storm:   { label: 'Storm',      icon: '⛈',  radiusKm: 180, color: '#f59e0b' },
    wind:    { label: 'Heavy Wind', icon: '🌬', radiusKm: 120, color: '#0ea5e9' },
  };

  // window.userHazards = [ {id, kind, lat, lon, radiusKm, circleLayer, markerLayer} ]
  // Only ever holds Storm / Wind markers now — these are visual-only, the
  // backend has no concept of them. Cyclones are NOT stored here: placing
  // one creates a real app.db.models.Cyclone row (see placeCyclone below),
  // and window.state.cyclones / renderCyclonesOnMap (main.js) is what
  // actually draws it, because that's the single source of truth the real
  // reroute engine also reads from.
  window.userHazards = [];

  let activeTool = null; // 'cyclone' | 'storm' | 'wind' | 'ship' | null
  let mapClickHandler = null;

  // ── Drop a REAL cyclone at (lat, lon): a genuine backend Cyclone row,
  // not a client-side decoration. This is what makes "place cyclone near
  // the ship" actually generate a validated, water-only alternate route
  // from the ship's current position — the exact same engine
  // (cyclone_reroute_service + reroute_orchestrator) the automatic
  // background monitor uses, so there is only ever one routing algorithm
  // in the whole app, not a second fake one living in the browser. ──
  async function placeCyclone(lat, lon) {
    if (window.currentUserRole !== 'control_station') {
      if (typeof toast === 'function') toast('Only Control Station can place cyclones.', 'warning');
      return;
    }
    const meta = HAZARD_META.cyclone;
    try {
      await apiPost('/api/cyclones/test_trigger', {
        lat, lon,
        radius_km: meta.radiusKm,
        severity: 'high',
        name: 'Cyclone ' + Math.floor(100 + Math.random() * 900),
      });
    } catch (e) {
      if (typeof toast === 'function') toast('Failed to place cyclone: ' + (typeof errMsg === 'function' ? errMsg(e) : e.message), 'error');
      return;
    }

    if (typeof toast === 'function') toast('🌀 Cyclone placed — computing a safe, water-only alternate route…', 'warning');

    if (typeof loadCyclonesForMap === 'function') await loadCyclonesForMap();
    if (typeof renderCyclonesOnMap === 'function') renderCyclonesOnMap();
    if (typeof checkAllCycloneRouteRisks === 'function') await checkAllCycloneRouteRisks();

    // Force an immediate check instead of waiting for the background
    // monitor's next tick (a few seconds) — every in-progress voyage this
    // cyclone actually threatens gets its alternate route (or, if none
    // exists, a safe-port diversion) applied right away, always starting
    // from the ship's current position.
    const voyageIds = Object.values(state.voyages || {})
      .filter(v => v.status === 'in_progress')
      .map(v => v.voyage_id);
    await Promise.all(voyageIds.map(id => apiPost('/api/reroutes/propose', { voyage_id: id }).catch(() => {})));

    if (typeof renderMapShips === 'function') renderMapShips();
    if (typeof refreshVoyagesPage === 'function' && document.getElementById('page-voyages')?.classList.contains('active')) refreshVoyagesPage();
  }

  // ── Drop a purely visual hazard (Storm / Wind) at (lat, lon). These
  // don't threaten any real route — only a Cyclone does. ──
  function placeVisualHazard(lat, lon) {
    const meta = HAZARD_META[activeTool];
    const map = state.trackingMap;
    const id = 'hz-' + Date.now() + '-' + Math.floor(Math.random() * 1000);

    const circle = L.circle([lat, lon], {
      radius: meta.radiusKm * 1000,
      color: meta.color,
      weight: 2,
      fillColor: meta.color,
      fillOpacity: 0.18,
      dashArray: '4 6',
    }).addTo(map);

    const marker = L.marker([lat, lon], {
      icon: L.divIcon({
        html: `<div class="hz-marker-dot hz-${activeTool}">${meta.icon}</div>`,
        className: '',
        iconSize: [34, 34],
        iconAnchor: [17, 17],
      }),
    }).addTo(map).bindTooltip(`${meta.label} — ${meta.radiusKm} km influence`, { direction: 'top' });

    window.userHazards.push({ id, kind: activeTool, lat, lon, radiusKm: meta.radiusKm, circleLayer: circle, markerLayer: marker });
    if (typeof toast === 'function') toast(`${meta.icon} ${meta.label} placed`, 'warning');
  }

  // ── Drop a hazard at (lat, lon) with the currently armed tool ──
  function placeHazard(lat, lon) {
    if (!activeTool) return;
    if (state.trackingMode === 'real') {
      if (typeof toast === 'function') toast('Hazard Painter is a Demo Mode tool — switch to Demo to place simulated hazards.', 'warning');
      setActiveTool(null);
      return;
    }
    // Auto-disarm so a stray click doesn't drop another one.
    const tool = activeTool;
    setActiveTool(null);
    if (tool === 'cyclone') {
      placeCyclone(lat, lon);
    } else {
      placeVisualHazard(lat, lon);
    }
  }

  // ── Tool state ──────────────────────────────────────────────────────────
  function setActiveTool(tool) {
    activeTool = tool;
    document.querySelectorAll('.hazard-btn[data-tool]').forEach(btn => {
      btn.classList.toggle('active', btn.dataset.tool === tool);
    });
    const wrap = document.querySelector('.tracking-map-wrap');
    if (wrap) wrap.classList.toggle('hazard-arming', !!tool);
  }
  window.setHazardTool = function (tool) { setActiveTool(activeTool === tool ? null : tool); };

  window.clearHazards = async function () {
    const map = state.trackingMap;
    window.userHazards.forEach(h => {
      try { map.removeLayer(h.circleLayer); } catch (e) {}
      try { map.removeLayer(h.markerLayer); } catch (e) {}
    });
    window.userHazards = [];
    setActiveTool(null);

    // Clear the real backend cyclones too, if any were placed.
    if (window.currentUserRole === 'control_station') {
      try {
        await apiPost('/api/cyclones/test_clear', {});
        if (typeof loadCyclonesForMap === 'function') await loadCyclonesForMap();
        if (typeof renderCyclonesOnMap === 'function') renderCyclonesOnMap();
        if (typeof checkAllCycloneRouteRisks === 'function') await checkAllCycloneRouteRisks();
      } catch (e) { /* non-fatal */ }
    }
    if (typeof renderMapShips === 'function') renderMapShips();
    if (typeof toast === 'function') toast('Cleared all placed hazards', 'success');
  };

  // ── Hook the map: install click handler once the tracking map exists ──
  function tryInstallMapClick() {
    if (!state.trackingMap) return false;
    if (mapClickHandler) return true;
    mapClickHandler = (e) => {
      if (!activeTool) return;
      if (activeTool === 'ship') {
        if (typeof window.openQuickShipModal === 'function') window.openQuickShipModal(e.latlng.lat, e.latlng.lng);
        setActiveTool(null);
        return;
      }
      placeHazard(e.latlng.lat, e.latlng.lng);
    };
    state.trackingMap.on('click', mapClickHandler);
    return true;
  }

  // Recompute reroutes whenever ship positions change (piggy-back on
  // renderMapShips, which fires on every backend tick).
  const _origRender = window.renderMapShips;
  if (typeof _origRender === 'function') {
    window.renderMapShips = function () {
      _origRender.apply(this, arguments);
      tryInstallMapClick();
    };
  }

  // Poll until the tracking map is ready (user might open the page later).
  const iv = setInterval(() => { if (tryInstallMapClick()) clearInterval(iv); }, 500);

  // ═════════════════════════════════════════════════════════════════════════
  // PoET TIME-SERIES CHART — wait-time per validator per block, over time
  // ═════════════════════════════════════════════════════════════════════════
  const VALIDATOR_COLORS = {
    'validator-alpha':   '#10B981',
    'validator-bravo':   '#3B82F6',
    'validator-charlie': '#F59E0B',
    'validator-delta':   '#A855F7',
  };
  const MAX_POINTS = 30;
  let poetTsChart = null;
  const poetTsSeries = {}; // validatorId -> [{x: round, y: ms}]
  let poetTsRounds = [];

  function ensurePoetTsChart() {
    const canvas = document.getElementById('poetTimeSeriesChart');
    if (!canvas || typeof Chart === 'undefined') return null;
    if (poetTsChart) return poetTsChart;
    poetTsChart = new Chart(canvas, {
      type: 'line',
      data: { labels: [], datasets: [] },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: { duration: 350 },
        interaction: { mode: 'index', intersect: false },
        plugins: {
          legend: { labels: { color: '#E2E8F0', boxWidth: 12 } },
          title: {
            display: true,
            text: 'PoET Wait Time per Validator — Every New Block',
            color: '#E2E8F0',
            font: { size: 13, weight: '600' },
          },
          tooltip: { callbacks: { label: (ctx) => `${ctx.dataset.label}: ${ctx.parsed.y.toFixed(2)} ms` } },
        },
        scales: {
          x: {
            title: { display: true, text: 'Block / PoET round', color: '#94A3B8' },
            ticks: { color: '#94A3B8' },
            grid: { color: '#1e293b' },
          },
          y: {
            beginAtZero: true,
            title: { display: true, text: 'Wait time (ms)', color: '#94A3B8' },
            ticks: { color: '#94A3B8' },
            grid: { color: '#334155' },
          },
        },
      },
    });
    return poetTsChart;
  }

  function pushPoetRound(data) {
    if (!data || !Array.isArray(data.validators)) return;
    const chart = ensurePoetTsChart();
    if (!chart) return;

    poetTsRounds.push(data.round);
    data.validators.forEach(v => {
      if (!poetTsSeries[v.id]) poetTsSeries[v.id] = [];
      poetTsSeries[v.id].push(v.wait_ms);
    });

    // Trim
    if (poetTsRounds.length > MAX_POINTS) {
      poetTsRounds = poetTsRounds.slice(-MAX_POINTS);
      Object.keys(poetTsSeries).forEach(k => {
        poetTsSeries[k] = poetTsSeries[k].slice(-MAX_POINTS);
      });
    }

    chart.data.labels = poetTsRounds.map(r => '#' + r);
    chart.data.datasets = Object.keys(poetTsSeries).map(id => ({
      label: id,
      data: poetTsSeries[id],
      borderColor: VALIDATOR_COLORS[id] || '#64748B',
      backgroundColor: (VALIDATOR_COLORS[id] || '#64748B') + '33',
      borderWidth: 2,
      tension: 0.3,
      pointRadius: 3,
      pointHoverRadius: 5,
      pointBackgroundColor: data.winner === id ? '#fff' : (VALIDATOR_COLORS[id] || '#64748B'),
      pointBorderColor: VALIDATOR_COLORS[id] || '#64748B',
      pointBorderWidth: data.winner === id ? 2 : 1,
    }));
    chart.update();

    const meta = document.getElementById('poet-ts-meta');
    if (meta) {
      meta.textContent = `Latest: round #${data.round} — winner ${data.winner} at ${
        (data.validators.find(v => v.id === data.winner)?.wait_ms ?? 0).toFixed(2)
      } ms`;
    }
  }

  // Wrap the existing renderPoetChart so we don't lose its bar-chart behavior.
  const _origRenderPoet = window.renderPoetChart;
  if (typeof _origRenderPoet === 'function') {
    window.renderPoetChart = function (data) {
      _origRenderPoet.apply(this, arguments);
      try { pushPoetRound(data); } catch (e) { console.warn('poet ts push failed', e); }
    };
  }

  // Seed the time-series with whatever PoET history the backend exposes
  // so the graph isn't empty on first load.
  async function seedPoetHistory() {
    try {
      const hist = await apiGet('/api/blockchain/poet/history?limit=' + MAX_POINTS);
      if (Array.isArray(hist)) hist.forEach(pushPoetRound);
    } catch (e) {
      // Fallback: single latest snapshot
      try {
        const latest = await apiGet('/api/blockchain/poet/latest');
        if (latest) pushPoetRound(latest);
      } catch (_) {}
    }
  }
  // Fire after main.js has settled + auth ready
  setTimeout(seedPoetHistory, 1500);
})();
