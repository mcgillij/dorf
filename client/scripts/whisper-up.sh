#!/usr/bin/env bash
# Adopt the old standalone container once, then let Compose manage updates.
set -euo pipefail
cd "$(dirname "$0")/.."
docker compose config --quiet
if docker container inspect derf-whisper >/dev/null 2>&1; then
  service=$(docker inspect --format '{{ index .Config.Labels "com.docker.compose.service" }}' derf-whisper)
  if [[ -z "$service" || "$service" == "<no value>" ]]; then
    docker rm -f derf-whisper
  fi
fi
docker compose up -d whisper
