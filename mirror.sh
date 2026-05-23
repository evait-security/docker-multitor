#!/bin/bash
# mirror.sh - Robust Tor mirror/download tool using docker-multitor proxy
# Downloads/mirrors directory-listed URLs over Tor with infinite retry on failure.
#
# Usage: ./mirror.sh <url> [destination]
#   url         - The .onion or clearnet URL to mirror (directory listing expected)
#   destination - Local directory to save files (default: current directory)

set -euo pipefail

PROXY="${MULTITOR_PROXY:-http://127.0.0.1:16379}"
WAIT_RETRY=10
RETRY_CONNREFUSED=true
MAX_REDIRECT=10
TIMEOUT=120
DNS_TIMEOUT=120
READ_TIMEOUT=120
USER_AGENT="Mozilla/5.0 (Windows NT 10.0; rv:128.0) Gecko/20100101 Firefox/128.0"

usage() {
  echo "Usage: $0 <url> [destination]"
  echo ""
  echo "  url         URL to mirror (supports .onion and clearnet via Tor)"
  echo "  destination Local directory to save to (default: current directory)"
  echo ""
  echo "Environment variables:"
  echo "  MULTITOR_PROXY  Proxy address (default: http://127.0.0.1:16379)"
  echo ""
  echo "Examples:"
  echo "  $0 http://example.onion/leaks/"
  echo "  $0 http://example.onion/leaks/ /data/mirror"
  exit 1
}

# --- Argument parsing ---
if [[ $# -lt 1 ]]; then
  usage
fi

URL="$1"
DEST="${2:-.}"

# Ensure destination exists
mkdir -p "$DEST"

# Resolve to absolute path
DEST="$(cd "$DEST" && pwd)"

# --- Dependency check ---
if ! command -v wget &>/dev/null; then
  echo "[!] wget is required but not installed."
  echo "    Install with: sudo apt install wget"
  exit 1
fi

# --- Proxy connectivity check ---
echo "[*] Mirror configuration:"
echo "    URL:         $URL"
echo "    Destination: $DEST"
echo "    Proxy:       $PROXY"
echo ""

check_proxy() {
  local attempt=0
  while true; do
    attempt=$((attempt + 1))
    if wget -q --spider --proxy=on -e "use_proxy=yes" \
         -e "http_proxy=$PROXY" -e "https_proxy=$PROXY" \
         --timeout=30 "$URL" 2>/dev/null; then
      return 0
    fi
    echo "[!] Proxy or target not reachable (attempt $attempt). Retrying in ${WAIT_RETRY}s..."
    sleep "$WAIT_RETRY"
  done
}

echo "[*] Checking proxy and target connectivity..."
check_proxy
echo "[+] Connection established."
echo ""

# --- Mirror function with infinite retry ---
mirror() {
  local exit_code=0

  while true; do
    echo "[*] Starting/resuming mirror at $(date '+%Y-%m-%d %H:%M:%S')..."

    # wget options explained:
    #   -r                  recursive download
    #   -N                  timestamping (only download newer files)
    #   -l inf              infinite recursion depth
    #   --no-parent         don't ascend above the given URL path
    #   --continue          resume partial downloads
    #   -R "index.html*"   reject directory listing HTML files
    #   --content-disposition  use server-suggested filenames
    #   --proxy=on          use proxy
    #   --timeout           network timeout
    #   --waitretry         wait between retries
    #   --tries=0           infinite retries per file
    #   --retry-connrefused retry on connection refused
    #   --random-wait       randomize wait to avoid detection
    #   --wait=1            base wait between requests
    #   -e robots=off       ignore robots.txt (necessary for .onion sites)
    #   --no-check-certificate  skip TLS verification (Tor exit may MITM clearnet)
    set +e
    wget \
      --recursive \
      --level=inf \
      --timestamping \
      --no-parent \
      --continue \
      --reject "index.html*" \
      --content-disposition \
      --proxy=on \
      -e "use_proxy=yes" \
      -e "http_proxy=$PROXY" \
      -e "https_proxy=$PROXY" \
      -e "robots=off" \
      --timeout="$TIMEOUT" \
      --dns-timeout="$DNS_TIMEOUT" \
      --read-timeout="$READ_TIMEOUT" \
      --waitretry="$WAIT_RETRY" \
      --tries=0 \
      --retry-connrefused \
      --retry-on-http-error=500,502,503,504 \
      --random-wait \
      --wait=1 \
      --max-redirect="$MAX_REDIRECT" \
      --no-check-certificate \
      --user-agent="$USER_AGENT" \
      --directory-prefix="$DEST" \
      --no-verbose \
      --show-progress \
      "$URL"
    exit_code=$?
    set -e

    if [[ $exit_code -eq 0 ]]; then
      echo ""
      echo "[+] Mirror completed successfully at $(date '+%Y-%m-%d %H:%M:%S')."
      echo "[+] Files saved to: $DEST"
      return 0
    fi

    # wget exit codes:
    #   1 = generic error
    #   2 = parse error
    #   3 = file I/O error
    #   4 = network failure
    #   5 = SSL failure
    #   6 = auth failure
    #   7 = protocol error
    #   8 = server error
    case $exit_code in
      2|3|6)
        echo "[!] Fatal error (exit code $exit_code). Cannot recover by retrying."
        return $exit_code
        ;;
      *)
        echo ""
        echo "[!] wget exited with code $exit_code at $(date '+%Y-%m-%d %H:%M:%S')."
        echo "[*] Waiting ${WAIT_RETRY}s before resuming..."
        sleep "$WAIT_RETRY"
        echo "[*] Checking connectivity before retry..."
        check_proxy
        ;;
    esac
  done
}

# --- Run ---
echo "[*] Beginning mirror of: $URL"
echo "[*] Press Ctrl+C to abort."
echo ""
mirror
