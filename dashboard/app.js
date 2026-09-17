/**
 * Pre-VIEW Audit Trend Monitor — Dashboard Application
 * ======================================================
 * Polls the Python HTTP bridge for audit data, draws the confidence
 * trend chart, updates cassette health cards, and sends fault
 * injection commands.
 *
 * Architecture mapping:
 *   DC-648 app.js (polling + ring buffer SVG + throughput chart)  →  This module
 *
 * Key components:
 *   1. Cassette health cards (status, confidence, rolling avg)
 *   2. Multi-line confidence trend chart (canvas, 5 cassettes)
 *   3. Scrolling audit log feed
 *   4. Fault injection command sender
 */

// ════ CONFIGURATION ════
const POLL_INTERVAL_MS = 500;    // Poll every 500ms
const MAX_LOG_ENTRIES  = 50;     // Max audit log entries to display

// Cassette colors (must match CSS --color-cN variables)
const CASSETTE_COLORS = {
    'C1': '#448aff',
    'C2': '#66bb6a',
    'C3': '#ef5350',
    'C4': '#ffa726',
    'C5': '#ab47bc',
};

const CASSETTE_TABLETS = {
    'C1': 'Amlodipine 5mg',
    'C2': 'Losartan Potassium 50mg',
    'C3': 'Metformin 500mg',
    'C4': 'Atorvastatin 10mg',
    'C5': 'Loxoprofen 60mg',
};

// Chart Y-axis range
const Y_MIN = 0.80;
const Y_MAX = 1.00;
const FAIL_THRESHOLD = 0.85;

// ════ STATE ════
let chartCanvas, chartCtx;
let trendData = null;
let lastSeenId = -1;
let isConnected = false;
let pollCount = 0;
let alertDismissed = false;


// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
//  INITIALIZATION
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

document.addEventListener('DOMContentLoaded', () => {
    initChart();
    startPolling();
});


// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
//  TREND CHART — Multi-line canvas chart (the ⭐ hero visual)
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

function initChart() {
    chartCanvas = document.getElementById('trend-chart');
    if (!chartCanvas) return;

    // High-DPI rendering
    const rect = chartCanvas.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    chartCanvas.width = rect.width * dpr;
    chartCanvas.height = rect.height * dpr;
    chartCtx = chartCanvas.getContext('2d');
    chartCtx.scale(dpr, dpr);

    // Draw initial empty state
    drawChart();
}

