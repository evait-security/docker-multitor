"""CLI entry point for mirror tool."""

import argparse
import logging
import os
import sys
import threading
import time
from pathlib import Path

from .queue import MirrorQueue
from .spider import spider
from .worker import download_file
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
    parser.add_argument("--rescan", action="store_true", help="Re-run spider even when resuming (find new files on server)")
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
    if db_path.exists() and not args.fresh:
        # Peek at DB to show resume info
        import sqlite3
        try:
            conn = sqlite3.connect(str(db_path), timeout=5)
            cur = conn.execute("SELECT status, COUNT(*) FROM files GROUP BY status")
            counts = dict(cur.fetchall())
            conn.close()
            if sum(counts.values()) > 0:
                print(f"\033[1m[*] Resuming previous mirror session\033[0m")
                print(f"    Database: {db_path}")
                print(f"    Previous: {counts.get('done', 0)} done, "
                      f"{counts.get('pending', 0)} pending, "
                      f"{counts.get('failed', 0)} failed")
                print()
        except Exception:
            pass

    # Init queue (single writer thread handles all SQLite)
    queue = MirrorQueue(db_path, fresh=args.fresh)

    print(f"[*] Mirror configuration:")
    print(f"    URL:         {url}")
    print(f"    Destination: {dest}")
    print(f"    Proxy:       {proxy}")
    print(f"    Workers:     {parallel}")
    print(f"    Log:         {log_path}")
    print()

    display = Display(queue, parallel)
    display.start_time = time.time()

    # --- Spider thread (skip on resume unless --rescan) ---
    spider_done = threading.Event()
    has_pending = queue.has_pending_jobs()
    run_spider_flag = args.rescan or args.fresh or not has_pending

    if run_spider_flag:
        def run_spider():
            try:
                spider(queue, url, proxy, args.timeout,
                       status_callback=lambda msg: setattr(display, 'spider_status', msg))
            except Exception as e:
                log.error(f"Spider error: {e}")
            finally:
                display.spider_status = "done ✓"
                spider_done.set()

        spider_thread = threading.Thread(target=run_spider, daemon=True, name="spider")
        spider_thread.start()
        time.sleep(2)
    else:
        spider_thread = None
        spider_done.set()
        display.spider_status = "skipped (resuming, use --rescan to re-crawl)"
        log.info("Spider skipped — resuming with existing queue")

    # --- Worker threads ---
    stop_event = threading.Event()
    worker_threads = []

    def run_worker(wid: int):
        idle_count = 0
        while not stop_event.is_set():
            job = queue.get_job(timeout=1.0)
            if job is None:
                idle_count += 1
                if spider_done.is_set() and not queue.has_pending_jobs():
                    break
                if idle_count > 30 and spider_done.is_set():
                    break
                continue

            idle_count = 0
            file_id, file_url, rel_path = job
            display.set_worker_status(wid, rel_path)
            log.info(f"W{wid} downloading: {rel_path}")

            dest_path = dest / rel_path
            dest_path.parent.mkdir(parents=True, exist_ok=True)

            try:
                success, error = download_file(file_url, dest_path, proxy, args.timeout)

                if success:
                    size = dest_path.stat().st_size if dest_path.exists() else 0
                    queue.complete(file_id, size)
                    display.add_bytes(size)
                    display.set_worker_status(wid, "idle")
                    log.info(f"W{wid} done: {rel_path} ({size} bytes)")
                else:
                    queue.fail(file_id, error)
                    display.set_worker_status(wid, f"FAILED: {rel_path}")
                    log.warning(f"W{wid} failed: {rel_path} - {error}")
            except Exception as e:
                log.error(f"W{wid} exception on {rel_path}: {e}")
                queue.fail(file_id, str(e))
                display.set_worker_status(wid, f"ERROR: {rel_path}")

        display.set_worker_status(wid, "done")

    for i in range(parallel):
        t = threading.Thread(target=run_worker, args=(i,), daemon=True, name=f"worker-{i}")
        t.start()
        worker_threads.append(t)

    # --- Monitor loop ---
    try:
        def still_working():
            if spider_thread and spider_thread.is_alive():
                return True
            return any(t.is_alive() for t in worker_threads)

        while still_working():
            display.render()
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n\n[!] Interrupted. Stopping...")
        stop_event.set()
        if spider_thread:
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

    # --- Retry failed files ---
    stats = queue.stats()
    if stats["failed"] > 0:
        log.info(f"{stats['failed']} file(s) failed. Starting retry cycle...")
        print(f"\n[!] {stats['failed']} file(s) failed. Retrying...")

        while stats["failed"] > 0:
            queue.requeue_failed()
            time.sleep(3)  # Give writer thread time to process

            retry_threads = []
            for i in range(parallel):
                def retry_worker(wid):
                    while not stop_event.is_set():
                        job = queue.get_job(timeout=1.0)
                        if job is None:
                            if not queue.has_pending_jobs():
                                break
                            continue
                        file_id, file_url, rel_path = job
                        display.set_worker_status(wid, rel_path)
                        dest_path = dest / rel_path
                        dest_path.parent.mkdir(parents=True, exist_ok=True)
                        try:
                            success, error = download_file(file_url, dest_path, proxy, args.timeout)
                            if success:
                                size = dest_path.stat().st_size if dest_path.exists() else 0
                                queue.complete(file_id, size)
                                display.add_bytes(size)
                                display.set_worker_status(wid, "idle")
                            else:
                                queue.fail(file_id, error)
                                display.set_worker_status(wid, f"RETRY FAILED: {rel_path}")
                        except Exception as e:
                            queue.fail(file_id, str(e))
                    display.set_worker_status(wid, "done")

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
