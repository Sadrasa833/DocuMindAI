#!/bin/sh
set -eu

if [ "${RUN_MIGRATIONS:-false}" = "true" ]; then
  echo "Applying database migrations..."
  attempt=1
  until alembic upgrade head; do
    if [ "$attempt" -ge 12 ]; then
      echo "Database migrations failed after $attempt attempts." >&2
      exit 1
    fi
    attempt=$((attempt + 1))
    echo "Migration attempt failed. Retrying in 3 seconds..."
    sleep 3
  done
fi

exec "$@"
