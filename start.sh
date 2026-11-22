#!/usr/bin/env bash
set -Eeuo pipefail

cd /app

log() {
  printf '[pharmacy-manager] %s\n' "$*"
}

log "Running Django deployment checks..."
python manage.py check --deploy

log "Applying committed database migrations..."
python manage.py migrate --noinput

# Customer Registry has a production repair command for legacy databases where
# tables/columns may be missing even though the migration history is present.
log "Verifying customer registry schema..."
python manage.py ensure_customer_registry

# Static files are normally collected during the Docker build. Running this once
# at boot is harmless and protects deployments that mount/override static storage.
log "Ensuring static files are collected..."
python manage.py collectstatic --noinput

# The normal web service can optionally embed Celery Worker + Beat. For a
# separate Railway worker service, set EMBEDDED_CELERY_WORKER=0 on the web service.
CELERY_PID=""
if [[ "${EMBEDDED_CELERY_WORKER:-1}" != "0" && -n "${REDIS_URL:-}" ]]; then
  log "Starting embedded Celery worker + beat..."
  celery -A pharmacy_system worker \
    --beat \
    --loglevel="${CELERY_LOG_LEVEL:-INFO}" \
    --concurrency="${CELERY_WORKER_CONCURRENCY:-2}" &
  CELERY_PID=$!
else
  log "Embedded Celery worker disabled or REDIS_URL is not configured."
fi

cleanup() {
  if [[ -n "${CELERY_PID}" ]] && kill -0 "${CELERY_PID}" 2>/dev/null; then
    log "Stopping embedded Celery worker..."
    kill "${CELERY_PID}" 2>/dev/null || true
    wait "${CELERY_PID}" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

PORT="${PORT:-8000}"
WEB_CONCURRENCY="${WEB_CONCURRENCY:-3}"
GUNICORN_THREADS="${GUNICORN_THREADS:-4}"
GUNICORN_TIMEOUT="${GUNICORN_TIMEOUT:-120}"
GUNICORN_ACCESS_LOG="${GUNICORN_ACCESS_LOG:-0}"

GUNICORN_ARGS=(
  "pharmacy_system.wsgi:application"
  "--bind=0.0.0.0:${PORT}"
  "--workers=${WEB_CONCURRENCY}"
  "--threads=${GUNICORN_THREADS}"
  "--timeout=${GUNICORN_TIMEOUT}"
  "--graceful-timeout=30"
  "--max-requests=1500"
  "--max-requests-jitter=150"
  "--error-logfile=-"
  "--log-level=${LOG_LEVEL:-info}"
)

# Gunicorn treats an empty --access-logfile value as a filename ("") and
# crashes with: "'' isn't writable". When access logging is disabled, omit
# the option entirely. When enabled, send it explicitly to stdout.
if [[ "${GUNICORN_ACCESS_LOG}" == "1" ]]; then
  GUNICORN_ARGS+=("--access-logfile=-")
fi

log "Starting Gunicorn on 0.0.0.0:${PORT} (${WEB_CONCURRENCY} workers x ${GUNICORN_THREADS} threads)..."
exec gunicorn "${GUNICORN_ARGS[@]}"
