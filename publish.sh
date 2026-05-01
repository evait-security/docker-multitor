#!/bin/bash
set -e

REPO="evait/multitor"
VERSION="${1:-latest}"

if [[ "$VERSION" == "latest" ]]; then
  echo "usage: ./publish.sh <version>"
  echo "example: ./publish.sh 1.0.0"
  exit 1
fi

echo "building ${REPO}:${VERSION}"
docker build -t "${REPO}:${VERSION}" -t "${REPO}:latest" .

echo "pushing ${REPO}:${VERSION}"
docker push "${REPO}:${VERSION}"

echo "pushing ${REPO}:latest"
docker push "${REPO}:latest"

echo "done"
