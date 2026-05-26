# Docker Postgres Redis Worker Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 将 LLM Status Machine 容器化，并分阶段支持 Docker Compose、本地 DockerHub 直推、Postgres、Redis 与独立 worker。

**Architecture:** 先容器化现有 Vite + Express + 文件存储应用，形成可运行的单容器基线；再抽出 storage、event bus、queue 三个适配层，让文件存储/内存队列作为本地默认，Postgres/Redis/BullMQ 作为 Docker 与多进程运行模式。worker 负责长时间运行的 LLM session，API 负责 CRUD、SSE、静态前端和入队。

**Tech Stack:** Node.js 24 Alpine, npm, Vite, React, Express, Docker Buildx, Docker Compose, PostgreSQL, Redis, `pg`, `ioredis`, `bullmq`。

**Minimal Change Scope:** 允许修改 `package.json`、`package-lock.json`、`server/`、`tests/`、`README.md`、`.gitignore`、新增 `Dockerfile`、`.dockerignore`、`docker-compose*.yml`、`deploy/`、`tools/`、`Makefile`。避免改动 UI 视觉与实验业务语义，避免一次性重写前端。

**Success Criteria:** `npm test`、`npm run build` 通过；`docker compose up --build` 后 `GET /api/health` 正常；文件存储模式兼容现有行为；Postgres/Redis 模式可创建 prompt/state/environment 并完成 Dry Run；worker 可独立消费 run job；本地脚本可执行 `DRY_RUN=1 make dockerhub-publish` 并输出 DockerHub 构建/推送步骤；本地 `node_modules` 可软链接到 `/Volumes/2T01/Test/llm-status-machine/node_modules`。

**Verification Plan:** `npm test`; `npm run build`; `docker build -t llm-status-machine:local .`; `docker compose up --build`; `curl http://localhost:4317/api/health`; 使用 Dry Run 调 `POST /api/runs`; `DRY_RUN=1 make dockerhub-publish`; `PUSH=0 make dockerhub-publish ALLOW_DIRTY=1 SKIP_TESTS=1`。

---

## Findings

- 当前应用入口是 `server/index.js`，端口默认 `4317`，同时服务 API、SSE 与 `dist` 静态资源。
- 当前持久化在 `server/store.js` 中硬编码为 `data/store.json`，运行产物在 `data/runs`，状态目录在 `data/states`。
- 当前 runner 在 `server/runner.js` 的 API 进程里通过 `queueMicrotask` 执行，parallel 模式用 `Promise.all`，长任务、进程重启和多实例运行都会受影响。
- 当前 session transcript、stdout、stderr、diff、metadata、artifact 仍适合继续放在文件卷中；Postgres 更适合保存 prompt、environment、state、run、session、event 的元数据。
- `sub2api` 的本地 DockerHub 直推链路主要由 `tools/dockerhub-publish.sh` 和 `Makefile dockerhub-publish` 组成，值得复用其预检、版本标签、dirty worktree、Docker login、tag 防覆盖、`DRY_RUN`、`PUSH=0`、`FORCE=1`、`ALLOW_DIRTY=1` 等行为。
- 本仓库使用 npm 与 `package-lock.json`，所以 Docker 和发布脚本应使用 `npm ci`，不要引入 pnpm。

## Decisions

- 默认保留文件存储模式，新增 `STORAGE_DRIVER=file|postgres`；没有 `DATABASE_URL` 时保持当前本地体验。
- Redis 只在需要 worker 或跨进程 SSE 时启用，新增 `QUEUE_DRIVER=inline|redis` 与 `EVENT_BUS=memory|redis`。
- worker 是必要的，但应在第二阶段启用：LLM session 是长时间子进程，容器/API 重启、横向扩展和 Redis 队列都要求它从 API 中拆出。
- Docker Compose 提供两套路径：`docker-compose.yml` 为完整 Postgres + Redis + app + worker，`docker-compose.file.yml` 为单容器文件存储快速体验。
- Docker 镜像默认只保证 Dry Run 与自定义命令基础能力，运行 Codex/Claude CLI 需要用户扩展镜像或挂载已安装 CLI、凭据与工作区目录。
- 镜像版本以 `package.json` 的 `version` 为唯一来源，符合项目 SemVer 约定。
- 本地 npm module 托管采用软链接：仓库内 `node_modules -> /Volumes/2T01/Test/llm-status-machine/node_modules`，并提供脚本保证目录存在；Docker build 不依赖本地 `node_modules`。

## Task 1: Local Dependency Storage

**Files:**
- Create: `tools/use-external-node-modules.sh`
- Modify: `package.json`
- Modify: `README.md`

