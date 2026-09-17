"""
Pre-VIEW Trend Analyzer — Rolling Average & Drift Detection
=============================================================
Background thread that queries SQLite every ~2 seconds, computes
rolling averages via SQL window functions, and writes JSON files
for the dashboard to consume.

Architecture mapping:
  DC-648 mock_machine.cpp (Thread 2: Telemetry Consumer)  →  This module

Outputs (to data/ directory):
  trend.json  — Rolling average time series per cassette (for chart)
  feed.json   — Latest 50 audit records (for scrolling log)
  status.json — Per-cassette health classification + alerts
"""

import threading
import sqlite3
import json
import os
import time

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  HEALTH THRESHOLDS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

THRESHOLD_HEALTHY   = 0.94   # Rolling avg >= 0.94 → HEALTHY
THRESHOLD_WATCH     = 0.90   # Rolling avg >= 0.90 → WATCH
THRESHOLD_DEGRADING = 0.85   # Rolling avg >= 0.85 → DEGRADING
                             # Rolling avg <  0.85 → CRITICAL

TREND_WINDOW = 20            # Batches to look back for trend direction
TREND_DELTA  = 0.015         # Min change to classify as declining/improving

# How many data points per cassette to keep for the chart
CHART_POINTS = 150

# Analyzer interval
ANALYZE_INTERVAL_SEC = 2.0


