# Pre-VIEW Audit Trend Monitor

**Proactive drift detection for the Takazono Eser Pre-VIEW tablet audit system.**

A companion analytics layer that watches detection confidence *across* batches over time, catching a slowly degrading cassette or camera **before** it causes a real audit failure — instead of only reacting after a bad detection.

> **Synthetic Data Note:** This project uses fully synthetic audit data generated locally. No real patient data, no real pharmaceutical images, and no connection to any production system. The degradation patterns (lens fouling, cassette wear) are physically plausible scenarios designed to demonstrate the trend-detection concept.

---

## Architecture

```
┌──────────────────┐     ┌──────────────────┐     ┌──────────────────┐     ┌──────────────────┐
│  Audit Engine     │     │  SQLite DB        │     │  Trend Analyzer   │     │  Dashboard        │
│  (Python thread)  │────►│  (audit_log.db)   │────►│  (Python thread)  │────►│  HTML/JS          │
│  Emits 1 batch/s  │     │  WAL mode         │     │  SQL window fns   │     │  Live chart       │
│  per cassette     │     │  Append-only      │     │  Rolling avg      │     │  + scrolling feed │
└──────────────────┘     └──────────────────┘     └──────────────────┘     └──────────────────┘
       │                                                  │
       │ Injectable faults:                               │ Writes JSON to data/
       │ - lens degradation (C3)                          │ every 2 seconds
       │ - cassette wear (C2)                             │
       │ - pill mismatch (instant)                        │
```

**How data flows:**

```
Python engine/mock_pharmacy.py              Python server.py              Browser Dashboard
     │                                            │                              │
     ├─ Thread 1: Audit Engine                    │                              │
     │  └─ INSERT → data/audit_log.db             │                              │
     │                                            │                              │
     ├─ Thread 2: Trend Analyzer                  │                              │
     │  └─ SELECT → data/trend.json          ←────┤── GET /api/trend ────────────┤ Trend Chart
     │  └─ SELECT → data/feed.json           ←────┤── GET /api/feed  ────────────┤ Audit Log
     │  └─ SELECT → data/status.json         ←────┤── GET /api/status ───────────┤ Cassette Cards
     │                                            │                              │
     ├─ Thread 3: Command Reader                  │                              │
     │  └─ reads data/command.json  ←─────────────┤←─ POST /api/command ←────────┤ Fault Buttons
```

---

## Project Structure

```
├── engine/
│   ├── audit_engine.py        # Producer thread: synthetic audit records → SQLite
│   ├── trend_analyzer.py      # Consumer thread: SQL window functions → JSON
│   └── mock_pharmacy.py       # Main entry point: starts threads + command reader
├── dashboard/
│   ├── index.html             # Pharmacist terminal view + trend chart
│   ├── style.css              # Clinical dark theme (pharmaceutical palette)
│   └── app.js                 # Polling, canvas chart, cassette cards, commands
├── server.py                  # Zero-dependency Python HTTP bridge
├── run.bat                    # One-click demo launcher
└── README.md
```

---

## Quick Start

### Prerequisites
- **Python 3.8+** (no pip install needed — uses only built-in modules: `sqlite3`, `http.server`, `threading`, `json`)

### Run

```bash
# Option 1: One-click launch
run.bat

# Option 2: Manual (two terminals)
python engine/mock_pharmacy.py       # Terminal 1: Start the audit engine
python server.py                     # Terminal 2: Start the dashboard server
# Open http://localhost:8080 in your browser
```

---

## Demo Scenarios

### 1. Normal Operation
Engine runs with 5 cassettes (C1–C5), each emitting ~1 audit record per second. Dashboard shows all cassettes green, trend lines flat and stable at ~0.96 confidence, scrolling audit feed all passing.

### 2. Hard Fault — Pill Mismatch
Click **"Inject Pill Mismatch"** in the Fault Simulator panel.
- Next batch immediately gets `mismatch_flag=true`, confidence drops to ~0.40
- Audit log flashes red with `✗ MISMATCH`
- This mirrors Pre-VIEW's real per-batch audit behavior — instant detection of a wrong tablet

### 3. ⭐ Early Warning — Lens Degradation (the key demo)
Click **"Simulate Lens Degradation (C3)"** in the Fault Simulator panel.
- Cassette C3's detection confidence starts drifting downward gradually (~0.001 per batch)
- **Every individual batch still technically passes** — confidence remains above the 0.85 threshold
- **But the trend chart visibly bends downward** while the per-batch status stays "✓ PASS"
- Cassette C3's health card transitions: HEALTHY → WATCH (amber) → DEGRADING (red)
- The alert banner appears: "DRIFT DETECTED — EARLY WARNING"
- **This is the proof of concept**: a per-batch audit system says everything is fine, while the trend layer is already raising a flag

Click **"Reset All Cassettes"** to restore everything to healthy baseline for another demo run.

---

## Code Walkthrough (Interview Talking Points)

### `engine/audit_engine.py` — The Producer
- Background thread emitting synthetic audit records with configurable noise
- Injectable fault system: `lens_degradation` decays C3 confidence by 0.001/batch
- Physically plausible model: Gaussian noise (σ=0.015) on a drifting baseline
- Thread-safe fault injection via shared dictionary with `threading.Lock`

### `engine/trend_analyzer.py` — The Analytics Core
- SQL window functions compute rolling 20-batch averages per cassette:
  ```sql
  AVG(detection_confidence) OVER (
      PARTITION BY cassette_id ORDER BY batch_id
      ROWS BETWEEN 20 PRECEDING AND CURRENT ROW
  ) AS rolling_avg
  ```
- Health classification: HEALTHY (≥0.94) → WATCH (≥0.90) → DEGRADING (≥0.85) → CRITICAL (<0.85)
- Trend direction detection: compares current vs 20-batch-ago rolling average
- Atomic JSON file writes (temp + rename) for safe concurrent reads

### `dashboard/app.js` — The Live Visualization
- Polls 3 API endpoints in parallel every 500ms
- Multi-line canvas chart draws 5 cassette trend lines with a dashed fail-threshold at 0.85
- C3 drawn last (on top) with a subtle gradient fill for visual emphasis
- Same command-injection pattern as the DC-648 project — POST to `/api/command`

---

## Why This Project Matters for Takazono

Pre-VIEW already audits each packaging batch individually — it catches a wrong tablet *the moment* it happens. But it doesn't track confidence trends *across* batches over time. A slowly dirtying camera lens, a wearing cassette mechanism, or a medication that keeps triggering low-confidence matches wouldn't show up as a pattern until it eventually causes a hard audit failure.

This project fills that gap: **turn a reactive safety system into a proactive maintenance one**, directly aligned with Takazono's stated mission of protecting patient safety and their R&D roadmap of applying AI and big data to the pharmacy industry.

---

## License

Demo project for interview discussion. Not intended for production use.
