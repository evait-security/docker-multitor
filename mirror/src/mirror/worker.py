"""Download workers - pull jobs from queue and download files via proxy."""

import logging
import os
import subprocess
import time
from pathlib import Path

from .queue import Queue, Status

log = logging.getLogger(__name__)


def download_file(
    url: str,
    dest_path: Path,
    proxy: str,
    timeout: int = 120,
) -> tuple[bool, str]:
    """Download a single file using wget. Returns (success, error_message)."""
    dest_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        result = subprocess.run(
            [
                "wget",
                "--quiet",
                "--proxy=on",
                "-e", "use_proxy=yes",
                "-e", f"http_proxy={proxy}",
                "-e", f"https_proxy={proxy}",
                "-e", "robots=off",
                f"--timeout={timeout}",
                "--tries=3",
                "--waitretry=5",
                "--retry-connrefused",
                "--continue",
                "--no-check-certificate",
                "--user-agent=Mozilla/5.0 (Windows NT 10.0; rv:128.0) Gecko/20100101 Firefox/128.0",
                f"--output-document={dest_path}",
                url,
            ],
            capture_output=True,
            text=True,
            timeout=timeout + 60,
        )
        if result.returncode == 0 and dest_path.exists():
            return True, ""
        error = result.stderr.strip() if result.stderr else f"exit code {result.returncode}"
        return False, error
    except subprocess.TimeoutExpired:
        return False, "timeout"
    except Exception as e:
        return False, str(e)


def worker(
    worker_id: int,
    queue: Queue,
    dest: Path,
    proxy: str,
    timeout: int = 120,
    stop_event=None,
):
    """Worker loop: claim jobs from queue and download until no work remains.
    
    Args:
        worker_id: Identifier for this worker
        queue: The job queue
        dest: Base destination directory
        proxy: Proxy URL
        timeout: Download timeout in seconds
        stop_event: threading.Event to signal shutdown
    """
    log.info(f"Worker {worker_id} started")
    idle_count = 0

    while True:
        if stop_event and stop_event.is_set():
            break

        job = queue.claim_job(worker_id)

        if job is None:
            idle_count += 1
            if idle_count > 10:
                # No work for a while, check if we should stop
                break
            time.sleep(1)
            continue

        idle_count = 0
        file_id, url, rel_path = job
        dest_path = dest / rel_path

        log.info(f"[W{worker_id}] Downloading: {rel_path}")

        success, error = download_file(url, dest_path, proxy, timeout)

        if success:
            size = dest_path.stat().st_size if dest_path.exists() else None
            queue.mark_done(file_id, size)
            log.info(f"[W{worker_id}] Done: {rel_path} ({size or 0} bytes)")
        else:
            queue.mark_failed(file_id, error)
            log.warning(f"[W{worker_id}] Failed: {rel_path} - {error}")

    log.info(f"Worker {worker_id} stopped")
