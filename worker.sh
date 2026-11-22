#!/usr/bin/env bash
set -Eeuo pipefail

cd /app

log() {
  printf '[pharmacy-manager-worker] %s\n' "$*"
}

if [[ -z "${REDIS_URL:-}" ]]; then
  log "ERROR: REDIS_URL is required for the dedicated Celery worker."
  exit 1
fi

log "Running Django deployment checks..."
python manage.py check --deploy

log "Starting Celery worker..."
exec celery -A pharmacy_system worker \
  --loglevel="${CELERY_LOG_LEVEL:-INFO}" \
  --concurrency="${CELERY_WORKER_CONCURRENCY:-2}"
