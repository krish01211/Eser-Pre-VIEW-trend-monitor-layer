"""
Pre-VIEW Mock Pharmacy — Main Entry Point
===========================================
Runnable demo: creates the SQLite database, starts the audit engine
and trend analyzer threads, and runs a command reader loop.

Architecture mapping:
  DC-648 mock_machine.cpp (main + Thread 4: Command Reader)  →  This module

Usage:
  python engine/mock_pharmacy.py
  Then start server.py in another terminal and open http://localhost:8080

Or use run.bat to launch everything at once.
"""

import os
import sys
import io
import sqlite3
import json
import time
import threading

# Force UTF-8 on Windows console (prevents cp1252 encoding errors)
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# Add parent directory to path so we can run from project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.audit_engine import AuditEngine, CASSETTES
from engine.trend_analyzer import TrendAnalyzer

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  CONFIGURATION
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

DATA_DIR    = 'data'
DB_FILENAME = 'audit_log.db'
COMMAND_FILE = os.path.join(DATA_DIR, 'command.json')
COMMAND_POLL_SEC = 0.5

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  DATABASE SCHEMA
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

CREATE_TABLE_SQL = '''
CREATE TABLE IF NOT EXISTS audit_log (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id             INTEGER NOT NULL,
    timestamp            TEXT    NOT NULL,
    cassette_id          TEXT    NOT NULL,
    tablet_sku           TEXT    NOT NULL,
    detection_confidence REAL    NOT NULL,
    imprint_match_score  REAL    NOT NULL,
    mismatch_flag        INTEGER NOT NULL DEFAULT 0
)
'''

CREATE_INDEX_SQL = '''
CREATE INDEX IF NOT EXISTS idx_cassette_batch
ON audit_log (cassette_id, batch_id)
'''


def init_database(db_path):
    """Create the SQLite database and schema."""
    conn = sqlite3.connect(db_path)
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute(CREATE_TABLE_SQL)
    conn.execute(CREATE_INDEX_SQL)
    conn.commit()
    conn.close()


def print_banner(db_path):
    """Print the startup banner (mirrors DC-648's console output style)."""
    print()
    print('  =======================================================')
    print('   Pre-VIEW Audit Trend Monitor -- Pharmacy Engine')
    print('  =======================================================')
    print()
    print(f'  Database:  {db_path} (SQLite WAL)')
    print(f'  Cassettes: {len(CASSETTES)} active')
    print()
    print('  +----------------------------------------------+')
    for cid, info in CASSETTES.items():
        print(f'  |  {cid}  {info["tablet"]:<40s}|')
    print('  +----------------------------------------------+')
    print()


def read_command():
    """
    Read a command from data/command.json (written by the dashboard).
    After reading, the file is deleted to prevent re-processing.
    Returns the command string, or None if no command is pending.
    
    Same pattern as DC-648's readCommand() in mock_machine.cpp.
    """
    if not os.path.exists(COMMAND_FILE):
        return None

    try:
        with open(COMMAND_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        os.remove(COMMAND_FILE)
        return data.get('command', None)
    except (json.JSONDecodeError, OSError, KeyError):
        # Malformed or partially-written file — skip it
        try:
            os.remove(COMMAND_FILE)
        except OSError:
            pass
        return None


def main():
    """Main entry point — start engine, analyzer, and command reader."""

    # ── Setup ───────────────────────────────────────────────
    os.makedirs(DATA_DIR, exist_ok=True)

    db_path = os.path.join(DATA_DIR, DB_FILENAME)
    init_database(db_path)
    print_banner(db_path)

    # Shared state for fault injection (same pattern as DC-648's atomic flags)
    state = {
        'lock': threading.Lock(),
        'lens_degradation': False,
        'cassette_wear': False,
        # 'pill_mismatch' and 'reset' are set/popped as one-shot commands
    }

    # ── Start engine thread ─────────────────────────────────
    engine = AuditEngine(db_path, state, print_fn=print)
    engine.start()
    print('  [READY] Audit engine running (5 cassettes × 0.2s)')

    # ── Start trend analyzer thread ─────────────────────────
    analyzer = TrendAnalyzer(db_path, DATA_DIR, print_fn=print)
    analyzer.start()
    print('  [READY] Trend analyzer running (2s interval)')

    # ── Command reader loop (main thread) ───────────────────
    print(f'  [READY] Command reader active ({COMMAND_FILE})')
    print()
    print('  Audit records streaming below:')
    print('  ' + '-' * 70)
    print()

    try:
        while True:
            command = read_command()
            if command:
                handle_command(command, state, engine)
            time.sleep(COMMAND_POLL_SEC)

    except KeyboardInterrupt:
        print('\n\n  [SHUTDOWN] Stopping engine...')
        engine.stop()
        analyzer.stop()
        print('  [SHUTDOWN] Done.\n')


def handle_command(command, state, engine):
    """
    Process a command from the dashboard.
    Mirrors DC-648's command handler in mock_machine.cpp.
    """
    command_descriptions = {
        'lens_degradation':  '>>> FAULT INJECTED: Lens degradation on Cassette C3',
        'cassette_wear':     '>>> FAULT INJECTED: Cassette wear on C2',
        'pill_mismatch':     '>>> FAULT INJECTED: Pill mismatch — next batch will fail',
        'reset':             '>>> COMMAND: Reset all cassettes to healthy baseline',
    }

    desc = command_descriptions.get(command, f'>>> UNKNOWN COMMAND: {command}')
    print(f'\n  \033[93m{desc}\033[0m\n')

    with state['lock']:
        if command == 'lens_degradation':
            state['lens_degradation'] = True
        elif command == 'cassette_wear':
            state['cassette_wear'] = True
        elif command == 'pill_mismatch':
            state['pill_mismatch'] = True
        elif command == 'reset':
            state['reset'] = True


if __name__ == '__main__':
    main()