**Steps:**
- 新增脚本创建 `/Volumes/2T01/Test/llm-status-machine/node_modules` 与 `/Volumes/2T01/Test/llm-status-machine/.npm-cache`。
- 若仓库内 `node_modules` 是普通目录，提示先移动或由脚本安全迁移；若是软链接，校验目标。
- 在 `package.json` 增加 `deps:link` 脚本，例如 `bash tools/use-external-node-modules.sh`。
- 文档说明推荐流程：`npm run deps:link && npm ci --cache /Volumes/2T01/Test/llm-status-machine/.npm-cache`。

**Verification:**
- `npm run deps:link`
- `test -L node_modules`
- `npm test`

## Task 2: Container Baseline

**Files:**
- Create: `Dockerfile`
- Create: `.dockerignore`
- Create: `docker-compose.file.yml`
- Create: `deploy/docker-entrypoint.sh`
- Modify: `server/index.js`
- Modify: `README.md`

**Steps:**
- 使用多阶段 Dockerfile：builder 阶段 `npm ci`、`npm test`、`npm run build`；runtime 阶段复制 `server/`、`dist/`、`package*.json` 和 production dependencies。
- runtime 基于 `node:24-alpine`，安装 `git`、`bash`、`ca-certificates`、`openssh-client`，创建非 root 运行用户。
- entrypoint 创建 `/app/data`、`/workspaces`，修复挂载卷权限后启动 `node server/index.js`。
- 为 API 增加 `HOST=0.0.0.0` 支持，保持 `PORT=4317` 默认。
- `.dockerignore` 排除 `.git`、`node_modules`、`dist`、`data/runs`、`tmp`、日志、环境文件。
- `docker-compose.file.yml` 只启动 app，挂载 `llm_status_data:/app/data` 与可选 `./examples:/workspaces/examples:ro`。

**Verification:**
- `docker build -t llm-status-machine:local .`
- `docker compose -f docker-compose.file.yml up --build`
- `curl http://localhost:4317/api/health`

## Task 3: Storage Adapter

**Files:**
- Create: `server/storage/index.js`
- Create: `server/storage/fileStore.js`
- Create: `server/storage/postgresStore.js`
- Create: `server/db/schema.sql`
- Create: `server/db/migrate.js`
- Modify: `server/store.js`
- Modify: `server/index.js`
- Test: `tests/storage.test.js`

**Steps:**
- 将现有 `readStore`、`listCollection`、`getItem`、`createItem`、`updateItem`、`deleteItem`、`mutateStore` 包成 storage interface。
- `fileStore` 复用当前 JSON 文件行为，作为默认实现。
- `postgresStore` 使用 `pg`，建表建议：
  - `documents(collection text, id text, body jsonb, created_at timestamptz, updated_at timestamptz, primary key(collection, id))` 保存 prompts/environments/states。
  - `runs(id text primary key, body jsonb, status text, created_at timestamptz, updated_at timestamptz)`。
  - `sessions(id text primary key, run_id text, body jsonb, status text, created_at timestamptz, updated_at timestamptz)`。
  - `session_events(id text primary key, run_id text, session_id text, body jsonb, ts timestamptz)`。
- API 返回值保持现有 shape：`GET /api/runs/:id` 聚合 sessions 与 events。
- 新增 `npm run db:migrate`。

**Verification:**
- `npm test`
- `STORAGE_DRIVER=file npm test`
- `DATABASE_URL=postgres://... STORAGE_DRIVER=postgres npm run db:migrate && npm test`

## Task 4: Event Bus And Queue

**Files:**
- Create: `server/events.js`
- Create: `server/queue.js`
- Modify: `server/runner.js`
- Modify: `server/index.js`
- Test: `tests/queue.test.js`

**Steps:**
- 把 `listeners`、`subscribe`、`broadcast` 从 runner 中移到 `server/events.js`。
- 默认 `memory` event bus 保持现有 SSE 行为。
- Redis event bus 使用 `ioredis` pub/sub，让 worker 发出的事件能被 API SSE 推送。
- 新增 `server/queue.js`：`inline` 模式直接执行当前 microtask；`redis` 模式用 BullMQ `Queue` 入队。
- `startRun` 只创建 run 记录并提交 job，不直接执行 session。

**Verification:**
- `QUEUE_DRIVER=inline EVENT_BUS=memory npm test`
- Docker Compose 中触发 Dry Run，前端实时看到 session 事件。

## Task 5: Worker Process

**Files:**
- Create: `server/worker.js`
- Modify: `server/runner.js`
- Modify: `package.json`
- Modify: `docker-compose.yml`
- Test: `tests/runner.test.js`

