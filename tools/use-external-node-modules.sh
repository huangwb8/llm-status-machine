#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

EXTERNAL_ROOT="${EXTERNAL_ROOT:-/Volumes/2T01/Test/llm-status-machine}"
EXTERNAL_NODE_MODULES="${EXTERNAL_NODE_MODULES:-${EXTERNAL_ROOT}/node_modules}"
EXTERNAL_NPM_CACHE="${EXTERNAL_NPM_CACHE:-${EXTERNAL_ROOT}/.npm-cache}"
LOCAL_NODE_MODULES="${REPO_ROOT}/node_modules"
MIGRATE="${MIGRATE:-0}"

fail() {
  echo "ERROR: $*" >&2
  exit 1
}

info() {
  echo "==> $*"
}

is_empty_dir() {
  [[ -d "$1" ]] || return 1
  [[ -z "$(find "$1" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null)" ]]
}

mkdir -p "${EXTERNAL_ROOT}" "${EXTERNAL_NPM_CACHE}"

if [[ -L "${LOCAL_NODE_MODULES}" ]]; then
  current_target="$(readlink "${LOCAL_NODE_MODULES}")"
  if [[ "${current_target}" != "${EXTERNAL_NODE_MODULES}" ]]; then
    fail "node_modules already points to ${current_target}; remove it or set EXTERNAL_NODE_MODULES=${current_target}"
  fi
  mkdir -p "${EXTERNAL_NODE_MODULES}"
  info "node_modules already links to ${EXTERNAL_NODE_MODULES}"
  info "npm cache directory: ${EXTERNAL_NPM_CACHE}"
  exit 0
fi

if [[ -e "${LOCAL_NODE_MODULES}" ]]; then
  if [[ ! -d "${LOCAL_NODE_MODULES}" ]]; then
    fail "node_modules exists but is not a directory or symlink: ${LOCAL_NODE_MODULES}"
  fi

  if [[ "${MIGRATE}" != "1" ]]; then
    fail "node_modules is a local directory. Move it manually or rerun with MIGRATE=1 to move it to ${EXTERNAL_NODE_MODULES}."
  fi

  if [[ -e "${EXTERNAL_NODE_MODULES}" ]] && ! is_empty_dir "${EXTERNAL_NODE_MODULES}"; then
    fail "target node_modules is not empty: ${EXTERNAL_NODE_MODULES}"
  fi

  rm -rf "${EXTERNAL_NODE_MODULES}"
  mv "${LOCAL_NODE_MODULES}" "${EXTERNAL_NODE_MODULES}"
  info "migrated local node_modules to ${EXTERNAL_NODE_MODULES}"
else
  mkdir -p "${EXTERNAL_NODE_MODULES}"
fi

ln -s "${EXTERNAL_NODE_MODULES}" "${LOCAL_NODE_MODULES}"

info "created node_modules -> ${EXTERNAL_NODE_MODULES}"
info "npm cache directory: ${EXTERNAL_NPM_CACHE}"
info "recommended install: npm ci --cache ${EXTERNAL_NPM_CACHE}"
