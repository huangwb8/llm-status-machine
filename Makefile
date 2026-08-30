.PHONY: sync test smoke build

# Keep tool state out of the project root. Override LSM_ENV when needed.
LSM_ENV ?= .bensz-api/.venv
LSM_UV_ENV = UV_PROJECT_ENVIRONMENT=$(LSM_ENV) HYPOTHESIS_STORAGE_DIRECTORY=.bensz-api/.hypothesis
UV = $(LSM_UV_ENV) uv

sync:
	@$(UV) sync --frozen --extra test --extra analysis

test:
	@$(UV) run pytest

smoke:
	@$(UV) run lsm smoke --root "tmp/core-smoke-$$(date +%Y%m%d-%H%M%S)" --json
	@$(UV) run lsm research smoke --root "tmp/research-smoke-$$(date +%Y%m%d-%H%M%S)" --json

build: test
	@$(UV) build