**Steps:**
- 将 `runSession` 和 run job 执行逻辑导出为 worker 可调用函数。
- `server/worker.js` 连接 storage、queue、event bus，消费 `run.execute` job。
- `package.json` 增加 `worker` 脚本：`node server/worker.js`。
- inline 模式仍允许 `npm run start` 单进程运行；redis 模式要求 worker 单独启动。
- worker 容器挂载与 app 相同的 `/app/data` 和 `/workspaces`，确保 workspace、diff、artifact 路径一致。

**Verification:**
- `npm run worker` 在 Redis 配置下可消费 run。
- `docker compose up --build app worker postgres redis`
- `POST /api/runs` 返回 202，worker 完成 session，`GET /api/runs/:id` 可看到 completed/failed。

## Task 6: Full Compose Stack

**Files:**
- Create: `docker-compose.yml`
- Create: `.env.example`
- Modify: `README.md`

**Steps:**
- `app` 服务暴露 `${BIND_HOST:-0.0.0.0}:${PORT:-4317}:4317`。
- `worker` 服务使用同一镜像，命令为 `npm run worker`。
- `postgres` 使用 `postgres:18-alpine`，显式 `PGDATA=/var/lib/postgresql/data`，挂载命名卷。
- `redis` 使用 `redis:8-alpine`，开启 AOF，支持可选 `REDIS_PASSWORD`。
- healthcheck 覆盖 app `/api/health`、Postgres `pg_isready`、Redis `redis-cli ping`。
- 明确工作区挂载规则：宿主状态目录必须挂入容器，例如 `/Volumes/2T01/Github:/workspaces/github:ro`，state path 使用容器内路径。

**Verification:**
- `docker compose config`
- `docker compose up --build`
- `curl http://localhost:4317/api/health`
- Dry Run smoke test。

## Task 7: DockerHub Direct Publish

**Files:**
- Create: `Makefile`
- Create: `tools/dockerhub-publish.sh`
- Modify: `README.md`

**Steps:**
- 参照 `/Volumes/2T01/Github/sub2api/tools/dockerhub-publish.sh` 实现本地直推。
- 默认变量：`IMAGE=huangwb8/llm-status-machine`、`VERSION=$(node -p "require('./package.json').version")`、`PROFILE=amd64`、`PUSH=1`、`DRY_RUN=0`、`FORCE=0`、`SKIP_TESTS=0`、`ALLOW_DIRTY=0`。
- 标签规则：所有版本推 `x.y.z`；稳定版额外推 `latest`、`x.y`、`x`；预发布只推完整版本。
- 预检：仓库根目录、SemVer、Docker buildx、Docker login、tag 不存在、默认工作区干净。
- 发布前默认跑 `npm test` 与 `npm run build`。
- 构建命令使用 `docker buildx build --platform linux/amd64 --provenance=false -t ... --push .`；multiarch profile 使用 `linux/amd64,linux/arm64`。
- 发布后 `docker buildx imagetools inspect "$IMAGE:$VERSION"`，可选 `docker run --rm -p 4317:4317 "$IMAGE:$VERSION"` health check。

**Verification:**
- `DRY_RUN=1 make dockerhub-publish`
- `PUSH=0 ALLOW_DIRTY=1 SKIP_TESTS=1 make dockerhub-publish`

## Task 8: Documentation And Changelog

**Files:**
- Modify: `README.md`
- Modify: `CHANGELOG.md`

**Steps:**
- README 增加本地运行、单容器运行、完整 Compose 运行、Postgres/Redis/worker 环境变量、工作区挂载、DockerHub 发布。
- `CHANGELOG.md` 的 `[Unreleased]` 记录容器化、Postgres/Redis/worker、DockerHub 直推和外部 `node_modules` 托管。
- 若修改 `AGENTS.md`，同步检查 `CLAUDE.md`；本计划不要求修改指令文件。

**Verification:**
- README 命令逐条 smoke test。
- `git diff --check`。

## Rollback

- Docker 化改动可通过继续使用 `npm run dev` 回退到本地开发路径。
- `STORAGE_DRIVER=file` 是行为回滚开关；Postgres 失败时不影响文件模式。
- `QUEUE_DRIVER=inline` 与 `EVENT_BUS=memory` 是 worker/Redis 回滚开关。
- DockerHub 脚本默认拒绝覆盖已有版本标签；镜像发布失败不会改动源码状态。

## Implementation Order

1. Task 1 和 Task 2 先做，拿到可构建镜像。
2. Task 3 单独做，并先保证 file driver 兼容。
3. Task 4 和 Task 5 一起做，因为 Redis event bus 与 worker 消费链路相互依赖。
4. Task 6 接入完整 Compose。
5. Task 7 最后做 DockerHub 发布脚本，复用已经验证的 Dockerfile。
6. Task 8 收尾文档与变更记录。
