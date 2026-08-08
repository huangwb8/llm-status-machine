.PHONY: sync test smoke build

sync:
	@uv sync --frozen --extra test --extra analysis

test:
	@uv run pytest

smoke:
	@uv run lsm smoke --root "tmp/core-smoke-$$(date +%Y%m%d-%H%M%S)" --json
	@uv run lsm research smoke --root "tmp/research-smoke-$$(date +%Y%m%d-%H%M%S)" --json

build: test
	@uv build
