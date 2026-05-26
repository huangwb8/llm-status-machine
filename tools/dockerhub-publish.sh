#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

IMAGE="${IMAGE:-huangwb8/llm-status-machine}"
VERSION="${VERSION:-}"
PROFILE="${PROFILE:-amd64}"
PUSH="${PUSH:-1}"
DRY_RUN="${DRY_RUN:-0}"
FORCE="${FORCE:-0}"
SKIP_TESTS="${SKIP_TESTS:-0}"
ALLOW_DIRTY="${ALLOW_DIRTY:-0}"
RUN_HEALTHCHECK="${RUN_HEALTHCHECK:-0}"

fail() {
  echo "ERROR: $*" >&2
  exit 1
}

warn() {
  echo "WARN: $*" >&2
}

info() {
  echo "==> $*"
}

is_true() {
  case "${1:-0}" in
    1|true|TRUE|yes|YES) return 0 ;;
    *) return 1 ;;
  esac
}

run_cmd() {
  if is_true "${DRY_RUN}"; then
    printf '+'
    printf ' %q' "$@"
    printf '\n'
    return 0
  fi
  "$@"
}

run_cmd_in_repo() {
  if is_true "${DRY_RUN}"; then
    printf '+ cd %q &&' "${REPO_ROOT}"
    printf ' %q' "$@"
    printf '\n'
    return 0
  fi
  (cd "${REPO_ROOT}" && "$@")
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || fail "missing required command: $1"
}

require_repo_root() {
  [[ "$(pwd -P)" == "${REPO_ROOT}" ]] || fail "run this script from the repository root: ${REPO_ROOT}"
  [[ -f "${REPO_ROOT}/package.json" ]] || fail "missing package.json"
  [[ -f "${REPO_ROOT}/package-lock.json" ]] || fail "missing package-lock.json"
  [[ -f "${REPO_ROOT}/Dockerfile" ]] || fail "missing Dockerfile"
  [[ -x "${REPO_ROOT}/deploy/docker-entrypoint.sh" ]] || fail "deploy/docker-entrypoint.sh must exist and be executable"
}

read_version() {
  if [[ -z "${VERSION}" ]]; then
    VERSION="$(node -p "require('./package.json').version")"
  fi
  [[ -n "${VERSION}" ]] || fail "VERSION is empty"
  [[ "${VERSION}" != v* ]] || fail "VERSION must not include the v prefix: ${VERSION}"
  [[ "${VERSION}" =~ ^[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z][0-9A-Za-z.-]*)?$ ]] || \
    fail "VERSION must match x.y.z or x.y.z-prerelease: ${VERSION}"
}

validate_flags() {
  case "${PROFILE}" in
    amd64|arm64|multiarch) ;;
    *) fail "unsupported PROFILE: ${PROFILE} (expected amd64, arm64, or multiarch)" ;;
  esac

  case "${PUSH}" in
    0|1) ;;
    *) fail "PUSH must be 0 or 1" ;;
  esac

  case "${DRY_RUN}" in
    0|1) ;;
    *) fail "DRY_RUN must be 0 or 1" ;;
  esac
}

require_docker() {
  require_command docker
  docker buildx version >/dev/null 2>&1 || fail "docker buildx is not available"
}

require_clean_worktree() {
  if is_true "${DRY_RUN}" || is_true "${ALLOW_DIRTY}"; then
    return 0
  fi

  if [[ -n "$(git -C "${REPO_ROOT}" status --porcelain)" ]]; then
    fail "working tree is dirty; commit/stash changes or set ALLOW_DIRTY=1"
  fi
}

require_docker_login() {
  if [[ "${PUSH}" != "1" ]] || is_true "${DRY_RUN}"; then
    return 0
  fi

  local docker_config="${DOCKER_CONFIG:-${HOME}/.docker}/config.json"
  [[ -f "${docker_config}" ]] || fail "Docker is not logged in; run docker login with a Docker Hub access token"

  if ! grep -Eq '"auths"|"credsStore"|"credHelpers"' "${docker_config}"; then
    fail "Docker login state was not found in ${docker_config}; run docker login first"
  fi
}