function drawChart() {
    if (!chartCtx) return;

    const rect = chartCanvas.getBoundingClientRect();
    const w = rect.width;
    const h = rect.height;
    const ctx = chartCtx;

    const pad = { top: 20, right: 24, bottom: 28, left: 52 };
    const plotW = w - pad.left - pad.right;
    const plotH = h - pad.top - pad.bottom;

    ctx.clearRect(0, 0, w, h);

    // ── Grid lines & Y-axis labels ────────────────────
    ctx.font = '10px JetBrains Mono, monospace';
    ctx.textAlign = 'right';

    for (let y = Y_MIN; y <= Y_MAX + 0.001; y += 0.02) {
        const py = pad.top + plotH * (1 - (y - Y_MIN) / (Y_MAX - Y_MIN));

        ctx.strokeStyle = 'rgba(255, 255, 255, 0.04)';
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(pad.left, py);
        ctx.lineTo(w - pad.right, py);
        ctx.stroke();

        ctx.fillStyle = '#5f6368';
        ctx.fillText(y.toFixed(2), pad.left - 8, py + 3);
    }

    // ── Fail threshold line (dashed red at 0.85) ──────
    const threshY = pad.top + plotH * (1 - (FAIL_THRESHOLD - Y_MIN) / (Y_MAX - Y_MIN));
    ctx.strokeStyle = 'rgba(239, 83, 80, 0.45)';
    ctx.lineWidth = 1.5;
    ctx.setLineDash([6, 4]);
    ctx.beginPath();
    ctx.moveTo(pad.left, threshY);
    ctx.lineTo(w - pad.right, threshY);
    ctx.stroke();
    ctx.setLineDash([]);

    // Threshold label
    ctx.fillStyle = 'rgba(239, 83, 80, 0.7)';
    ctx.font = '9px JetBrains Mono, monospace';
    ctx.textAlign = 'right';
    ctx.fillText('FAIL THRESHOLD 0.85', w - pad.right, threshY - 6);

    // ── Draw cassette lines ───────────────────────────
    if (!trendData) {
        // No data yet — show message
        ctx.fillStyle = '#5f6368';
        ctx.font = '12px JetBrains Mono, monospace';
        ctx.textAlign = 'center';
        ctx.fillText('Waiting for audit data...', w / 2, h / 2);
        return;
    }

    // Find max data length across cassettes for X-axis scaling
    let maxPoints = 0;
    for (const cid in trendData) {
        maxPoints = Math.max(maxPoints, trendData[cid].length);
    }
    if (maxPoints < 2) return;

    // Draw each cassette's rolling average line
    const cassetteOrder = ['C1', 'C2', 'C4', 'C5', 'C3']; // C3 drawn last (on top)

    for (const cid of cassetteOrder) {
        const points = trendData[cid];
        if (!points || points.length < 2) continue;

        const color = CASSETTE_COLORS[cid] || '#fff';

        // Draw the line
        ctx.beginPath();
        ctx.strokeStyle = color;
        ctx.lineWidth = cid === 'C3' ? 2.5 : 1.8;  // C3 slightly thicker
        ctx.lineJoin = 'round';
        ctx.lineCap = 'round';

        for (let i = 0; i < points.length; i++) {
            const x = pad.left + (i / (maxPoints - 1)) * plotW;
            const avg = points[i].rolling_avg;
            // Clamp to chart range
            const clampedAvg = Math.max(Y_MIN, Math.min(Y_MAX, avg));
            const y = pad.top + plotH * (1 - (clampedAvg - Y_MIN) / (Y_MAX - Y_MIN));

            if (i === 0) ctx.moveTo(x, y);
            else ctx.lineTo(x, y);
        }
        ctx.stroke();

        // Draw gradient fill under the line (subtle)
        if (cid === 'C3' && points.length > 2) {
            const lastX = pad.left + ((points.length - 1) / (maxPoints - 1)) * plotW;
            const lastAvg = Math.max(Y_MIN, Math.min(Y_MAX, points[points.length - 1].rolling_avg));
            const lastY = pad.top + plotH * (1 - (lastAvg - Y_MIN) / (Y_MAX - Y_MIN));

            ctx.lineTo(lastX, pad.top + plotH);
            ctx.lineTo(pad.left, pad.top + plotH);
            ctx.closePath();

            const grad = ctx.createLinearGradient(0, pad.top, 0, pad.top + plotH);
            grad.addColorStop(0, 'rgba(239, 83, 80, 0.08)');
            grad.addColorStop(1, 'rgba(239, 83, 80, 0)');
            ctx.fillStyle = grad;
            ctx.fill();
        }

        // Draw endpoint dot
        const lastPt = points[points.length - 1];
        const endX = pad.left + ((points.length - 1) / (maxPoints - 1)) * plotW;
        const endAvg = Math.max(Y_MIN, Math.min(Y_MAX, lastPt.rolling_avg));
        const endY = pad.top + plotH * (1 - (endAvg - Y_MIN) / (Y_MAX - Y_MIN));

        ctx.beginPath();
        ctx.fillStyle = color;
        ctx.arc(endX, endY, 3.5, 0, Math.PI * 2);
        ctx.fill();

        // Label at endpoint
        ctx.fillStyle = color;
        ctx.font = '9px JetBrains Mono, monospace';
        ctx.textAlign = 'left';
        ctx.fillText(cid, endX + 6, endY + 3);
    }

    // ── X-axis label ──────────────────────────────────
    ctx.fillStyle = '#5f6368';
    ctx.font = '9px JetBrains Mono, monospace';
    ctx.textAlign = 'center';
    ctx.fillText('Batch Sequence →', w / 2, h - 4);
}


// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
//  CASSETTE HEALTH CARDS
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

