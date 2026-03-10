#!/usr/bin/env bash
set -euo pipefail

# Start services via docker compose and optionally run schema initialization
# Usage: ./scripts/start.sh
# Requires: docker and docker-compose (or Docker Compose v2 as 'docker compose')

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

# Find repository root by walking up until we find docker-compose.yml (or stop at filesystem root)
ROOT_DIR=$SCRIPT_DIR
while [ "$ROOT_DIR" != "/" ] && [ ! -f "$ROOT_DIR/docker-compose.yml" ]; do
  ROOT_DIR=$(dirname "$ROOT_DIR")
done
ENV_FILE="$ROOT_DIR/.env"

# Load .env if present
if [ -f "$ENV_FILE" ]; then
  # shellcheck disable=SC1090
  set -o allexport
  # shellcheck disable=SC1091
  source "$ENV_FILE"
  set +o allexport
else
  echo "Warning: .env not found at $ENV_FILE. Using current environment variables."
fi

POSTGRES_CONTAINER_NAME=${POSTGRES_CONTAINER_NAME:-sttt-postgres}
POSTGRES_USER=${POSTGRES_USER:-postgres}
POSTGRES_DB=${POSTGRES_DB:-appdb}
RUN_DB_INIT=${RUN_DB_INIT:-false}

echo "Starting docker-compose services..."
docker compose up -d --remove-orphans

if [ "${RUN_DB_INIT,,}" = "true" ] || [ "${RUN_DB_INIT,,}" = "1" ] || [ "${RUN_DB_INIT,,}" = "yes" ]; then
  echo "RUN_DB_INIT is true — initializing database schema (this will DROP and recreate schema 'sttt')."

  echo "Waiting for Postgres container ($POSTGRES_CONTAINER_NAME) to be ready..."
  ATTEMPTS=0
  MAX_ATTEMPTS=30
  until docker exec "$POSTGRES_CONTAINER_NAME" pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB" >/dev/null 2>&1; do
    ATTEMPTS=$((ATTEMPTS + 1))
    if [ "$ATTEMPTS" -ge "$MAX_ATTEMPTS" ]; then
      echo "Postgres did not become ready after $MAX_ATTEMPTS attempts. Aborting."
      exit 1
    fi
    sleep 2
  done

  echo "Postgres is ready. Executing schema file..."
  # try a few candidate schema paths (handle being invoked from different subfolders)
  CANDIDATES=(
    "$ROOT_DIR/data_service/schema.sql"
    "$ROOT_DIR/schema.sql"
    "$SCRIPT_DIR/../data_service/schema.sql"
  )
  SCHEMA_FILE=""
  for c in "${CANDIDATES[@]}"; do
    if [ -f "$c" ]; then
      SCHEMA_FILE="$c"
      break
    fi
  done

  if [ -z "$SCHEMA_FILE" ]; then
    echo "Schema file not found in any candidate path. Tried:"
    for c in "${CANDIDATES[@]}"; do echo "  - $c"; done
    exit 1
  fi

  # Pipe the local schema.sql into psql inside the container
  cat "$SCHEMA_FILE" | docker exec -i "$POSTGRES_CONTAINER_NAME" psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1
  echo "Schema applied successfully."
else
  echo "RUN_DB_INIT is not enabled — skipping schema initialization."
fi

echo "All done."