require_version_tag_available() {
  if [[ "${PUSH}" != "1" ]] || is_true "${DRY_RUN}" || is_true "${FORCE}"; then
    return 0
  fi

  if docker buildx imagetools inspect "${IMAGE}:${VERSION}" >/dev/null 2>&1; then
    fail "image tag already exists: ${IMAGE}:${VERSION} (set FORCE=1 to overwrite)"
  fi
}

is_stable_version() {
  [[ "${VERSION}" != *-* ]]
}

version_tags() {
  local major minor patch

  printf '%s:%s\n' "${IMAGE}" "${VERSION}"
  if is_stable_version; then
    IFS='.' read -r major minor patch <<< "${VERSION}"
    printf '%s:latest\n' "${IMAGE}"
    printf '%s:%s.%s\n' "${IMAGE}" "${major}" "${minor}"
    printf '%s:%s\n' "${IMAGE}" "${major}"
  fi
}

platforms_for_profile() {
  case "${PROFILE}" in
    amd64) printf 'linux/amd64' ;;
    arm64) printf 'linux/arm64' ;;
    multiarch) printf 'linux/amd64,linux/arm64' ;;
  esac
}

run_preflight_tests() {
  if is_true "${SKIP_TESTS}"; then
    warn "tests and build were skipped because SKIP_TESTS=1"
    return 0
  fi

  info "Running npm test"
  run_cmd_in_repo npm test

  info "Running npm run build"
  run_cmd_in_repo npm run build
}

build_and_publish() {
  local platform output_flag commit created tag
  local tags=()
  local args=()

  platform="$(platforms_for_profile)"
  commit="$(git -C "${REPO_ROOT}" rev-parse --short=12 HEAD 2>/dev/null || printf unknown)"
  created="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

  while IFS= read -r tag; do
    [[ -n "${tag}" ]] && tags+=("${tag}")
  done < <(version_tags)

  if [[ "${PUSH}" == "1" ]]; then
    output_flag="--push"
  elif [[ "${PROFILE}" == "multiarch" ]]; then
    mkdir -p "${REPO_ROOT}/tmp/dockerhub-publish"
    output_flag="--output=type=oci,dest=${REPO_ROOT}/tmp/dockerhub-publish/llm-status-machine_${VERSION}.oci.tar"
  else
    output_flag="--load"
  fi

  args=(
    docker buildx build
    --platform "${platform}"
    --provenance=false
    --label "org.opencontainers.image.source=https://github.com/huangwb8/llm-status-machine"
    --label "org.opencontainers.image.revision=${commit}"
    --label "org.opencontainers.image.version=${VERSION}"
    --label "org.opencontainers.image.created=${created}"
  )

  for tag in "${tags[@]}"; do
    args+=(-t "${tag}")
  done
  args+=("${output_flag}" "${REPO_ROOT}")

  info "Building ${IMAGE}:${VERSION} for ${platform}"
  run_cmd "${args[@]}"

  if [[ "${PUSH}" == "1" ]]; then
    info "Inspecting pushed image ${IMAGE}:${VERSION}"
    run_cmd docker buildx imagetools inspect "${IMAGE}:${VERSION}"
  fi
}

run_container_healthcheck() {
  if ! is_true "${RUN_HEALTHCHECK}"; then
    return 0
  fi

  if [[ "${PUSH}" == "1" || "${PROFILE}" == "multiarch" ]]; then
    warn "RUN_HEALTHCHECK is only supported for local single-platform builds with PUSH=0"
    return 0
  fi

  local container_name="llm-status-machine-publish-check"
  run_cmd docker rm -f "${container_name}"
  run_cmd docker run -d --rm --name "${container_name}" -p 4317:4317 "${IMAGE}:${VERSION}"
  run_cmd docker exec "${container_name}" node -e "fetch('http://127.0.0.1:4317/api/health').then((r)=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"
  run_cmd docker rm -f "${container_name}"
}

main() {
  require_command node
  require_command git
  require_repo_root
  read_version
  validate_flags
  require_docker
  require_clean_worktree
  require_docker_login
  require_version_tag_available
  run_preflight_tests
  build_and_publish
  run_container_healthcheck
  info "Done: ${IMAGE}:${VERSION}"
}

main "$@"
