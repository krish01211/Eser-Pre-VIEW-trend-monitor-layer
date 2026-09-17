"""
Pre-VIEW Audit Engine — Synthetic Audit Record Producer
========================================================
Background thread that simulates the Eser tablet packaging machine's
Pre-VIEW audit system, emitting one audit record per cassette per cycle.

Architecture mapping:
  DC-648 mock_machine.cpp (Thread 1: Control Loop)  →  This module

Each record contains:
  batch_id, timestamp, cassette_id, tablet_sku,
  detection_confidence, imprint_match_score, mismatch_flag

Injectable faults (via shared state dict):
  - lens_degradation: Cassette C3 confidence decays ~0.001/batch
  - cassette_wear:    Cassette C2 confidence decays ~0.0007/batch
  - pill_mismatch:    Next batch gets hard mismatch (confidence ~0.40)
  - reset:            All baselines restored, DB cleared
"""

import threading
import sqlite3
import time
import random

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  CASSETTE CONFIGURATION
#  Real generic tablet names commonly dispensed in Japanese pharmacies
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

CASSETTES = {
    'C1': {'tablet': 'Amlodipine 5mg'},
    'C2': {'tablet': 'Losartan Potassium 50mg'},
    'C3': {'tablet': 'Metformin 500mg'},
    'C4': {'tablet': 'Atorvastatin 10mg'},
    'C5': {'tablet': 'Loxoprofen 60mg'},
}

# Healthy baseline values
DEFAULT_CONFIDENCE = 0.96
DEFAULT_IMPRINT    = 0.95

# Noise parameters (physically realistic for camera-based detection)
CONFIDENCE_NOISE_STD = 0.015
IMPRINT_NOISE_STD    = 0.012

# Degradation rates per batch
LENS_DECAY_RATE    = 0.001    # C3: ~60 batches to WATCH, ~110 to threshold
WEAR_DECAY_RATE    = 0.0007   # C2: slower, secondary pattern

# Hard failure threshold
MISMATCH_CONFIDENCE = 0.40


class AuditEngine:
    """
    Producer thread: emits synthetic audit records into SQLite.
    
    Mirrors DC-648's Control Loop thread — runs continuously, writes data,
    and responds to injected faults via a shared state dictionary.
    """

    def __init__(self, db_path, state, print_fn=None):
        """
        Args:
            db_path:  Path to SQLite database file
            state:    Shared dict with 'lock' key (threading.Lock) for fault injection
            print_fn: Optional callback for console output (batch progress)
        """
        self.db_path = db_path
        self.state = state
        self.print_fn = print_fn or (lambda *a: None)
        self.batch_counter = 0
        self.running = True
        self._thread = None

        # Per-cassette baselines (modified by fault injection)
        self.base_confidence = {cid: DEFAULT_CONFIDENCE for cid in CASSETTES}
        self.base_imprint    = {cid: DEFAULT_IMPRINT    for cid in CASSETTES}

    def start(self):
        """Start the engine in a background daemon thread."""
        self._thread = threading.Thread(target=self._run, daemon=True, name='AuditEngine')
        self._thread.start()
        return self._thread

    def stop(self):
        """Signal the engine to stop."""
        self.running = False

    def reset(self, conn):
        """Reset all baselines and clear the database."""
        for cid in CASSETTES:
            self.base_confidence[cid] = DEFAULT_CONFIDENCE
            self.base_imprint[cid] = DEFAULT_IMPRINT
        self.batch_counter = 0

        # Clear audit log for fresh demo restart
        conn.execute('DELETE FROM audit_log')
        conn.commit()
        self.print_fn('  [RESET] All cassettes restored to healthy baseline')

    def _run(self):
        """Main engine loop — runs until self.running is False."""
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.execute('PRAGMA journal_mode=WAL')

        while self.running:
            for cid, info in CASSETTES.items():
                if not self.running:
                    break

                self.batch_counter += 1

                # ── Read fault flags ────────────────────────────
                with self.state['lock']:
                    is_lens_degrading = self.state.get('lens_degradation', False)
                    is_wearing        = self.state.get('cassette_wear', False)
                    inject_mismatch   = self.state.pop('pill_mismatch', False)
                    do_reset          = self.state.pop('reset', False)

                # ── Handle reset ────────────────────────────────
                if do_reset:
                    with self.state['lock']:
                        self.state['lens_degradation'] = False
                        self.state['cassette_wear'] = False
                    self.reset(conn)
                    break  # Restart the cassette cycle

                # ── Apply gradual degradation ───────────────────
                if is_lens_degrading and cid == 'C3':
                    self.base_confidence['C3'] = max(0.65, self.base_confidence['C3'] - LENS_DECAY_RATE)

                if is_wearing and cid == 'C2':
                    self.base_confidence['C2'] = max(0.70, self.base_confidence['C2'] - WEAR_DECAY_RATE)
                    self.base_imprint['C2']    = max(0.55, self.base_imprint['C2'] - WEAR_DECAY_RATE * 0.8)

                # ── Generate detection values ───────────────────
                confidence = self.base_confidence[cid] + random.gauss(0, CONFIDENCE_NOISE_STD)
                confidence = max(0.0, min(1.0, confidence))

                imprint = self.base_imprint[cid] + random.gauss(0, IMPRINT_NOISE_STD)
                imprint = max(0.0, min(1.0, imprint))

                # ── Handle instant pill mismatch ────────────────
                mismatch = False
                if inject_mismatch:
                    confidence = random.uniform(0.35, 0.45)
                    imprint = random.uniform(0.20, 0.35)
                    mismatch = True

                # ── Write to SQLite ─────────────────────────────
                timestamp = time.strftime('%Y-%m-%d %H:%M:%S')

                conn.execute(
                    '''INSERT INTO audit_log
                       (batch_id, timestamp, cassette_id, tablet_sku,
                        detection_confidence, imprint_match_score, mismatch_flag)
                       VALUES (?, ?, ?, ?, ?, ?, ?)''',
                    (self.batch_counter, timestamp, cid, info['tablet'],
                     round(confidence, 4), round(imprint, 4), int(mismatch))
                )
                conn.commit()

                # ── Console output ──────────────────────────────
                status = '[FAIL]' if mismatch else '[PASS]'
                color_code = '\033[91m' if mismatch else '\033[92m'
                reset_code = '\033[0m'
                self.print_fn(
                    f'  [{timestamp.split(" ")[1]}] '
                    f'Batch #{self.batch_counter:04d} | {cid} | '
                    f'{info["tablet"]:<26s} | '
                    f'Conf: {confidence:.4f} | '
                    f'{color_code}{status}{reset_code}'
                )

                # ~200ms per cassette × 5 cassettes = ~1s per round
                time.sleep(0.2)

        conn.close()
