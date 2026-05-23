"""Spider - crawls directory listings and feeds URLs into the queue."""

import logging
import re
import subprocess
import time
from html.parser import HTMLParser
from urllib.parse import urljoin, unquote

from .queue import Queue

log = logging.getLogger(__name__)


class LinkExtractor(HTMLParser):
    """Extract href links from HTML directory listings."""

    def __init__(self):
        super().__init__()
        self.links: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            for name, value in attrs:
                if name == "href" and value:
                    self.links.append(value)


def fetch_page(url: str, proxy: str, timeout: int = 120) -> str | None:
    """Fetch a page via wget through the proxy. Returns HTML content or None."""
    try:
        result = subprocess.run(
            [
                "wget",
                "--quiet",
                "--output-document=-",
                "--proxy=on",
                "-e", "use_proxy=yes",
                "-e", f"http_proxy={proxy}",
                "-e", f"https_proxy={proxy}",
                "-e", "robots=off",
                f"--timeout={timeout}",
                "--tries=3",
                "--waitretry=5",
                "--retry-connrefused",
                "--no-check-certificate",
                "--user-agent=Mozilla/5.0 (Windows NT 10.0; rv:128.0) Gecko/20100101 Firefox/128.0",
                url,
            ],
            capture_output=True,
            timeout=timeout + 30,
        )
        if result.returncode == 0:
            # Try UTF-8 first, fall back to latin-1 (never fails)
            try:
                return result.stdout.decode("utf-8")
            except UnicodeDecodeError:
                return result.stdout.decode("latin-1")
        log.warning(f"wget failed for {url}: exit {result.returncode}")
        return None
    except subprocess.TimeoutExpired:
        log.warning(f"Timeout fetching {url}")
        return None
    except Exception as e:
        log.warning(f"Error fetching {url}: {e}")
        return None


def is_directory_link(href: str) -> bool:
    """Check if a link points to a subdirectory."""
    return href.endswith("/")


def is_file_link(href: str) -> bool:
    """Check if a link points to a file (not a directory, not parent, not absolute)."""
    if not href or href.startswith("#") or href.startswith("?"):
        return False
    if href.startswith("http://") or href.startswith("https://"):
        return False
    if href in ("../", "./", "/"):
        return False
    if href.startswith("/"):
        return False
    return not href.endswith("/")


def extract_links(html: str, base_url: str) -> tuple[list[str], list[str]]:
    """Extract file URLs and directory URLs from HTML.
    
    Returns (file_urls, directory_urls).
    """
    parser = LinkExtractor()
    parser.feed(html)

    files = []
    dirs = []

    for href in parser.links:
        # Skip parent/self/sorting/query links
        if href in ("../", "./", "/", ""):
            continue
        if href.startswith("?") or href.startswith("#"):
            continue

        full_url = urljoin(base_url, href)

        # Only follow links under base_url
        if not full_url.startswith(base_url.split("?")[0].rsplit("/", 1)[0] + "/"):
            continue

        if is_directory_link(href):
            dirs.append(full_url)
        elif is_file_link(href):
            files.append(full_url)

    return files, dirs


def spider(queue: Queue, base_url: str, proxy: str, timeout: int = 120):
    """Crawl directory listings starting from base_url, adding files to queue.
    
    Runs continuously until all directories are explored.
    Retries on failure indefinitely.
    """
    # Ensure base URL ends with /
    if not base_url.endswith("/"):
        base_url += "/"

    visited: set[str] = set()
    to_visit: list[str] = [base_url]
    total_added = 0

    while to_visit:
        url = to_visit.pop(0)

        if url in visited:
            continue

        log.info(f"Crawling: {url}")
        html = None

        # Retry fetching this directory page indefinitely
        attempt = 0
        while html is None:
            attempt += 1
            html = fetch_page(url, proxy, timeout)
            if html is None:
                wait = min(10 * attempt, 60)
                log.warning(f"Failed to fetch {url} (attempt {attempt}), retrying in {wait}s...")
                time.sleep(wait)

        visited.add(url)

        # Parse links
        files, dirs = extract_links(html, url)

        # Add files to queue
        for file_url in files:
            rel_path = file_url[len(base_url):]
            rel_path = unquote(rel_path)
            if queue.add_url(file_url, rel_path):
                total_added += 1

        # Add subdirectories to crawl
        for dir_url in dirs:
            if dir_url not in visited:
                to_visit.append(dir_url)

        log.info(f"  Found {len(files)} file(s), {len(dirs)} subdir(s). Total queued: {total_added}")

    log.info(f"Spider complete. {total_added} new file(s) added to queue. {len(visited)} directories crawled.")
    return total_added
