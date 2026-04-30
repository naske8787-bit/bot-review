import os
import sqlite3
from contextlib import contextmanager

DB_PATH = os.path.join(os.path.dirname(__file__), 'mining.db')


def init_db():
    with get_conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS shifts (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            machine  TEXT NOT NULL,
            crew     TEXT NOT NULL,
            date     TEXT NOT NULL,
            shift    TEXT NOT NULL,
            tons     REAL DEFAULT 0,
            UNIQUE(machine, crew, date, shift)
        );

        CREATE TABLE IF NOT EXISTS shift_faults (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            shift_id   INTEGER NOT NULL REFERENCES shifts(id) ON DELETE CASCADE,
            fault_code TEXT NOT NULL,
            count      INTEGER DEFAULT 1,
            UNIQUE(shift_id, fault_code)
        );

        -- Raw timestamped fault events (before allocation to a shift)
        CREATE TABLE IF NOT EXISTS raw_fault_events (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            machine     TEXT NOT NULL,
            crew        TEXT NOT NULL,
            event_ts    TEXT NOT NULL,   -- ISO datetime string, e.g. '2026-01-07 14:32:00'
            fault_code  TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_shifts_mc   ON shifts(machine, crew);
        CREATE INDEX IF NOT EXISTS idx_faults_sid  ON shift_faults(shift_id);
        CREATE INDEX IF NOT EXISTS idx_rfe_machine ON raw_fault_events(machine, crew);
        """)


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
