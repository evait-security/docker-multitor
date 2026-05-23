"""Thread-safe job queue backed by SQLite for persistence.

All SQLite access is funneled through a single writer thread.
Workers and spider communicate via stdlib queue.Queue — zero contention.
"""

import logging
import queue
import sqlite3
import threading
import time
from pathlib import Path
from enum import Enum

log = logging.getLogger(__name__)


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


class MirrorQueue:
    """Thread-safe job queue with SQLite persistence.

    Architecture:
        Spider → add_url() → _commands queue → writer thread → SQLite + _jobs queue
        Workers ← get_job() ← _jobs queue
        Workers → complete()/fail() → _commands queue → writer thread → SQLite
    """

    def __init__(self, db_path: Path, fresh: bool = False):
        self._db_path = db_path

        if fresh and db_path.exists():
            db_path.unlink()

        # Job queue: workers pull from here
        self._jobs: queue.Queue[tuple[int, str, str]] = queue.Queue()

        # Command queue: all DB mutations go through here
        self._commands: queue.Queue[tuple] = queue.Queue()

        # Known URLs for fast dedup (spider adds thousands)
        self._known_urls: set[str] = set()
        self._known_lock = threading.Lock()

        # Stats — updated only by writer thread, read with lock
        self._stats_lock = threading.Lock()
        self._stats = {s.value: 0 for s in Status}
        self._stats["total"] = 0

        # Writer thread lifecycle
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._writer = threading.Thread(target=self._writer_loop, daemon=True, name="db-writer")
        self._writer.start()
        self._ready.wait()  # Block until DB is loaded

    def _writer_loop(self):
        """Single thread owning the SQLite connection. Processes all mutations."""
        conn = sqlite3.connect(str(self._db_path), timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(DB_SCHEMA)
        conn.commit()

        # Load existing state
        self._load_state(conn)
        self._ready.set()

        while not self._stop.is_set():
            try:
                cmd = self._commands.get(timeout=0.1)
            except queue.Empty:
                continue

            self._process_command(conn, cmd)

            # Drain remaining commands (batch processing)
            drained = 0
            while drained < 200:
                try:
                    cmd = self._commands.get_nowait()
                    self._process_command(conn, cmd)
                    drained += 1
                except queue.Empty:
                    break

            conn.commit()

        # Final drain on shutdown
        while not self._commands.empty():
            try:
                cmd = self._commands.get_nowait()
                self._process_command(conn, cmd)
            except queue.Empty:
                break
        conn.commit()
        conn.close()

    def _process_command(self, conn: sqlite3.Connection, cmd: tuple):
        """Process a single command. Called only from writer thread."""
        op = cmd[0]
        now = time.time()

        if op == "add":
            _, url, rel_path = cmd
            try:
                cur = conn.execute(
                    "INSERT INTO files (url, rel_path, status, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (url, rel_path, Status.PENDING, now, now),
                )
                file_id = cur.lastrowid
                self._jobs.put((file_id, url, rel_path))
                self._update_stats(pending=1)
            except sqlite3.IntegrityError:
                pass  # Already exists

        elif op == "done":
            _, file_id, size = cmd
            conn.execute(
                "UPDATE files SET status = ?, size = ?, updated_at = ? WHERE id = ?",
                (Status.DONE, size, now, file_id),
            )
            self._update_stats(downloading=-1, done=1)

        elif op == "failed":
            _, file_id, error = cmd
            conn.execute(
                "UPDATE files SET status = ?, retries = retries + 1, error = ?, updated_at = ? "
                "WHERE id = ?",
                (Status.FAILED, error, now, file_id),
            )
            self._update_stats(downloading=-1, failed=1)

        elif op == "requeue_failed":
            cur = conn.execute(
                "UPDATE files SET status = ?, updated_at = ? WHERE status = ? "
                "RETURNING id, url, rel_path",
                (Status.PENDING, now, Status.FAILED),
            )
            rows = cur.fetchall()
            for row in rows:
                self._jobs.put(row)
            self._update_stats(failed=-len(rows), pending=len(rows))

        elif op == "requeue_stale":
            _, timeout_seconds = cmd
            cutoff = now - timeout_seconds
            cur = conn.execute(
                "UPDATE files SET status = ?, updated_at = ? "
                "WHERE status = ? AND updated_at < ? "
                "RETURNING id, url, rel_path",
                (Status.PENDING, now, Status.DOWNLOADING, cutoff),
            )
            rows = cur.fetchall()
            for row in rows:
                self._jobs.put(row)
            self._update_stats(downloading=-len(rows), pending=len(rows))

    def _load_state(self, conn: sqlite3.Connection):
        """Load existing DB state on startup. Requeue stale downloads."""
        now = time.time()

        # Load all known URLs for dedup
        cur = conn.execute("SELECT url FROM files")
        with self._known_lock:
            self._known_urls = {row[0] for row in cur.fetchall()}

        # Requeue anything stuck in 'downloading' (leftover from crash)
        conn.execute(
            "UPDATE files SET status = ?, updated_at = ? WHERE status = ?",
            (Status.PENDING, now, Status.DOWNLOADING),
        )
        conn.commit()

        # Load pending jobs into the queue
        cur = conn.execute(
            "SELECT id, url, rel_path FROM files WHERE status = ? ORDER BY id",
            (Status.PENDING,),
        )
        for row in cur.fetchall():
            self._jobs.put(row)

        # Compute stats
        cur = conn.execute("SELECT status, COUNT(*) FROM files GROUP BY status")
        with self._stats_lock:
            self._stats = {s.value: 0 for s in Status}
            for status, count in cur.fetchall():
                self._stats[status] = count
            self._stats["total"] = sum(v for k, v in self._stats.items() if k != "total")

    def _update_stats(self, **deltas):
        """Update stats counters atomically."""
        with self._stats_lock:
            for key, delta in deltas.items():
                self._stats[key] = self._stats.get(key, 0) + delta
            self._stats["total"] = sum(
                v for k, v in self._stats.items() if k != "total"
            )

    # --- Public API (thread-safe, called from any thread) ---

    def add_url(self, url: str, rel_path: str) -> bool:
        """Add a URL to the queue. Returns True if new, False if already known."""
        with self._known_lock:
            if url in self._known_urls:
                return False
            self._known_urls.add(url)
        self._commands.put(("add", url, rel_path))
        return True

    def get_job(self, timeout: float = 1.0) -> tuple[int, str, str] | None:
        """Get next job for a worker. Blocks up to timeout. Returns (id, url, rel_path) or None."""
        try:
            job = self._jobs.get(timeout=timeout)
            self._update_stats(pending=-1, downloading=1)
            return job
        except queue.Empty:
            return None

    def complete(self, file_id: int, size: int):
        """Mark a job as successfully downloaded."""
        self._commands.put(("done", file_id, size))

    def fail(self, file_id: int, error: str):
        """Mark a job as failed."""
        self._commands.put(("failed", file_id, error))

    def requeue_failed(self):
        """Move all failed jobs back to pending."""
        self._commands.put(("requeue_failed",))

    def requeue_stale(self, timeout_seconds: float = 600):
        """Requeue downloads stuck too long."""
        self._commands.put(("requeue_stale", timeout_seconds))

    def stats(self) -> dict[str, int]:
        """Get current stats (thread-safe, reads from memory)."""
        with self._stats_lock:
            return dict(self._stats)

    def has_pending_jobs(self) -> bool:
        """Check if there are jobs in the queue."""
        return not self._jobs.empty()

    def has_failed(self) -> bool:
        """Check if there are failed jobs."""
        with self._stats_lock:
            return self._stats.get("failed", 0) > 0

    def close(self):
        """Stop the writer thread and flush remaining writes."""
        self._stop.set()
        self._writer.join(timeout=10)