class TrendAnalyzer:
    """
    Consumer thread: reads audit_log from SQLite, computes rolling averages
    using SQL window functions, classifies cassette health, writes JSON.
    
    Mirrors DC-648's Telemetry Consumer — runs on a timer, reads data,
    transforms it, and writes output files for the dashboard.
    """

    def __init__(self, db_path, data_dir, print_fn=None):
        """
        Args:
            db_path:  Path to SQLite database file
            data_dir: Directory to write JSON output files
            print_fn: Optional callback for console output
        """
        self.db_path = db_path
        self.data_dir = data_dir
        self.print_fn = print_fn or (lambda *a: None)
        self.running = True
        self._thread = None

    def start(self):
        """Start the analyzer in a background daemon thread."""
        self._thread = threading.Thread(target=self._run, daemon=True, name='TrendAnalyzer')
        self._thread.start()
        return self._thread

    def stop(self):
        """Signal the analyzer to stop."""
        self.running = False

    def _run(self):
        """Main analyzer loop — runs until self.running is False."""
        while self.running:
            try:
                self._analyze()
            except Exception as e:
                self.print_fn(f'  [ANALYZER ERROR] {e}')
            time.sleep(ANALYZE_INTERVAL_SEC)

    def _analyze(self):
        """Run one analysis cycle: query → compute → write JSON."""
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row

        # ── Check if we have enough data ────────────────────
        count = conn.execute('SELECT COUNT(*) FROM audit_log').fetchone()[0]
        if count < 5:
            conn.close()
            return

        # ── 1. Trend data: rolling averages for chart ───────
        trend_rows = conn.execute('''
            SELECT cassette_id, batch_id, detection_confidence, rolling_avg, id
            FROM (
                SELECT cassette_id, batch_id, detection_confidence, id,
                       AVG(detection_confidence) OVER (
                           PARTITION BY cassette_id
                           ORDER BY id
                           ROWS BETWEEN 20 PRECEDING AND CURRENT ROW
                       ) AS rolling_avg,
                       ROW_NUMBER() OVER (
                           PARTITION BY cassette_id
                           ORDER BY id DESC
                       ) AS rn
                FROM audit_log
            )
            WHERE rn <= ?
            ORDER BY cassette_id, id
        ''', (CHART_POINTS,)).fetchall()

        # Group by cassette
        trend_data = {}
        for row in trend_rows:
            cid = row['cassette_id']
            if cid not in trend_data:
                trend_data[cid] = []
            trend_data[cid].append({
                'batch_id':    row['batch_id'],
                'confidence':  round(row['detection_confidence'], 4),
                'rolling_avg': round(row['rolling_avg'], 4),
            })

        # ── 2. Feed data: latest 50 audit records ──────────
        feed_rows = conn.execute('''
            SELECT id, batch_id, timestamp, cassette_id, tablet_sku,
                   detection_confidence, imprint_match_score, mismatch_flag
            FROM audit_log
            ORDER BY id DESC
            LIMIT 50
        ''').fetchall()

        feed = []
        for r in reversed(feed_rows):
            feed.append({
                'id':           r['id'],
                'batch_id':     r['batch_id'],
                'timestamp':    r['timestamp'],
                'cassette_id':  r['cassette_id'],
                'tablet_sku':   r['tablet_sku'],
                'confidence':   round(r['detection_confidence'], 4),
                'imprint_match': round(r['imprint_match_score'], 4),
                'mismatch':     bool(r['mismatch_flag']),
            })

        # ── 3. Status: per-cassette health classification ──
        total_batches = count
        total_mismatches = conn.execute(
            'SELECT COUNT(*) FROM audit_log WHERE mismatch_flag = 1'
        ).fetchone()[0]

        cassette_status = {}
        alerts = []

        for cid in ['C1', 'C2', 'C3', 'C4', 'C5']:
            points = trend_data.get(cid, [])
            if not points:
                cassette_status[cid] = {
                    'health': 'UNKNOWN',
                    'current_confidence': 0,
                    'rolling_avg': 0,
                    'trend': 'stable',
                }
                continue

            latest = points[-1]
            avg = latest['rolling_avg']
            current = latest['confidence']

            # Health classification based on rolling average
            if avg >= THRESHOLD_HEALTHY:
                health = 'HEALTHY'
            elif avg >= THRESHOLD_WATCH:
                health = 'WATCH'
            elif avg >= THRESHOLD_DEGRADING:
                health = 'DEGRADING'
            else:
                health = 'CRITICAL'

            # Trend direction: compare current avg vs avg from TREND_WINDOW batches ago
            trend = 'stable'
            if len(points) >= TREND_WINDOW:
                old_avg = points[-TREND_WINDOW]['rolling_avg']
                delta = avg - old_avg
                if delta < -TREND_DELTA:
                    trend = 'declining'
                elif delta > TREND_DELTA:
                    trend = 'improving'

            cassette_status[cid] = {
                'health': health,
                'current_confidence': round(current, 4),
                'rolling_avg': round(avg, 4),
                'trend': trend,
            }

            # Generate alerts for degrading/critical cassettes
            if health in ('DEGRADING', 'CRITICAL'):
                alerts.append({
                    'cassette_id': cid,
                    'type': 'DRIFT',
                    'health': health,
                    'message': f'Detection confidence declining — rolling avg {avg:.3f}',
                })
            elif health == 'WATCH' and trend == 'declining':
                alerts.append({
                    'cassette_id': cid,
                    'type': 'WATCH',
                    'health': health,
                    'message': f'Confidence trending downward — rolling avg {avg:.3f}',
                })

        conn.close()

        # ── Write JSON files (atomic via temp+rename) ──────
        self._write_json('trend.json', trend_data)
        self._write_json('feed.json', {'records': feed})
        self._write_json('status.json', {
            'cassettes': cassette_status,
            'total_batches': total_batches,
            'total_mismatches': total_mismatches,
            'alerts': alerts,
        })

    def _write_json(self, filename, data):
        """Write JSON atomically: write to .tmp, then rename."""
        tmp_path = os.path.join(self.data_dir, filename + '.tmp')
        final_path = os.path.join(self.data_dir, filename)

        try:
            with open(tmp_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, separators=(',', ':'))
            # Atomic-ish rename (Windows: need to remove first)
            if os.path.exists(final_path):
                os.remove(final_path)
            os.rename(tmp_path, final_path)
        except OSError:
            pass  # Dashboard will get stale data, not a crash
