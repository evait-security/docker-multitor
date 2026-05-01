#!/bin/bash
set -e

TOR_INSTANCES=${TOR_INSTANCES:-5}
[[ "$TOR_INSTANCES" =~ ^[0-9]+$ ]] || TOR_INSTANCES=5

echo "[*] Starting multitor with $TOR_INSTANCES instances"

multitor --init "$TOR_INSTANCES" --user root --socks-port 9000 --control-port 9900 \
  --proxy privoxy --haproxy --verbose

# Keep container alive while haproxy runs
while pidof haproxy > /dev/null; do
  sleep 5
done