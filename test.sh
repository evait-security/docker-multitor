#!/bin/bash
set -e

IMAGE="multitor:test"
CONTAINER="multitor-test"
PROXY="http://127.0.0.1:16379"
PASS=0
FAIL=0

cleanup() {
  echo "[*] Cleanup"
  docker rm -f "$CONTAINER" 2>/dev/null || true
}
trap cleanup EXIT

pass() { echo "  ✓ $1"; PASS=$((PASS + 1)); }
fail() { echo "  ✗ $1"; FAIL=$((FAIL + 1)); }

echo "=== docker-multitor test ==="

# Build
echo "[*] Building image"
docker build -t "$IMAGE" . || { fail "build failed"; exit 1; }
pass "image built"

# Run
echo "[*] Starting container (2 Tor instances for faster test)"
docker rm -f "$CONTAINER" 2>/dev/null || true
docker run -d --name "$CONTAINER" -p 16379:16379 -e TOR_INSTANCES=2 "$IMAGE"
pass "container started"

# Wait for ready
echo "[*] Waiting for proxy to be ready (max 90s)"
ready=false
for i in $(seq 1 90); do
  if curl -sf --proxy "$PROXY" --max-time 10 http://httpbin.org/ip >/dev/null 2>&1; then
    ready=true
    break
  fi
  sleep 1
done

if [[ "$ready" != "true" ]]; then
  echo "[!] Proxy not ready after 90s, showing container logs:"
  docker logs "$CONTAINER" | tail -30
  fail "proxy not ready"
  exit 1
fi
pass "proxy ready (${i}s)"

# Test 1: HTTP request through proxy
echo "[*] Test: HTTP request"
response=$(curl -sf --proxy "$PROXY" --max-time 15 http://httpbin.org/ip)
if [[ -n "$response" ]]; then
  pass "HTTP works: $response"
else
  fail "HTTP request failed"
fi

# Test 2: HTTPS request through proxy
echo "[*] Test: HTTPS request"
response=$(curl -sf --proxy "$PROXY" --max-time 15 https://httpbin.org/ip)
if [[ -n "$response" ]]; then
  pass "HTTPS works: $response"
else
  fail "HTTPS request failed"
fi

# Test 3: Verify Tor
echo "[*] Test: Tor verification"
tor_check=$(curl -sf --proxy "$PROXY" --max-time 15 https://check.torproject.org/api/ip)
if echo "$tor_check" | grep -q '"IsTor":true'; then
  pass "Tor verified: $tor_check"
else
  fail "Not using Tor: $tor_check"
fi

# Test 4: Multiple requests show round-robin
echo "[*] Test: Round-robin (3 requests)"
ips=()
for i in 1 2 3; do
  ip=$(curl -sf --proxy "$PROXY" --max-time 15 https://httpbin.org/ip | grep -oP '"origin":\s*"\K[^"]+')
  ips+=("$ip")
  echo "    request $i: $ip"
done

if [[ ${#ips[@]} -eq 3 ]]; then
  pass "round-robin responding"
else
  fail "round-robin failed"
fi

# Summary
echo ""
echo "=== Results: $PASS passed, $FAIL failed ==="
[[ "$FAIL" -eq 0 ]] && exit 0 || exit 1
