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
