# Local LLM Auth Only Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 保证 LLM 鉴权信息绝不进入本软件的配置、API 响应、运行环境覆写或运行记录，Codex、Claude Code 等 AI CLI 只使用宿主机本地已登录/已配置的鉴权。

**Architecture:** 将 Model/Environment 收敛为“客户端调用参数”而非“凭据载体”：保留 client、model、reasoning、timeout、command template 等非密钥字段，移除 `envVars` 注入能力，并在 API 边界、存储读取、runner metadata 写入处统一净化历史字段。runner 继续继承宿主进程环境和 CLI 本地配置，但本应用不再保存、展示、覆盖或记录任何疑似 API key/token/password/secret。

**Tech Stack:** Node.js/Express、React/Vite、文件存储与 Postgres document storage、node:test、Docker Compose。

**Minimal Change Scope:** 允许修改 `server/index.js`、`server/runner.js`、`server/storage/fileStore.js`、`server/storage/postgresStore.js`、新增小型净化 helper、`src/main.jsx`、`tests/runner.test.js`、`tests/storage.test.js`、`tests/devtoolsApi.test.js`、`README.md`、`docs/how-it-works.md`、`CHANGELOG.md`。避免修改 prompt/state/run 调度语义、git 快照逻辑、DevTools API key 机制和 CLI 命令模板语义，除非为了移除 LLM 鉴权字段暴露。

**Success Criteria:** 前端没有 LLM API key/token/env var 配置入口；`POST/PATCH /api/environments` 即使收到 `envVars`、`apiKey`、`token`、`password`、`secret` 等字段也不会持久化；`GET /api/store`、`GET /api/environments`、DevTools context/models 都不返回这些字段；runner 不再从 environment 注入 `envVars`；session `metadata.json` 不包含 `envVars` 或疑似密钥字段；历史 `data/store.json` 或 Postgres documents 中已有的 environment 敏感字段在读写路径被净化；现有 Codex/Claude 命令仍可通过宿主机 CLI 登录状态运行；测试、构建和 Docker 镜像重建通过。

**Verification Plan:** 运行 `npm test`、`npm run build`、`docker compose -f deploy/docker-compose.yml build app worker`。手动用 API 创建带 `envVars: { OPENAI_API_KEY: "sk-test" }` 的 environment，确认存储文件、API 响应、run metadata、transcript/stdout/stderr 均不出现 `sk-test`；在宿主机已有 Codex/Claude 登录的环境下执行一次真实 CLI smoke test 或保留 Dry Run 作为无凭据回归。

---

## Current Diagnosis

当前默认环境确实没有保存 LLM 鉴权信息，但实现上仍存在三条进入路径：

- `server/runner.js` 会把 `environment.envVars` 合并进子进程环境。
- `/api/environments` 的 create/patch 会原样接受并持久化客户端提交字段。
- `metadata.json` 会写入完整 `environment`，如果历史或 API 注入过 `envVars`，密钥会进入运行记录。

目标不是让本应用管理 OpenAI/Anthropic 等供应商凭据，而是让它像真实用户一样调用本机已配置好的 CLI。换句话说：本软件只选择“用哪个 CLI、哪个 model、怎样调用”，鉴权由 `codex`、`claude` 等客户端自己的登录态、配置文件、系统 keychain、宿主机环境或容器挂载机制负责。

## Policy

本项目采用以下 LLM 鉴权边界：

- 允许：`client`、`model`、`reasoningEffort`、`timeoutMs`、`commandTemplate`、非密钥的 `baseUrl`。
- 禁止保存：`envVars`、`apiKey`、`api_key`、`token`、`accessToken`、`refreshToken`、`password`、`secret`、`credential`、`credentials`、`authorization`。
- 禁止记录：任何出现在 environment 对象里的禁用字段，尤其是 session `metadata.json`。
- 不负责清理：宿主进程已有环境变量和 CLI 自身本地配置。它们可以被子进程继承使用，但本应用不读取、不展示、不持久化、不复制到 metadata。

## Task: Add Environment Sanitizer

**Files:**

- Create: `server/environmentSanitizer.js`
- Test: `tests/storage.test.js`

**Step: Write failing sanitizer tests**

在 `tests/storage.test.js` 增加断言：创建 environment 时传入 `envVars`、`apiKey`、`token`、`nested.secret` 后，返回对象和 `readStore()` 都不包含这些字段；保留 `name`、`client`、`model`、`baseUrl`、`reasoningEffort`、`commandTemplate`、`timeoutMs`。

Run:

```bash
npm test -- tests/storage.test.js
```

Expected: FAIL，因为当前存储层会原样保存这些字段。

**Step: Implement minimal sanitizer**

新增 `sanitizeEnvironment(input)`：

- 只白名单复制 environment 支持字段。
- 将 `timeoutMs` 保持现有行为，不额外做复杂校验。
- 不递归保留未知对象，避免未来把凭据藏在嵌套字段里。
- 导出 `SENSITIVE_ENVIRONMENT_FIELDS` 供测试表达策略。

**Step: Apply sanitizer at storage boundary**

在 file storage 和 Postgres document storage 的 `createItem`、`updateItem`、`listCollection`、`getItem`、`readStore` 路径中，对 `collection === "environments"` 统一净化。

要点：

- create/patch 写入前净化，阻止新密钥落盘。
- read/list/get 返回前净化，避免历史密钥继续通过 API 泄露。
- 读取到历史敏感字段时，下一次写 store 会自然清掉；不做一次性迁移脚本，保持 KISS。

**Step: Verify storage tests**

Run:

```bash
npm test -- tests/storage.test.js
```

Expected: PASS。

## Task: Stop Runner Injection And Metadata Leakage

**Files:**

- Modify: `server/runner.js`
- Test: `tests/runner.test.js`

