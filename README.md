# LLM Status Machine

A local experiment console for running prompts against LLM coding clients and preserving the full behavior trail: prompt, environment, stdout/stderr, transcript, artifacts, workspace snapshot, git commit, changed files, and patch diff.

## What It Does

- Manage reusable prompts.
- Manage LLM environments as Models, including Codex CLI, Claude Code, or any custom command.
- Register Workspace states as local folders.
- Use the Experiment bench to connect Prompts, Models, and Workspace into repeatable runs.
- Run selected prompts once or many times in serial or parallel mode.
- In serial mode, copy `state-i` into an isolated workspace, run one prompt attempt, then use that output as `state-i+1` for the next attempt.
- In parallel mode, keep attempts independent by copying each session from the selected initial Workspace.
- Initialize git inside each session workspace on a single `main` branch, commit the initial state, run the client, commit the result, and store the diff.
- Persist every session transcript as newline-delimited JSON plus `stdout.txt`, `stderr.txt`, metadata, artifacts, and patch files.
- Expose a local API through DevTools for automation and model interaction.

## Run Locally

```bash
npm install
npm run dev
```

Open the Vite URL shown in the terminal, usually `http://localhost:5173`.

The API listens on `http://localhost:4317`.

To keep dependencies outside the repository on this machine:

```bash
npm run deps:link
npm ci --cache /Volumes/2T01/Test/llm-status-machine/.npm-cache
```

If `node_modules` is already a local directory, rerun with `MIGRATE=1 npm run deps:link` to move it into `/Volumes/2T01/Test/llm-status-machine/node_modules` and replace it with a symlink.

## Docker

Build the local image:

```bash
docker build -f deploy/Dockerfile -t llm-status-machine:local .
```

Run the single-container file-storage profile:

```bash
docker compose --project-directory . -f deploy/docker-compose.file.yml up --build
curl http://localhost:4317/api/health
```

Run the full stack with Postgres, Redis, API, and worker:

```bash
cp .env.example .env
docker compose --project-directory . -f deploy/docker-compose.yml up --build
curl http://localhost:4317/api/health
```

The full stack uses:

- `STORAGE_DRIVER=postgres`
- `QUEUE_DRIVER=redis`
- `EVENT_BUS=redis`
- `DATABASE_URL=postgres://...`
- `REDIS_URL=redis://...`

The app and worker share `/app/data` for run files. Workspace state paths must use paths visible inside the container. By default `./examples` is mounted read-write at `/workspaces/examples`, so the bundled dry-run state uses `/workspaces/examples/buggy-js`. For real projects, set `WORKSPACES_MOUNT=/host/projects` and register states with container paths such as `/workspaces/examples/project-a`. Set `WORKSPACES_TARGET=/workspaces/github` if you prefer a different container path, and set `WORKSPACES_MOUNT_MODE=ro` only when the source folder should be read-only.

Codex and Claude CLIs are not installed in the base image. Dry Run and custom commands work out of the box; real LLM clients require extending the image or mounting the CLI, credentials, and workspaces yourself.

## Worker Mode

Local development defaults to a single API process:

```bash
STORAGE_DRIVER=file QUEUE_DRIVER=inline EVENT_BUS=memory npm run start
```

For multi-process execution, run migrations once and start the API plus worker:

```bash
DATABASE_URL=postgres://... npm run db:migrate
STORAGE_DRIVER=postgres QUEUE_DRIVER=redis EVENT_BUS=redis REDIS_URL=redis://localhost:6379 npm run start
STORAGE_DRIVER=postgres QUEUE_DRIVER=redis EVENT_BUS=redis REDIS_URL=redis://localhost:6379 npm run worker
```

The Docker entrypoint runs the Postgres migration automatically when `STORAGE_DRIVER=postgres`; set `RUN_DB_MIGRATIONS=0` to disable that behavior.

## Command Templates

Each environment has a shell command template. Supported placeholders:

- `{prompt}`: shell-escaped prompt text
- `{promptRaw}`: raw prompt text
- `{promptFile}`: path to a prompt text file
- `{model}`: configured model
- `{baseUrl}`: configured base URL
- `{reasoningEffort}`: configured reasoning value
- `{workspace}`: isolated session workspace
- `{simulator}`: bundled dry-run simulator

