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

    # Requeue any failed/stale jobs from previous run
    requeued = queue.requeue_failed()
    stale = queue.requeue_stale()
    if requeued or stale:
        log.info(f"Requeued: {requeued} failed, {stale} stale")

    # --- Run spider + workers concurrently ---
    stop_event = threading.Event()
    spider_done = threading.Event()
    display.start_time = time.time()

    # Spider thread
    def run_spider():
        try:
            spider_queue = Queue(db_path)
            spider(spider_queue, url, proxy, args.timeout,
                   status_callback=lambda msg: setattr(display, 'spider_status', msg))
            spider_queue.close()
        except Exception as e:
            log.error(f"Spider error: {e}")
        finally:
            display.spider_status = "done ✓"
            spider_done.set()

    spider_thread = threading.Thread(target=run_spider, daemon=True)
    spider_thread.start()

    # Wait briefly for spider to find first files
    time.sleep(2)

    # Worker threads
    worker_queues = []
    worker_threads = []
    for i in range(parallel):

        def run_worker(wid):
            # Each worker creates its own DB connection in its own thread
            wqueue = Queue(db_path)
            worker_queues.append(wqueue)
            idle_count = 0
            while not stop_event.is_set():
                job = wqueue.claim_job(wid)
                if job is None:
                    idle_count += 1
                    # If spider is done and no more work, exit
                    if spider_done.is_set() and not wqueue.has_work():
                        break
                    # Be patient while spider is still running
                    if idle_count > 30 and spider_done.is_set():
                        break
                    time.sleep(1)
                    continue

                idle_count = 0
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

            display.set_worker_status(wid, "done")
            wqueue.close()

        t = threading.Thread(target=run_worker, args=(i,), daemon=True)
        t.start()
        worker_threads.append(t)

    # Monitor loop with display updates
    try:
        while spider_thread.is_alive() or any(t.is_alive() for t in worker_threads):
            display.render()
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n\n[!] Interrupted. Stopping...")
        stop_event.set()
        spider_thread.join(timeout=5)
        for t in worker_threads:
            t.join(timeout=5)
        stats = queue.stats()
        print(f"    Saved state: {stats['done']} done, {stats['pending']+stats['downloading']} remaining")
        print(f"    Run again to resume.")
        queue.close()
        sys.exit(1)

    display.render()
    print()

    # Check results — retry loop for failed files
    stats = queue.stats()
    if stats["failed"] > 0:
        log.info(f"{stats['failed']} file(s) failed. Starting retry cycle...")
        print(f"\n[!] {stats['failed']} file(s) failed. Retrying...")

        while stats["failed"] > 0:
            queue.requeue_failed()
            time.sleep(5)

            retry_threads = []
            for i in range(parallel):

                def retry_worker(wid):
                    wqueue = Queue(db_path)
                    while not stop_event.is_set():
                        job = wqueue.claim_job(wid)
                        if job is None:
                            if not wqueue.has_work():
                                break
                            time.sleep(1)
                            continue
                        file_id, file_url, rel_path = job
                        display.set_worker_status(wid, rel_path)
                        dest_path = dest / rel_path
                        dest_path.parent.mkdir(parents=True, exist_ok=True)
                        from .worker import download_file
                        success, error = download_file(file_url, dest_path, proxy, args.timeout)
                        if success:
                            size = dest_path.stat().st_size if dest_path.exists() else 0
                            wqueue.mark_done(file_id, size)
                            display.add_bytes(size)
                            display.set_worker_status(wid, "idle")
                        else:
                            wqueue.mark_failed(file_id, error)
                            display.set_worker_status(wid, f"RETRY FAILED: {rel_path}")
                    display.set_worker_status(wid, "done")
                    wqueue.close()

                t = threading.Thread(target=retry_worker, args=(i,), daemon=True)
                t.start()
                retry_threads.append(t)

            try:
                while any(t.is_alive() for t in retry_threads):
                    display.render()
                    time.sleep(1)
            except KeyboardInterrupt:
                stop_event.set()
                break

            stats = queue.stats()
            if stats["failed"] == 0:
                break
            print(f"\n[!] Still {stats['failed']} failed. Retrying in 10s...")
            time.sleep(10)

    display.render_final()
    queue.close()


if __name__ == "__main__":
    main()
