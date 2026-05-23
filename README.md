# docker-multitor

Dockerized [multitor](https://github.com/trimstray/multitor). Runs multiple Tor circuits behind HAProxy for load-balanced anonymous proxying.

Alpine 3.21 / Tor / Privoxy / HAProxy

## usage

```bash
docker build -t multitor .
docker run --rm -p 16379:16379 multitor
```

```bash
curl --proxy http://localhost:16379 https://httpbin.org/ip
```

More Tor instances:

```bash
docker run --rm -p 16379:16379 -e TOR_INSTANCES=10 multitor
```

Or pull from registry:

```bash
docker run --rm -p 16379:16379 evait/multitor
```

## env

| var | default | what it does |
|-----|---------|--------------|
| `TOR_INSTANCES` | `5` | number of Tor circuits to spawn |

## mirror

Robust parallel Tor mirror tool for downloading directory-listed URLs from `.onion` sites. Built for bulk-downloading leaked datasets for DSGVO analysis (informing affected users about stolen data).

**Architecture:** Python + SQLite job queue + parallel wget workers. Spider crawls directory listings, workers download files concurrently through multiple Tor circuits.

- SQLite database tracks all files — crash-safe, resumable
- Parallel download workers (one per Tor circuit)
- Real-time terminal UI showing progress, speed, and worker status
- Automatic infinite retry on network failure
- Resume support — re-run the same command to pick up where you left off

### install

Requires [uv](https://docs.astral.sh/uv/) and `wget`.

```bash
# Install as global tool
uv tool install ./mirror

# Or run directly from repo
uv run --directory mirror mirror --help

# or install it from the online repo
uv tool install git+https://github.com/evait-security/docker-multitor/tree/master/mirror
```

### usage

```bash
# Start multitor proxy
docker run --rm -d -p 16379:16379 -e TOR_INSTANCES=5 evait/multitor

# Mirror a .onion directory listing
mirror http://exampleonion.onion/files/

# Mirror to a specific destination with 10 workers
mirror --parallel 10 http://exampleonion.onion/dump/ /data/leak-mirror

# Custom proxy address
mirror --proxy http://10.0.0.5:16379 http://exampleonion.onion/files/

# Resume an interrupted download (just re-run the same command)
mirror http://exampleonion.onion/dump/ /data/leak-mirror

# Start fresh, ignoring previous state
mirror --fresh http://exampleonion.onion/dump/ /data/leak-mirror
```

### options

| flag | default | description |
|------|---------|-------------|
| `url` | — | URL to mirror (directory listing with links) |
| `destination` | `.` | Local directory to save files |
| `-p, --parallel` | `5` | Number of parallel download workers |
| `--proxy` | `http://127.0.0.1:16379` | Multitor proxy address |
| `--timeout` | `120` | Download timeout per file (seconds) |
| `--fresh` | — | Delete existing database, start over |
| `-v, --verbose` | — | Write detailed logs to `mirror.log` |

### how it works

1. **Spider** crawls directory listings recursively via the Tor proxy, discovers all file URLs, stores them in a SQLite queue (`.mirror.db` in the destination)
2. **Workers** (N parallel threads) pull jobs from the queue and download files using `wget` through the HAProxy round-robin — each request exits through a different Tor circuit
3. **Retry loop** — failed downloads are requeued and retried indefinitely until all files are saved
4. **Resume** — the database persists in the destination dir; re-running detects it and picks up where it left off

## test

```bash
./test.sh
```

Builds the image, spins up a container, waits for Tor bootstrap, runs HTTP/HTTPS requests through the proxy, verifies Tor exit and round-robin. Cleans up after itself.

## publish

```bash
./publish.sh 1.0.0
```

Tags and pushes to Docker Hub as `evait/multitor:1.0.0` and `evait/multitor:latest`.

## how it works

multitor spawns N Tor processes with separate SOCKS ports, puts a Privoxy HTTP proxy in front of each, then HAProxy round-robins across all Privoxy backends on port 16379. Each request potentially exits through a different Tor circuit.

## credits

- [multitor](https://github.com/trimstray/multitor) by trimstray
- [evait-security](https://github.com/evait-security) for the Docker packaging