function updateCassetteCards(statusData) {
    if (!statusData || !statusData.cassettes) return;

    for (const cid of ['C1', 'C2', 'C3', 'C4', 'C5']) {
        const data = statusData.cassettes[cid];
        if (!data) continue;

        const card = document.getElementById(`card-${cid}`);
        const healthBadge = document.getElementById(`health-${cid}`);
        const confEl = document.getElementById(`conf-${cid}`);
        const avgEl = document.getElementById(`avg-${cid}`);
        const trendEl = document.getElementById(`trend-${cid}`);

        if (!card) continue;

        // Update card state class
        card.className = `cassette-card state-${data.health.toLowerCase()}`;

        // Update health badge
        if (healthBadge) {
            healthBadge.textContent = data.health;
            healthBadge.className = `cassette-health health-${data.health.toLowerCase()}`;
        }

        // Update confidence value
        if (confEl) {
            confEl.textContent = data.current_confidence.toFixed(3);
            confEl.className = 'cassette-metric-value mono';
            if (data.current_confidence < FAIL_THRESHOLD) {
                confEl.classList.add('critical');
            } else if (data.current_confidence < 0.90) {
                confEl.classList.add('low');
            }
        }

        // Update rolling average
        if (avgEl) {
            // Preserve the trend arrow span
            const arrowSpan = trendEl ? trendEl.outerHTML : '';
            avgEl.innerHTML = data.rolling_avg.toFixed(3) + arrowSpan;

            avgEl.className = 'cassette-metric-value mono';
            if (data.rolling_avg < FAIL_THRESHOLD) {
                avgEl.classList.add('critical');
            } else if (data.rolling_avg < 0.90) {
                avgEl.classList.add('low');
            }
        }

        // Update trend arrow
        const trendArrow = document.getElementById(`trend-${cid}`);
        if (trendArrow) {
            if (data.trend === 'declining') {
                trendArrow.textContent = ' ▼';
                trendArrow.className = 'cassette-trend-arrow trend-declining';
            } else if (data.trend === 'improving') {
                trendArrow.textContent = ' ▲';
                trendArrow.className = 'cassette-trend-arrow trend-improving';
            } else {
                trendArrow.textContent = ' ●';
                trendArrow.className = 'cassette-trend-arrow trend-stable';
            }
        }
    }
}


// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
//  ALERT BANNER
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

function updateAlertBanner(statusData) {
    if (alertDismissed) return;

    const banner = document.getElementById('alert-banner');
    const title = document.getElementById('alert-title');
    const desc = document.getElementById('alert-desc');
    if (!banner) return;

    const alerts = statusData.alerts || [];
    if (alerts.length === 0) {
        banner.style.display = 'none';
        return;
    }

    // Show the most severe alert
    const alert = alerts[0];
    title.textContent = alert.health === 'CRITICAL'
        ? '⚠ CRITICAL — CONFIDENCE BELOW THRESHOLD'
        : '⚠ DRIFT DETECTED — EARLY WARNING';
    desc.textContent = `Cassette ${alert.cassette_id}: ${alert.message}`;
    banner.style.display = 'flex';
}

function dismissAlert() {
    alertDismissed = true;
    const banner = document.getElementById('alert-banner');
    if (banner) banner.style.display = 'none';
    // Reset after 30 seconds so new alerts can show
    setTimeout(() => { alertDismissed = false; }, 30000);
}


// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
//  AUDIT LOG FEED
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

function updateAuditLog(feedData) {
    if (!feedData || !feedData.records) return;

    const records = feedData.records;
    if (records.length === 0) return;

    const container = document.getElementById('log-container');
    if (!container) return;

    const latestId = records[records.length - 1].id;
    if (latestId <= lastSeenId) return; // No new records

    // On first load or if the database was reset, clear and rebuild
    if (lastSeenId === -1 || records[0].id < lastSeenId - 100) {
        container.innerHTML = '';
        lastSeenId = -1;
    }

    // Append only new records
    const newRecords = records.filter(r => r.id > lastSeenId);
    for (const record of newRecords) {
        const entry = document.createElement('div');

        const isFail = record.mismatch;
        const isLow = record.confidence < 0.90;

        if (isFail) {
            entry.className = 'log-entry log-error';
        } else if (isLow) {
            entry.className = 'log-entry log-warn';
        } else {
            entry.className = 'log-entry log-ok';
        }

        const time = record.timestamp.split(' ')[1] || record.timestamp;
        const status = isFail ? '✗ MISMATCH' : '✓ PASS';
        const confStr = record.confidence.toFixed(3);

        entry.innerHTML = `<span class="log-time">${time}</span>` +
            `<span class="log-msg">Batch #${String(record.batch_id).padStart(4, '0')} | ` +
            `${record.cassette_id} | ${record.tablet_sku} | ` +
            `Conf: ${confStr} | ${status}</span>`;

        container.appendChild(entry);
    }

    // Trim old entries
    while (container.children.length > MAX_LOG_ENTRIES) {
        container.removeChild(container.firstChild);
    }

    // Auto-scroll to bottom
    container.scrollTop = container.scrollHeight;

    lastSeenId = latestId;
}

function clearLog() {
    const container = document.getElementById('log-container');
    if (container) {
        container.innerHTML = '';
        lastSeenId = -1;
    }
}


// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
//  CONNECTION STATUS
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

function updateConnectionStatus(status) {
    const dot = document.getElementById('conn-dot');
    const text = document.getElementById('conn-text');
    if (!dot || !text) return;

    dot.className = 'conn-dot ' + status;
    text.textContent = status === 'connected' ? 'CONNECTED' :
                       status === 'disconnected' ? 'DISCONNECTED' : 'CONNECTING...';
}

