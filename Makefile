IMAGE ?= huangwb8/llm-status-machine
VERSION ?= $(shell node -p "require('./package.json').version")
PROFILE ?= amd64
PUSH ?= 1
DRY_RUN ?= 0
FORCE ?= 0
SKIP_TESTS ?= 0
ALLOW_DIRTY ?= 0

.PHONY: deps-link docker-build compose-file compose-up dockerhub-publish

deps-link:
	@bash tools/use-external-node-modules.sh

docker-build:
	@docker build -f deploy/Dockerfile -t llm-status-machine:local .

compose-file:
	@docker compose --project-directory . -f deploy/docker-compose.file.yml up --build

compose-up:
	@docker compose --project-directory . -f deploy/docker-compose.yml up --build

dockerhub-publish:
	@IMAGE="$(IMAGE)" VERSION="$(VERSION)" PROFILE="$(PROFILE)" PUSH="$(PUSH)" DRY_RUN="$(DRY_RUN)" FORCE="$(FORCE)" SKIP_TESTS="$(SKIP_TESTS)" ALLOW_DIRTY="$(ALLOW_DIRTY)" bash deploy/dockerhub-publish.sh