Examples:

```bash
codex exec --model {model} --sandbox danger-full-access {prompt}
claude -p {prompt} --model {model}
node {simulator} {promptFile}
```

When `baseUrl` is set, the runner also exports `OPENAI_BASE_URL` and `ANTHROPIC_BASE_URL` for the child process. Custom environment variables can be stored in the environment object through the API.

Every child process also receives:

- `LLM_STATUS_MACHINE_API`: local API base URL, defaulting to `http://localhost:4317`
- `LLM_STATUS_MACHINE_RUN_ID`: active run id
- `LLM_STATUS_MACHINE_SESSION_ID`: active session id
- `LLM_STATUS_MACHINE_BRANCH`: snapshot branch name, always `main`
- `LLM_STATUS_MACHINE_WORKSPACE`: isolated workspace path
- `LLM_STATUS_MACHINE_ARTIFACTS_DIR`: directory for extra run artifacts

## API

```bash
GET    /api/store
GET    /api/prompts
POST   /api/prompts
PATCH  /api/prompts/:id
DELETE /api/prompts/:id

GET    /api/environments
POST   /api/environments
PATCH  /api/environments/:id
DELETE /api/environments/:id

GET    /api/states
POST   /api/states
PATCH  /api/states/:id
DELETE /api/states/:id

GET    /api/runs
POST   /api/runs
GET    /api/runs/:id
GET    /api/sessions/:id/diff
GET    /api/sessions/:id/transcript
GET    /api/sessions/:id/artifacts/:name
GET    /api/events

POST   /api/agent/events
POST   /api/agent/artifacts
```

Start a run:

```bash
curl -X POST http://localhost:4317/api/runs \
  -H 'content-type: application/json' \
  -d '{
    "stateId": "state-buggy-js",
    "environmentId": "env-dry-run",
    "mode": "serial",
    "promptRuns": [{ "promptId": "prompt-review", "count": 2 }]
  }'
```

Running clients can add structured events:

```bash
curl -X POST http://localhost:4317/api/agent/events \
  -H 'content-type: application/json' \
  -d '{
    "runId": "'$LLM_STATUS_MACHINE_RUN_ID'",
    "sessionId": "'$LLM_STATUS_MACHINE_SESSION_ID'",
    "type": "assistant_note",
    "payload": "Observed flaky test before editing."
  }'
```

## Data Layout

- `data/store.json`: prompts, environments, states, run metadata in file-storage mode
- `data/runs/<run>/state-N/workspace`: isolated working copy for a session output state
- `data/runs/<run>/state-N/diff.patch`: captured code changes
- `data/runs/<run>/state-N/metadata.json`: prompt/environment/state/branch/source snapshot
- `data/runs/<run>/state-N/transcript.ndjson`: lifecycle events, stdout/stderr, and agent-written events
- `data/runs/<run>/state-N/stdout.txt` and `stderr.txt`: raw process streams
- `data/runs/<run>/state-N/artifacts`: optional extra files written by the client or API

In Postgres mode, prompt/environment/state/run/session/event metadata is stored in Postgres. Session workspaces, diffs, transcripts, and artifacts still live under `data/runs` so large artifacts stay on the shared file volume.

The runner copies each state folder into an isolated session workspace before executing a command. The original state folder is mounted read-write by default so custom commands and local tooling can access it naturally, but normal run diffs are still captured from the isolated session workspace.

## DockerHub Publish

The DockerHub publish path is intentionally local and explicit:

```bash
DRY_RUN=1 make dockerhub-publish
PUSH=0 ALLOW_DIRTY=1 SKIP_TESTS=1 make dockerhub-publish
```

Defaults:

- `IMAGE=huangwb8/llm-status-machine`
- `VERSION=$(node -p "require('./package.json').version")`
- `PROFILE=amd64`
- `PUSH=1`

Stable versions publish `x.y.z`, `latest`, `x.y`, and `x` tags. Prerelease versions publish only the full version tag. The script checks Docker buildx, Docker login, SemVer, tag collisions, and a clean worktree unless `ALLOW_DIRTY=1` is set.
