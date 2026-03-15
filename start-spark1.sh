#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
echo "Starting MemoryWeb on Spark1..."
echo "  Dashboard: http://localhost:8100"
docker compose -f docker-compose.yml -f docker-compose.spark1.yml up -d "$@"
docker compose -f docker-compose.yml -f docker-compose.spark1.yml ps
