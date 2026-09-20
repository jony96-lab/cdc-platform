import sqlite3
import threading
import time

from .config import settings

_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS pipelines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    slug TEXT NOT NULL UNIQUE,
    source_engine TEXT NOT NULL,
    source_host TEXT NOT NULL,
    source_port INTEGER NOT NULL,
    source_db TEXT NOT NULL,
    source_user TEXT NOT NULL,
    source_tables TEXT NOT NULL DEFAULT '',
    topic_prefix TEXT NOT NULL,
    server_id TEXT,
    target_engine TEXT NOT NULL,
    target_host TEXT NOT NULL,
    target_port INTEGER NOT NULL,
    target_db TEXT NOT NULL,
    target_user TEXT NOT NULL,
    source_connector TEXT NOT NULL,
    sink_connector TEXT NOT NULL,
    secrets_file TEXT NOT NULL,
    notes TEXT DEFAULT '',
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS alerts (
    fingerprint TEXT PRIMARY KEY,
    alertname TEXT NOT NULL,
    severity TEXT DEFAULT 'warning',
    status TEXT NOT NULL,
    summary TEXT DEFAULT '',
    description TEXT DEFAULT '',
    labels_json TEXT DEFAULT '{}',
    starts_at TEXT,
    ends_at TEXT,
    updated_at REAL NOT NULL
);
"""


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(settings.portal_db_path, check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c


def init_db() -> None:
    with _lock, _conn() as c:
        c.executescript(SCHEMA)


def insert_pipeline(data: dict) -> int:
    cols = list(data.keys())
    with _lock, _conn() as c:
        cur = c.execute(
            f"INSERT INTO pipelines ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
            [data[k] for k in cols],
        )
        return cur.lastrowid


def list_pipelines() -> list[dict]:
    with _lock, _conn() as c:
        rows = c.execute("SELECT * FROM pipelines ORDER BY id").fetchall()
    return [dict(r) for r in rows]


def get_pipeline(pid: int) -> dict | None:
    with _lock, _conn() as c:
        row = c.execute("SELECT * FROM pipelines WHERE id=?", (pid,)).fetchone()
    return dict(row) if row else None


def delete_pipeline(pid: int) -> None:
    with _lock, _conn() as c:
        c.execute("DELETE FROM pipelines WHERE id=?", (pid,))


def upsert_alert(a: dict) -> None:
    with _lock, _conn() as c:
        c.execute(
            """INSERT INTO alerts (fingerprint, alertname, severity, status, summary,
                                   description, labels_json, starts_at, ends_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(fingerprint) DO UPDATE SET
                 status=excluded.status, summary=excluded.summary,
                 description=excluded.description, labels_json=excluded.labels_json,
                 starts_at=excluded.starts_at, ends_at=excluded.ends_at,
                 updated_at=excluded.updated_at""",
            (
                a["fingerprint"],
                a["alertname"],
                a["severity"],
                a["status"],
                a["summary"],
                a["description"],
                a["labels_json"],
                a["starts_at"],
                a["ends_at"],
                time.time(),
            ),
        )


def list_alerts(active_only: bool = False, limit: int = 100) -> list[dict]:
    q = "SELECT * FROM alerts"
    if active_only:
        q += " WHERE status='firing'"
    q += " ORDER BY updated_at DESC LIMIT ?"
    with _lock, _conn() as c:
        rows = c.execute(q, (limit,)).fetchall()
    return [dict(r) for r in rows]


def count_active_alerts() -> int:
    with _lock, _conn() as c:
        row = c.execute("SELECT COUNT(*) n FROM alerts WHERE status='firing'").fetchone()
    return row["n"]
