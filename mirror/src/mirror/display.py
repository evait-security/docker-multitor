"""Terminal UI for mirror progress display."""

import os
import shutil
import time
from .queue import MirrorQueue, Status


class Display:
    """Real-time terminal progress display."""

    def __init__(self, queue: MirrorQueue, num_workers: int):
        self.queue = queue
        self.num_workers = num_workers
        self.start_time = time.time()
        self.worker_status: dict[int, str] = {}
        self.last_done = 0
        self.bytes_total = 0
        self.spider_status = "starting..."
        self._lines_printed = 0

    def set_worker_status(self, worker_id: int, status: str):
        self.worker_status[worker_id] = status

    def add_bytes(self, n: int):
        self.bytes_total += n

    def _clear_lines(self):
        if self._lines_printed > 0:
            # Move cursor up and clear each line
            for _ in range(self._lines_printed):
                print("\033[A\033[2K", end="")
            self._lines_printed = 0

    def _format_size(self, size_bytes: int) -> str:
        if size_bytes < 1024:
            return f"{size_bytes} B"
        elif size_bytes < 1024 * 1024:
            return f"{size_bytes / 1024:.1f} KB"
        elif size_bytes < 1024 * 1024 * 1024:
            return f"{size_bytes / (1024 * 1024):.1f} MB"
        else:
            return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"

    def _format_time(self, seconds: float) -> str:
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = int(seconds % 60)
        if h > 0:
            return f"{h}h {m:02d}m {s:02d}s"
        return f"{m:02d}m {s:02d}s"

    def _progress_bar(self, done: int, total: int, width: int = 30) -> str:
        if total == 0:
            return f"[{'─' * width}]"
        ratio = min(done / total, 1.0)
        filled = int(width * ratio)
        bar = "█" * filled + "░" * (width - filled)
        pct = ratio * 100
        return f"[{bar}] {pct:.1f}%"

    def render(self):
        """Render the current state to terminal."""
        self._clear_lines()

        stats = self.queue.stats()
        elapsed = time.time() - self.start_time
        term_width = shutil.get_terminal_size((80, 24)).columns

        lines = []

        # Header
        lines.append(f"\033[1m{'─' * min(term_width, 70)}\033[0m")

        # Progress bar
        done = stats["done"]
        total = stats["total"]
        bar = self._progress_bar(done, total)
        lines.append(f"  Progress: {bar}  {done}/{total} files")

        # Stats line
        speed = ""
        if elapsed > 0 and self.bytes_total > 0:
            bps = self.bytes_total / elapsed
            speed = f" │ Speed: {self._format_size(int(bps))}/s"

        lines.append(
            f"  Elapsed: {self._format_time(elapsed)} │ "
            f"Downloaded: {self._format_size(self.bytes_total)}{speed}"
        )

        # Status counts
        lines.append(
            f"  \033[32m✓ Done: {stats['done']}\033[0m │ "
            f"\033[34m↓ Active: {stats['downloading']}\033[0m │ "
            f"\033[33m◌ Pending: {stats['pending']}\033[0m │ "
            f"\033[31m✗ Failed: {stats['failed']}\033[0m"
        )

        # Spider status
        lines.append(f"  \033[33m⟳\033[0m Spider: {self.spider_status}")

        # Worker status
        lines.append("")
        lines.append("  \033[1mWorkers:\033[0m")
        for i in range(self.num_workers):
            status = self.worker_status.get(i, "idle")
            # Truncate long filenames
            max_len = min(term_width, 70) - 12
            if len(status) > max_len:
                status = "…" + status[-(max_len - 1):]
            indicator = "\033[34m↓\033[0m" if status != "idle" else "\033[90m·\033[0m"
            lines.append(f"    {indicator} W{i}: {status}")

        lines.append(f"\033[1m{'─' * min(term_width, 70)}\033[0m")

        output = "\n".join(lines)
        print(output)
        self._lines_printed = len(lines)

    def render_spider(self, message: str):
        """Show spider progress."""
        self._clear_lines()
        lines = [
            f"\033[1m{'─' * 50}\033[0m",
            f"  \033[33m⟳\033[0m Spider: {message}",
            f"\033[1m{'─' * 50}\033[0m",
        ]
        print("\n".join(lines))
        self._lines_printed = len(lines)

    def render_final(self):
        """Show final summary."""
        self._clear_lines()
        stats = self.queue.stats()
        elapsed = time.time() - self.start_time

        print(f"\033[1m{'─' * 50}\033[0m")
        print(f"  \033[32m✓ Mirror complete\033[0m")
        print(f"  Files:      {stats['done']}")
        print(f"  Downloaded: {self._format_size(self.bytes_total)}")
        print(f"  Duration:   {self._format_time(elapsed)}")
        if elapsed > 0 and self.bytes_total > 0:
            print(f"  Avg speed:  {self._format_size(int(self.bytes_total / elapsed))}/s")
        if stats["failed"] > 0:
            print(f"  \033[31mFailed: {stats['failed']}\033[0m")
        print(f"\033[1m{'─' * 50}\033[0m")
