"""
persistence.py — SQLite persistence layer.
Stores last 10 scores per mode/difficulty, settings, and scenario metadata.
"""

import sqlite3
import json
import logging
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Optional, Any

log = logging.getLogger(__name__)
DB_PATH = Path(__file__).parent.parent / "data" / "simulator.db"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Create tables if they don't exist."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS scores (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp       TEXT    NOT NULL,
                mode            TEXT    NOT NULL,
                difficulty      TEXT    NOT NULL,
                shift_duration  INTEGER NOT NULL,
                score           INTEGER NOT NULL,
                grade           TEXT    NOT NULL,
                on_target_pct   REAL,
                alarms_fired    INTEGER,
                scrams          INTEGER,
                mwh_generated   REAL,
                extra_json      TEXT
            );

            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS scenario_meta (
                filename    TEXT PRIMARY KEY,
                name        TEXT,
                description TEXT,
                type        TEXT,
                created_at  TEXT
            );
        """)
    log.info("Database initialised at %s", DB_PATH)


# ── Scores ────────────────────────────────────────────────────────────

def save_score(mode: str, difficulty: str, shift_duration: int, score: int,
               grade: str, on_target_pct: float = None, alarms_fired: int = 0,
               scrams: int = 0, mwh_generated: float = 0.0, extra: dict = None):
    with _connect() as conn:
        conn.execute("""
            INSERT INTO scores
                (timestamp, mode, difficulty, shift_duration, score, grade,
                 on_target_pct, alarms_fired, scrams, mwh_generated, extra_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            datetime.utcnow().isoformat(),
            mode, difficulty, shift_duration, score, grade,
            on_target_pct, alarms_fired, scrams, mwh_generated,
            json.dumps(extra) if extra else None,
        ))
        # Keep only last 10 per mode+difficulty
        conn.execute("""
            DELETE FROM scores WHERE id NOT IN (
                SELECT id FROM scores
                WHERE mode = ? AND difficulty = ?
                ORDER BY timestamp DESC
                LIMIT 10
            ) AND mode = ? AND difficulty = ?
        """, (mode, difficulty, mode, difficulty))


def get_scores(mode: str = None, difficulty: str = None) -> List[Dict]:
    with _connect() as conn:
        if mode and difficulty:
            rows = conn.execute("""
                SELECT * FROM scores WHERE mode=? AND difficulty=?
                ORDER BY timestamp DESC LIMIT 10
            """, (mode, difficulty)).fetchall()
        elif mode:
            rows = conn.execute("""
                SELECT * FROM scores WHERE mode=?
                ORDER BY timestamp DESC LIMIT 10
            """, (mode,)).fetchall()
        else:
            rows = conn.execute("""
                SELECT * FROM scores ORDER BY timestamp DESC LIMIT 50
            """).fetchall()
    return [dict(r) for r in rows]


def get_leaderboard() -> Dict[str, List[Dict]]:
    """Returns top 10 per mode/difficulty combination."""
    modes       = ["free", "dispatch", "incident"]
    difficulties = ["easy", "normal", "hard", "extreme"]
    result = {}
    for m in modes:
        for d in difficulties:
            key    = f"{m}_{d}"
            scores = get_scores(m, d)
            if scores:
                result[key] = scores
    return result


# ── Settings ──────────────────────────────────────────────────────────

def get_setting(key: str, default: Any = None) -> Any:
    with _connect() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    if row is None:
        return default
    try:
        return json.loads(row["value"])
    except Exception:
        return row["value"]


def set_setting(key: str, value: Any):
    with _connect() as conn:
        conn.execute("""
            INSERT INTO settings (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
        """, (key, json.dumps(value)))


def get_all_settings() -> Dict[str, Any]:
    with _connect() as conn:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
    result = {}
    for row in rows:
        try:
            result[row["key"]] = json.loads(row["value"])
        except Exception:
            result[row["key"]] = row["value"]
    return result


# ── Scenario metadata ─────────────────────────────────────────────────

def upsert_scenario_meta(filename: str, name: str, description: str, stype: str):
    with _connect() as conn:
        conn.execute("""
            INSERT INTO scenario_meta (filename, name, description, type, created_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(filename) DO UPDATE SET
                name=excluded.name,
                description=excluded.description,
                type=excluded.type
        """, (filename, name, description, stype, datetime.utcnow().isoformat()))


def delete_scenario_meta(filename: str):
    with _connect() as conn:
        conn.execute("DELETE FROM scenario_meta WHERE filename=?", (filename,))


def get_scenario_list() -> List[Dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM scenario_meta ORDER BY created_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]
