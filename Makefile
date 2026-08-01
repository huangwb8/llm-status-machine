.PHONY: sync test smoke build

sync:
	@uv sync --frozen --extra test

test:
	@uv run pytest

smoke:
	@uv run lsm smoke --root "tmp/core-smoke-$$(date +%Y%m%d-%H%M%S)" --json

build: test
	@uv build
