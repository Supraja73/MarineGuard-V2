/**
 * sar_classifier.js
 * -----------------
 * Interactive PyTorch SAR (Synthetic Aperture Radar) Iceberg vs Vessel Classifier UI.
 * Renders dual-band SAR radar imagery (HH Band 1, HV Band 2) on HTML5 canvases,
 * connects to MarineGuard FastAPI backend (/api/detect/sar-classify), and
 * triggers real-time PoET Blockchain threat alerts on iceberg detection.
 */

(function () {
  'use strict';

  let currentSample = null;

  // Color map for SAR radar intensity backscatter (dB values)
  function dBToColor(val, minVal, maxVal) {
    const norm = Math.max(0, Math.min(1, (val - minVal) / (maxVal - minVal + 1e-6)));
    
    // Smooth blue -> cyan -> yellow -> white intensity map
    let r, g, b;
    if (norm < 0.25) {
      // Dark navy to deep blue
      const t = norm / 0.25;
      r = Math.floor(10 * t);
      g = Math.floor(20 * t);
      b = Math.floor(40 + 80 * t);
    } else if (norm < 0.6) {
      // Deep blue to bright cyan
      const t = (norm - 0.25) / 0.35;
      r = Math.floor(10 + 30 * t);
      g = Math.floor(100 + 120 * t);
      b = Math.floor(120 + 135 * t);
    } else if (norm < 0.85) {
      // Cyan to vibrant yellow
      const t = (norm - 0.6) / 0.25;
      r = Math.floor(40 + 215 * t);
      g = Math.floor(220 + 35 * t);
      b = Math.floor(255 * (1 - t));
    } else {
      // Yellow to intense white highlight
      const t = (norm - 0.85) / 0.15;
      r = 255;
      g = 255;
      b = Math.floor(255 * t);
    }
    return [r, g, b];
  }

  function renderSarCanvas(canvasId, bandData) {
    const canvas = document.getElementById(canvasId);
    if (!canvas || !bandData || bandData.length !== 5625) return;

    const ctx = canvas.getContext('2d');
    const imgData = ctx.createImageData(75, 75);

    let minVal = Infinity;
    let maxVal = -Infinity;
    for (let i = 0; i < bandData.length; i++) {
      if (bandData[i] < minVal) minVal = bandData[i];
      if (bandData[i] > maxVal) maxVal = bandData[i];
    }

    for (let i = 0; i < 5625; i++) {
      const rgb = dBToColor(bandData[i], minVal, maxVal);
      const pixelIdx = i * 4;
      imgData.data[pixelIdx] = rgb[0];     // Red
      imgData.data[pixelIdx + 1] = rgb[1]; // Green
      imgData.data[pixelIdx + 2] = rgb[2]; // Blue
      imgData.data[pixelIdx + 3] = 255;    // Alpha
    }

    ctx.putImageData(imgData, 0, 0);
  }

  window.initSarClassifierUI = function () {
    const sampleSelect = document.getElementById('sar-sample-select');
    if (!sampleSelect) return;

    if (typeof window.SAR_SAMPLES === 'undefined' || !Array.isArray(window.SAR_SAMPLES) || window.SAR_SAMPLES.length === 0) {
      sampleSelect.innerHTML = '<option value="">No SAR dataset samples loaded</option>';
      return;
    }

    sampleSelect.innerHTML = '';
    window.SAR_SAMPLES.forEach((sample, idx) => {
      const opt = document.createElement('option');
      opt.value = sample.id;
      const trueType = sample.is_iceberg === 1 ? '🧊 Iceberg Target' : '🚢 Vessel Target';
      opt.textContent = `Sample #${idx + 1} (ID: ${sample.id}) — ${trueType}`;
      sampleSelect.appendChild(opt);
    });

    sampleSelect.selectedIndex = 0;
    window.onSarSampleChanged();

    // Populate active ship dropdown for optional coordinate linking
    window.updateSarShipDropdown();
  };

  window.updateSarShipDropdown = function () {
    const shipSelect = document.getElementById('sar-ship-select');
    if (!shipSelect) return;

    const ships = window.currentShipsList || [];
    shipSelect.innerHTML = '<option value="">-- Sector Hazard Only (North Atlantic 47.5°N, 52.5°W) --</option>';
    ships.forEach(s => {
      const opt = document.createElement('option');
      opt.value = s.ship_id;
      opt.textContent = `🚢 ${s.name} (${s.ship_id}) [${s.current_lat ? s.current_lat.toFixed(2) : '--'}°, ${s.current_lon ? s.current_lon.toFixed(2) : '--'}°]`;
      shipSelect.appendChild(opt);
    });
  };

  window.onSarSampleChanged = function () {
    const sampleSelect = document.getElementById('sar-sample-select');
    const angleInput = document.getElementById('sar-inc-angle');
    const resultBox = document.getElementById('sar-result');
    if (!sampleSelect) return;

    const selectedId = sampleSelect.value;
    if (!window.SAR_SAMPLES) return;

    currentSample = window.SAR_SAMPLES.find(s => s.id === selectedId) || window.SAR_SAMPLES[0];
    if (!currentSample) return;

    if (angleInput) {
      angleInput.value = currentSample.inc_angle && currentSample.inc_angle !== 'na' 
        ? `${parseFloat(currentSample.inc_angle).toFixed(2)}°` 
        : '38.45° (Estimated)';
    }

    renderSarCanvas('sar-canvas-b1', currentSample.band_1);
    renderSarCanvas('sar-canvas-b2', currentSample.band_2);

    if (resultBox) {
      resultBox.innerHTML = `
        <div style="font-size:12px;color:var(--text2);background:var(--bg3);padding:10px;border-radius:6px;border:1px dashed var(--border)">
          Target SAR radar imagery ready for inference. Click <b>⚡ Run PyTorch AI Classification</b> to evaluate neural network prediction.
        </div>
      `;
    }
  };

  window.runSarClassification = async function () {
    const resultBox = document.getElementById('sar-result');
    const shipSelect = document.getElementById('sar-ship-select');
    if (!currentSample) return;

    if (resultBox) {
      resultBox.innerHTML = `
        <div style="display:flex;align-items:center;gap:8px;font-size:12px;color:var(--accent)">
          <span class="spinner" style="width:14px;height:14px;border:2px solid var(--accent);border-top-color:transparent;border-radius:50%;animation:spin 0.8s linear infinite"></span>
          Running PyTorch CustomCNN tensor inference...
        </div>
      `;
    }

    const shipId = shipSelect ? shipSelect.value : null;

    try {
      const response = await fetch('/api/detect/sar-classify', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          sample_id: currentSample.id,
          band_1: currentSample.band_1,
          band_2: currentSample.band_2,
          ship_id: shipId || null
        })
      });

      const res = await response.json();
      if (!response.ok) {
        throw new Error(res.detail || 'Classification endpoint returned an error.');
      }

      const isIceberg = res.prediction === 1;
      const trueLabel = currentSample.is_iceberg === 1 ? 'Iceberg' : 'Vessel';
      const isMatch = (res.class_label === trueLabel);

      let alertHtml = '';
      if (res.alert) {
        alertHtml = `
          <div style="margin-top:10px;padding:8px 12px;background:rgba(239, 68, 68, 0.15);border:1px solid var(--red);border-radius:6px;font-size:11px;color:#fca5a5">
            <strong>🚨 Threat Alert Raised & Written to PoET Blockchain Ledger!</strong><br/>
            Block Hash: <code style="font-family:monospace;color:var(--text);font-size:10px">${res.alert.blockchain_block_hash || 'Verified'}</code><br/>
            Alert Detail: ${res.alert.detail}
          </div>
        `;
      }

      if (resultBox) {
        resultBox.innerHTML = `
          <div style="background:var(--bg3);border:1px solid ${isIceberg ? 'var(--red)' : 'var(--emerald)'};border-radius:8px;padding:12px">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
              <span style="font-size:14px;font-weight:700;color:${isIceberg ? 'var(--red)' : 'var(--emerald)'}">
                ${isIceberg ? '🧊 ICEBERG HAZARD DETECTED' : '🚢 VESSEL / SHIP IDENTIFIED'}
              </span>
              <span class="badge" style="background:${isIceberg ? 'rgba(239,68,68,0.2)' : 'rgba(16,185,129,0.2)'};color:${isIceberg ? 'var(--red)' : 'var(--emerald)'}">
                ${res.confidence}% Confidence
              </span>
            </div>

            <div style="font-size:12px;margin-bottom:8px">
              <div><b>PyTorch Probability (Iceberg):</b> ${(res.probability * 100).toFixed(2)}%</div>
              <div><b>Dataset Ground Truth:</b> ${trueLabel} ${isMatch ? '<span style="color:var(--emerald)">✓ Correct</span>' : '<span style="color:var(--amber)">⚠️ Misclassified</span>'}</div>
              <div style="font-size:10px;color:var(--text2);margin-top:2px">Model: ${res.model_used}</div>
            </div>

            <!-- Probability Bar Gauge -->
            <div style="background:rgba(255,255,255,0.08);height:8px;border-radius:4px;overflow:hidden;margin-bottom:6px">
              <div style="width:${(res.probability * 100).toFixed(1)}%;height:100%;background:${isIceberg ? 'linear-gradient(90deg, #f59e0b, #ef4444)' : 'linear-gradient(90deg, #3b82f6, #10b981)'};transition:width 0.4s ease"></div>
            </div>

            ${alertHtml}
          </div>
        `;
      }

      // Refresh alerts & blockchain tables in MarineGuard UI if alert raised
      if (res.alert && typeof window.fetchAlerts === 'function') {
        window.fetchAlerts();
      }
      if (res.alert && typeof window.fetchBlockchainBlocks === 'function') {
        window.fetchBlockchainBlocks();
      }

      if (typeof window.toast === 'function') {
        if (isIceberg) {
          window.toast(`SAR AI Classifier: Iceberg Hazard detected (${res.confidence}% conf)`, 'error');
        } else {
          window.toast(`SAR AI Classifier: Vessel target identified (${res.confidence}% conf)`, 'success');
        }
      }

    } catch (err) {
      if (resultBox) {
        resultBox.innerHTML = `
          <div style="padding:10px;background:rgba(239,68,68,0.1);border:1px solid var(--red);border-radius:6px;color:var(--red);font-size:12px">
            ❌ Classification failed: ${err.message}
          </div>
        `;
      }
    }
  };

  // Initialize UI once DOM is ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', window.initSarClassifierUI);
  } else {
    setTimeout(window.initSarClassifierUI, 100);
  }
})();