function updateArchArrows(connected) {
    for (let i = 1; i <= 4; i++) {
        const arrow = document.getElementById(`arrow-${i}`);
        if (arrow) {
            arrow.className = connected ? 'arch-arrow active' : 'arch-arrow';
        }
    }
}


// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
//  COMMAND SENDING — Fault injection via POST to Python server
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async function sendCommand(command) {
    try {
        const response = await fetch('/api/command', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ command: command }),
        });

        if (response.ok) {
            // Flash the button
            const btnMap = {
                'lens_degradation': 'btn-lens',
                'cassette_wear':    'btn-wear',
                'pill_mismatch':    'btn-mismatch',
                'reset':            'btn-reset',
            };
            const btnId = btnMap[command];
            if (btnId) {
                const btn = document.getElementById(btnId);
                if (btn) {
                    btn.style.boxShadow = '0 0 20px rgba(66, 165, 245, 0.3)';
                    setTimeout(() => { btn.style.boxShadow = ''; }, 500);
                }
            }

            // If reset, clear state
            if (command === 'reset') {
                alertDismissed = false;
                lastSeenId = -1;
                trendData = null;
                const banner = document.getElementById('alert-banner');
                if (banner) banner.style.display = 'none';
                clearLog();
                drawChart();
            }

            // Log the command
            addCommandLog(command);
        }
    } catch (e) {
        console.error('Failed to send command:', e);
    }
}

function addCommandLog(command) {
    const container = document.getElementById('log-container');
    if (!container) return;

    const now = new Date();
    const timeStr = now.toTimeString().slice(0, 8);

    const descriptions = {
        'lens_degradation': '>>> INJECTED: Lens Degradation on C3 — confidence will drift down',
        'cassette_wear':    '>>> INJECTED: Cassette Wear on C2 — slow mechanical decline',
        'pill_mismatch':    '>>> INJECTED: Pill Mismatch — next batch will hard-fail',
        'reset':            '>>> COMMAND: Reset All — cassettes restored to healthy baseline',
    };

    const entry = document.createElement('div');
    entry.className = 'log-entry log-warn';
    entry.innerHTML = `<span class="log-time">${timeStr}</span>` +
        `<span class="log-msg">${descriptions[command] || command}</span>`;
    container.appendChild(entry);
    container.scrollTop = container.scrollHeight;
}


// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
//  DATA POLLING — Feed + Trend + Status every 500ms
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async function pollData() {
    try {
        // Fetch all three endpoints in parallel
        const ts = Date.now();
        const fetchOpts = { cache: 'no-store' };
        const [feedRes, trendRes, statusRes] = await Promise.all([
            fetch(`/api/feed?_=${ts}`, fetchOpts),
            fetch(`/api/trend?_=${ts}`, fetchOpts),
            fetch(`/api/status?_=${ts}`, fetchOpts),
        ]);

        const feedData = await feedRes.json();
        const newTrendData = await trendRes.json();
        const statusData = await statusRes.json();

        // Update connection status
        if (!isConnected) {
            updateConnectionStatus('connected');
            updateArchArrows(true);
            isConnected = true;
        }

        // Update all UI components
        if (newTrendData && Object.keys(newTrendData).length > 0) {
            trendData = newTrendData;
            drawChart();
        }

        if (statusData && statusData.cassettes) {
            updateCassetteCards(statusData);
            updateAlertBanner(statusData);

            // Update header stats
            setText('stat-batches', (statusData.total_batches || 0).toLocaleString());
            setText('stat-mismatches', statusData.total_mismatches || 0);
        }

        if (feedData && feedData.records) {
            updateAuditLog(feedData);
        }

    } catch (e) {
        if (isConnected) {
            updateConnectionStatus('disconnected');
            updateArchArrows(false);
            isConnected = false;
        } else if (pollCount < 10) {
            updateConnectionStatus('connecting');
        }
    }

    pollCount++;
}

function startPolling() {
    // Initial poll
    pollData();
    // Recurring poll
    setInterval(pollData, POLL_INTERVAL_MS);
}


// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
//  UTILITY
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

function setText(id, value) {
    const el = document.getElementById(id);
    if (el) el.textContent = value;
}

// Handle window resize for chart
window.addEventListener('resize', () => {
    if (chartCanvas) {
        const rect = chartCanvas.getBoundingClientRect();
        const dpr = window.devicePixelRatio || 1;
        chartCanvas.width = rect.width * dpr;
        chartCanvas.height = rect.height * dpr;
        chartCtx = chartCanvas.getContext('2d');
        chartCtx.scale(dpr, dpr);
        drawChart();
    }
});