**Step: Write failing runner test**

在 `tests/runner.test.js` 增加一个 custom environment，它的 `commandTemplate` 执行 Node 脚本检查 `process.env.OPENAI_API_KEY` 不等于 environment 里提交的假值，并写入一个 artifact 或 stdout 标记。运行结束后读取 `metadata.json`，断言：

- 不包含 `envVars`
- 不包含 `OPENAI_API_KEY`
- 不包含测试密钥值
- command 仍然正常执行

Run:

```bash
npm test -- tests/runner.test.js
```

Expected: FAIL，因为当前 runner 会注入 `environment.envVars`，metadata 也写完整 environment。

**Step: Remove environment env var injection**

在 `server/runner.js` 中删除：

```js
...(environment.envVars || {})
```

runner 仍保留：

- `...process.env` 继承宿主进程环境
- `LLM_STATUS_MACHINE_*` 会话回调变量
- `LLM_REASONING_EFFORT`
- 非鉴权的 base URL 导出，除非后续决定也完全交给客户端配置

**Step: Sanitize environment before metadata write**

`metadata.json` 写入前使用 `sanitizeEnvironment(environment)`，而不是原始 environment。这样即使旧数据或测试绕过 API，也不会把敏感字段记录进 run artifact。

**Step: Verify runner tests**

Run:

```bash
npm test -- tests/runner.test.js
```

Expected: PASS。

## Task: Harden HTTP API Responses

**Files:**

- Modify: `server/index.js`
- Test: `tests/devtoolsApi.test.js`

**Step: Add API boundary tests**

新增测试覆盖：

- `POST /api/environments` 带 `envVars` 和 `apiKey`，响应不返回敏感字段。
- `GET /api/environments` 不返回敏感字段。
- `GET /api/store` 中的 `environments` 不返回敏感字段。
- DevTools context/models 继续不返回 `envVars`。

Run:

```bash
npm test -- tests/devtoolsApi.test.js
```

Expected: FAIL，直到 API 使用净化后的 storage 结果。

**Step: Keep route layer thin**

如果 storage 层已统一净化，`server/index.js` 不需要重复复杂逻辑，只保留必要的显式断言或调用，确保 route 行为清晰。

**Step: Verify API tests**

Run:

```bash
npm test -- tests/devtoolsApi.test.js
```

Expected: PASS。

## Task: Remove Frontend Credential Semantics

**Files:**

- Modify: `src/main.jsx`

**Step: Remove `envVars` from frontend model draft**

从 `blankEnv` 删除 `envVars: {}`，保存 model 时只提交 UI 支持字段。Models 页面保持没有密钥输入框。

**Step: Add copy guard if needed**

如页面仍展示 Base URL，文案应避免暗示“在这里配置鉴权”。Base URL 可以保留为非密钥 endpoint 配置；若产品决策希望完全由 CLI 管理 endpoint，也可把 Base URL 一并移到命令模板或删除，但这不是本计划的默认范围。

**Step: Build verify**

Run:

```bash
npm run build
```

Expected: PASS。

## Task: Update Documentation And Changelog

**Files:**

- Modify: `README.md`
- Modify: `docs/how-it-works.md`
- Modify: `CHANGELOG.md`

**Step: Rewrite auth guidance**

README 中将当前说明改成：

- 本应用不保存 LLM API key/token。
- Codex/Claude 等 CLI 的鉴权来自宿主机本地登录、CLI 配置或容器挂载。
- Docker 镜像不内置 CLI 与凭据；用户需要自行扩展镜像或挂载 CLI 配置。
- Custom command 如需凭据，应由宿主环境或命令自身读取，不能通过 environment 配置保存。

**Step: Remove envVars docs**

删除或改写 “Custom environment variables can be stored in the environment object through the API.”，避免继续鼓励把凭据放进本软件。

**Step: Record behavior change**

在 `CHANGELOG.md` 的 `[Unreleased]` 记录：

- Removed LLM environment variable persistence/injection from model configuration.
- Sanitized environment metadata to prevent credential leakage into run records.

## Task: Final Verification

**Files:**

- No source edits unless verification exposes a defect.

**Step: Run full automated checks**

Run:

```bash
npm test
npm run build
docker compose -f deploy/docker-compose.yml build app worker
```

Expected: all PASS.

**Step: Manual credential leak smoke test**

Run:

```bash
curl -sS -X POST http://localhost:4317/api/environments \
  -H 'content-type: application/json' \
  -d '{"name":"Leak Test","client":"custom","model":"simulator","commandTemplate":"node {simulator} {promptFile}","envVars":{"OPENAI_API_KEY":"sk-local-test"},"apiKey":"sk-local-test"}'

curl -sS http://localhost:4317/api/environments | rg 'sk-local-test|OPENAI_API_KEY|envVars'
rg 'sk-local-test|OPENAI_API_KEY|envVars' data/store.json data/runs || true
```

Expected:

- API response does not include sensitive fields.
- `rg` finds no test key.
- If `envVars` appears only in old fixture docs or historical plan files, document the location and confirm runtime/store artifacts are clean.

**Step: Optional real CLI smoke test**

在宿主机已登录 Codex 或 Claude Code 的环境中启动一个小型 run。不要在本软件中填写任何 key/token；确认 CLI 能使用自己的本地鉴权完成任务。

## Rollback Notes

如果改动导致某些 custom command 依赖 environment-level `envVars` 失败，不回退密钥存储能力。推荐替代方案：

- 在宿主机 shell、systemd、Docker Compose、Kubernetes Secret 或 CLI 自身配置中提供凭据。
- 对非密钥变量，优先写入 command template 或宿主环境。
- 如未来确实需要“变量模板”，必须另起设计，明确禁止 secret 字段并避免写入 run metadata。
