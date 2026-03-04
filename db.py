"""SQLite — execution_history + error_logs CRUD."""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from config import DB_PATH

_SCHEMA = """
CREATE TABLE IF NOT EXISTS execution_history (
    run_id TEXT PRIMARY KEY,
    start_time TEXT NOT NULL,
    end_time TEXT,
    total_files INTEGER DEFAULT 0,
    success_count INTEGER DEFAULT 0,
    error_count INTEGER DEFAULT 0,
    status TEXT DEFAULT 'Running'
);

CREATE TABLE IF NOT EXISTS error_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    filename TEXT NOT NULL,
    error_type TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES execution_history(run_id)
);
"""


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    with _connect() as conn:
        conn.executescript(_SCHEMA)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── execution_history ──

def create_run(run_id: str, total_files: int) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT INTO execution_history (run_id, start_time, total_files, status) VALUES (?, ?, ?, 'Running')",
            (run_id, _now(), total_files),
        )


def finish_run(run_id: str, success: int, errors: int) -> None:
    status = "Success" if errors == 0 else "Fail"
    with _connect() as conn:
        conn.execute(
            "UPDATE execution_history SET end_time=?, success_count=?, error_count=?, status=? WHERE run_id=?",
            (_now(), success, errors, status, run_id),
        )


def get_runs(limit: int = 20) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM execution_history ORDER BY start_time DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_last_run() -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM execution_history ORDER BY start_time DESC LIMIT 1"
        ).fetchone()
    return dict(row) if row else None


def get_total_stats() -> dict:
    with _connect() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(success_count),0) as total_success, COALESCE(SUM(error_count),0) as total_error FROM execution_history"
        ).fetchone()
    return dict(row)


# ── error_logs ──

def log_error(run_id: str, filename: str, error_type: str, error_message: str) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT INTO error_logs (run_id, filename, error_type, error_message, created_at) VALUES (?, ?, ?, ?, ?)",
            (run_id, filename, error_type, error_message, _now()),
        )


def get_errors(run_id: str | None = None, limit: int = 50) -> list[dict]:
    with _connect() as conn:
        if run_id:
            rows = conn.execute(
                "SELECT * FROM error_logs WHERE run_id=? ORDER BY created_at DESC LIMIT ?",
                (run_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM error_logs ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
    return [dict(r) for r in rows]


# 최초 import 시 테이블 자동 생성
init_db()
