"""SQLite-based job queue for mirror downloads."""

import sqlite3
import time
from pathlib import Path
from enum import Enum


class Status(str, Enum):
    PENDING = "pending"
    DOWNLOADING = "downloading"
    DONE = "done"
    FAILED = "failed"


DB_SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT UNIQUE NOT NULL,
    rel_path TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    retries INTEGER NOT NULL DEFAULT 0,
    size INTEGER,
    error TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_status ON files(status);
CREATE INDEX IF NOT EXISTS idx_url ON files(url);
"""


class Queue:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._conn = sqlite3.connect(str(db_path), timeout=30)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.executescript(DB_SCHEMA)
        self._conn.commit()

    def close(self):
        self._conn.close()

    def add_url(self, url: str, rel_path: str) -> bool:
        """Add a URL to the queue. Returns True if newly added, False if exists."""
        now = time.time()
        try:
            self._conn.execute(
                "INSERT OR IGNORE INTO files (url, rel_path, status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (url, rel_path, Status.PENDING, now, now),
            )
            self._conn.commit()
            return self._conn.total_changes > 0
        except sqlite3.IntegrityError:
            return False

    def claim_job(self, worker_id: int) -> tuple[int, str, str] | None:
        """Claim the next pending job. Returns (id, url, rel_path) or None."""
        now = time.time()
        cur = self._conn.execute(
            "UPDATE files SET status = ?, updated_at = ? "
            "WHERE id = (SELECT id FROM files WHERE status = ? ORDER BY id LIMIT 1) "
            "RETURNING id, url, rel_path",
            (Status.DOWNLOADING, now, Status.PENDING),
        )
        row = cur.fetchone()
        self._conn.commit()
        return row if row else None

    def mark_done(self, file_id: int, size: int | None = None):
        """Mark a job as completed."""
        now = time.time()
        self._conn.execute(
            "UPDATE files SET status = ?, size = ?, updated_at = ? WHERE id = ?",
            (Status.DONE, size, now, file_id),
        )
        self._conn.commit()

    def mark_failed(self, file_id: int, error: str):
        """Mark a job as failed, increment retry count."""
        now = time.time()
        self._conn.execute(
            "UPDATE files SET status = ?, retries = retries + 1, error = ?, updated_at = ? "
            "WHERE id = ?",
            (Status.FAILED, error, now, file_id),
        )
        self._conn.commit()

    def requeue_failed(self) -> int:
        """Move all failed jobs back to pending. Returns count."""
        now = time.time()
        cur = self._conn.execute(
            "UPDATE files SET status = ?, updated_at = ? WHERE status = ?",
            (Status.PENDING, now, Status.FAILED),
        )
        self._conn.commit()
        return cur.rowcount

    def requeue_stale(self, timeout_seconds: float = 600) -> int:
        """Requeue downloads stuck in 'downloading' state for too long."""
        cutoff = time.time() - timeout_seconds
        cur = self._conn.execute(
            "UPDATE files SET status = ?, updated_at = ? WHERE status = ? AND updated_at < ?",
            (Status.PENDING, time.time(), Status.DOWNLOADING, cutoff),
        )
        self._conn.commit()
        return cur.rowcount

    def stats(self) -> dict[str, int]:
        """Get counts by status."""
        cur = self._conn.execute(
            "SELECT status, COUNT(*) FROM files GROUP BY status"
        )
        result = {s.value: 0 for s in Status}
        for status, count in cur.fetchall():
            result[status] = count
        result["total"] = sum(result.values())
        return result

    def pending_count(self) -> int:
        cur = self._conn.execute(
            "SELECT COUNT(*) FROM files WHERE status = ?", (Status.PENDING,)
        )
        return cur.fetchone()[0]

    def has_work(self) -> bool:
        """Check if there's any pending or failed work."""
        cur = self._conn.execute(
            "SELECT COUNT(*) FROM files WHERE status IN (?, ?)",
            (Status.PENDING, Status.FAILED),
        )
        return cur.fetchone()[0] > 0
