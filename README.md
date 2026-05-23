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

Mirror/download directory-listed URLs over the Tor network with automatic retry on failure. Designed for robust bulk downloads from `.onion` sites (e.g. analyzing leaked data for DSGVO notification obligations).

```bash
./mirror.sh <url> [destination]
```

**Parameters:**

| parameter | required | description |
|-----------|----------|-------------|
| `url` | yes | URL to mirror (`.onion` or clearnet via Tor) |
| `destination` | no | Local directory to save files (default: current directory) |

**Examples:**

```bash
# Mirror a .onion directory listing to current directory
./mirror.sh http://exampleonion.onion/files/

# Mirror to a specific destination
./mirror.sh http://exampleonion.onion/dump/ /data/leak-mirror

# Use a custom proxy address
MULTITOR_PROXY=http://10.0.0.5:16379 ./mirror.sh http://exampleonion.onion/files/ /data/output
```

**Features:**

- Uses the docker-multitor HAProxy endpoint as HTTP proxy (default `http://127.0.0.1:16379`)
- Recursive mirror with `wget --mirror` preserving directory structure
- Infinite retry on network failure or connection loss — resumes where it left off
- Respects timestamps to avoid re-downloading unchanged files
- Random wait between requests to reduce load / avoid detection
- Configurable via `MULTITOR_PROXY` environment variable

**Requirements:**

- `wget` installed on the host
- docker-multitor container running and reachable

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
