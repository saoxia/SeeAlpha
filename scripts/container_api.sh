#!/bin/sh
set -eu
# This image is only for fresh, private single-node SQLite deployments.
# Existing databases are never upgraded, overwritten or adopted at startup.
state="/app/.local/state/api"
db="$state/seealpha.sqlite"
mkdir -p "$state"
if [ ! -e "$db" ]; then
  /app/platform/.venv/bin/alembic upgrade head
fi
exec /app/platform/.venv/bin/uvicorn api.main:app --host 0.0.0.0 --port 8000
