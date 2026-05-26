#!/usr/bin/env bash
set -Eeuo pipefail

APP_USER="${APP_USER:-app}"
APP_GROUP="${APP_GROUP:-app}"
DATA_DIR="${DATA_DIR:-/app/data}"
RUNS_DIR="${RUNS_DIR:-${DATA_DIR}/runs}"
WORKSPACES_DIR="${WORKSPACES_DIR:-/workspaces}"

ensure_dir() {
  local dir="$1"
  mkdir -p "${dir}"
}

try_chown() {
  local target="$1"
  chown "${APP_USER}:${APP_GROUP}" "${target}" 2>/dev/null || true
}

try_chown_recursive() {
  local target="$1"
  chown -R "${APP_USER}:${APP_GROUP}" "${target}" 2>/dev/null || true
}

if [[ "${1:-}" == -* ]]; then
  set -- node server/index.js "$@"
fi

ensure_dir "${DATA_DIR}"
ensure_dir "${RUNS_DIR}"
ensure_dir "${WORKSPACES_DIR}"

if [[ "${STORAGE_DRIVER:-file}" == "postgres" && "${RUN_DB_MIGRATIONS:-1}" != "0" ]]; then
  if [[ -z "${DATABASE_URL:-}" ]]; then
    echo "ERROR: DATABASE_URL is required when STORAGE_DRIVER=postgres" >&2
    exit 1
  fi
  node server/db/migrate.js
fi

if [[ "$(id -u)" == "0" ]]; then
  try_chown_recursive "${DATA_DIR}"
  try_chown "${WORKSPACES_DIR}"
  exec su-exec "${APP_USER}:${APP_GROUP}" "$@"
fi

exec "$@"
