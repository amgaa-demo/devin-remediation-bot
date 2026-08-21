"""SQLite-backed state: one row per remediation task (issue -> Devin session).

Design choice: a single flat table, updated in place, queried for the dashboard.
This is the system of record for observability — every state transition lands here.
"""
import sqlite3
import time
from contextlib import contextmanager

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    issue_number   INTEGER PRIMARY KEY,
    issue_title    TEXT NOT NULL,
    issue_url      TEXT NOT NULL,
    session_id     TEXT,
    session_url    TEXT,
    status         TEXT NOT NULL DEFAULT 'queued',   -- queued|running|succeeded|failed|skipped_human
    status_detail  TEXT,
    pr_url         TEXT,
    error          TEXT,
    created_at     REAL NOT NULL,
    updated_at     REAL NOT NULL,
    completed_at   REAL
);
CREATE TABLE IF NOT EXISTS events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         REAL NOT NULL,
    issue_number INTEGER,
    kind       TEXT NOT NULL,
    message    TEXT NOT NULL
);
"""


@contextmanager
def db():
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init():
    with db() as conn:
        conn.executescript(SCHEMA)


def log_event(issue_number: int | None, kind: str, message: str):
    with db() as conn:
        conn.execute(
            "INSERT INTO events (ts, issue_number, kind, message) VALUES (?, ?, ?, ?)",
            (time.time(), issue_number, kind, message),
        )


def upsert_task(issue_number: int, issue_title: str, issue_url: str, status: str,
                session_id: str | None = None, session_url: str | None = None,
                error: str | None = None):
    now = time.time()
    with db() as conn:
        conn.execute(
            """INSERT INTO tasks (issue_number, issue_title, issue_url, session_id,
                                  session_url, status, error, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(issue_number) DO UPDATE SET
                 session_id=COALESCE(excluded.session_id, tasks.session_id),
                 session_url=COALESCE(excluded.session_url, tasks.session_url),
                 status=excluded.status,
                 error=excluded.error,
                 updated_at=excluded.updated_at""",
            (issue_number, issue_title, issue_url, session_id, session_url,
             status, error, now, now),
        )


def update_status(issue_number: int, status: str, status_detail: str | None = None,
                  pr_url: str | None = None, error: str | None = None,
                  completed: bool = False):
    now = time.time()
    with db() as conn:
        conn.execute(
            """UPDATE tasks SET status=?, status_detail=?,
                 pr_url=COALESCE(?, pr_url), error=?,
                 updated_at=?, completed_at=CASE WHEN ? THEN ? ELSE completed_at END
               WHERE issue_number=?""",
            (status, status_detail, pr_url, error, now, completed, now, issue_number),
        )


def active_tasks():
    with db() as conn:
        return conn.execute(
            "SELECT * FROM tasks WHERE status IN ('queued', 'running')"
        ).fetchall()


def all_tasks():
    with db() as conn:
        return conn.execute("SELECT * FROM tasks ORDER BY created_at DESC").fetchall()


def recent_events(limit: int = 50):
    with db() as conn:
        return conn.execute(
            "SELECT * FROM events ORDER BY ts DESC LIMIT ?", (limit,)
        ).fetchall()


def metrics() -> dict:
    with db() as conn:
        rows = conn.execute("SELECT status, COUNT(*) n FROM tasks GROUP BY status").fetchall()
        counts = {r["status"]: r["n"] for r in rows}
        durations = conn.execute(
            """SELECT AVG(completed_at - created_at) avg_s
               FROM tasks WHERE completed_at IS NOT NULL AND status='succeeded'"""
        ).fetchone()
    total = sum(counts.values())
    done = counts.get("succeeded", 0)
    failed = counts.get("failed", 0)
    return {
        "total_tasks": total,
        "by_status": counts,
        "success_rate": round(done / (done + failed), 3) if (done + failed) else None,
        "avg_time_to_success_seconds": round(durations["avg_s"], 1) if durations["avg_s"] else None,
    }
