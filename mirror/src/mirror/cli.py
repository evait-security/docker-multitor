"""CLI entry point for mirror tool."""

import argparse
import logging
import os
import sys
import threading
import time
from pathlib import Path

from .queue import Queue
from .spider import spider
from .worker import worker
from .display import Display


def main():
    parser = argparse.ArgumentParser(
        description="Robust parallel Tor mirror tool using docker-multitor proxy",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  mirror http://example.onion/files/
  mirror http://example.onion/files/ /data/output
  mirror --parallel 10 http://example.onion/files/
        """,
    )
    parser.add_argument("url", help="URL to mirror (directory listing)")
    parser.add_argument("destination", nargs="?", default=".", help="Destination directory (default: current dir)")
    parser.add_argument("-p", "--parallel", type=int, default=int(os.environ.get("MIRROR_PARALLEL", "5")),
                        help="Number of parallel download workers (default: 5)")
    parser.add_argument("--proxy", default=os.environ.get("MULTITOR_PROXY", "http://127.0.0.1:16379"),
                        help="Proxy URL (default: http://127.0.0.1:16379)")
    parser.add_argument("--timeout", type=int, default=120, help="Download timeout in seconds (default: 120)")
    parser.add_argument("--fresh", action="store_true", help="Ignore existing database, start fresh")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose logging to mirror.log")

    args = parser.parse_args()

    url = args.url
    if not url.endswith("/"):
        url += "/"

    dest = Path(args.destination).resolve()
    dest.mkdir(parents=True, exist_ok=True)

    db_path = dest / ".mirror.db"
    proxy = args.proxy
    parallel = args.parallel

    # Setup logging to file (keep terminal clean for UI)
    log_path = dest / "mirror.log"
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        filename=str(log_path),
        filemode="a",
    )
    log = logging.getLogger("mirror")

    # Resume detection
    resuming = False
    if db_path.exists() and not args.fresh:
        queue = Queue(db_path)
        stats = queue.stats()
        if stats["total"] > 0:
            resuming = True
            print(f"\033[1m[*] Resuming previous mirror session\033[0m")
            print(f"    Database: {db_path}")
            print(f"    Previous: {stats['done']} done, {stats['pending']} pending, {stats['failed']} failed")
            print()
            queue.close()

    if args.fresh and db_path.exists():
        db_path.unlink()

    # Init
    print(f"[*] Mirror configuration:")
    print(f"    URL:         {url}")
    print(f"    Destination: {dest}")
    print(f"    Proxy:       {proxy}")
    print(f"    Workers:     {parallel}")
    print(f"    Log:         {log_path}")
    print()

    queue = Queue(db_path)
    display = Display(queue, parallel)

    # Outer retry loop
    while True:
        # Phase 1: Spider (discover files)
        display.render_spider("Crawling directory listings...")
        try:
            added = spider(queue, url, proxy, args.timeout)
        except KeyboardInterrupt:
            print("\n[!] Interrupted during crawl.")
            queue.close()
            sys.exit(1)

        stats = queue.stats()
        if stats["total"] == 0:
            display.render_spider("No files found. Retrying in 10s...")
            time.sleep(10)
            continue

        # Requeue any failed/stale jobs for retry
        requeued = queue.requeue_failed()
        stale = queue.requeue_stale()
        log.info(f"Requeued: {requeued} failed, {stale} stale")

        if not queue.has_work():
            display.render_final()
            break

        # Phase 2: Download workers
        stop_event = threading.Event()
        display.start_time = time.time()

        worker_queues = []
        threads = []
        for i in range(parallel):
            wq = Queue(db_path)
            worker_queues.append(wq)

            def run_worker(wid, wqueue):
                while not stop_event.is_set():
                    job = wqueue.claim_job(wid)
                    if job is None:
                        # Check if there's still pending work
                        if not wqueue.has_work():
                            break
                        time.sleep(1)
                        continue

                    file_id, file_url, rel_path = job
                    display.set_worker_status(wid, rel_path)
                    log.info(f"W{wid} downloading: {rel_path}")

                    dest_path = dest / rel_path
                    dest_path.parent.mkdir(parents=True, exist_ok=True)

                    from .worker import download_file
                    success, error = download_file(file_url, dest_path, proxy, args.timeout)

                    if success:
                        size = dest_path.stat().st_size if dest_path.exists() else 0
                        wqueue.mark_done(file_id, size)
                        display.add_bytes(size)
                        display.set_worker_status(wid, "idle")
                        log.info(f"W{wid} done: {rel_path} ({size} bytes)")
                    else:
                        wqueue.mark_failed(file_id, error)
                        display.set_worker_status(wid, f"FAILED: {rel_path}")
                        log.warning(f"W{wid} failed: {rel_path} - {error}")

                display.set_worker_status(wid, "idle")

            t = threading.Thread(target=run_worker, args=(i, wq), daemon=True)
            t.start()
            threads.append(t)

        # Monitor loop with display updates
        try:
            while any(t.is_alive() for t in threads):
                display.render()
                time.sleep(1)
        except KeyboardInterrupt:
            print("\n\n[!] Interrupted. Stopping workers...")
            stop_event.set()
            for t in threads:
                t.join(timeout=5)
            for wq in worker_queues:
                wq.close()
            # Show final state
            stats = queue.stats()
            print(f"    Saved state: {stats['done']} done, {stats['pending']+stats['downloading']} remaining")
            print(f"    Run again to resume.")
            queue.close()
            sys.exit(1)

        # Clean up worker connections
        for wq in worker_queues:
            wq.close()

        display.render()
        print()

        # Check results
        stats = queue.stats()
        if stats["failed"] == 0 and stats["pending"] == 0 and stats["downloading"] == 0:
            display.render_final()
            break

        if stats["failed"] > 0:
            log.info(f"{stats['failed']} file(s) failed. Retrying...")
            print(f"\n[!] {stats['failed']} file(s) failed. Retrying in 10s...")
            time.sleep(10)
            # Loop continues — failed jobs get requeued at top

    queue.close()


if __name__ == "__main__":
    main()
