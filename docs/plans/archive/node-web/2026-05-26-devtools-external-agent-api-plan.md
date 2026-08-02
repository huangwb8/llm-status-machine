# DevTools External Agent API Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 将 DevTools 从“LLM 算力/base URL 配置页”纠正为“外部 AI/Agent 与本应用交互的受控 API 入口”，类似 `bensz-channel` 的 DevTools/Vibe API。

**Architecture:** 保留现有本地前端 API 和运行中 session 上报 API，但新增一层专用于外部 AI 工具的 DevTools API：API Key 鉴权、连接生命周期、资源只读浏览、实验启动、运行状态查询、事件/artifact 上报。前端 DevTools 页面负责生成/撤销密钥、展示 API 接入信息和连接记录；Models 页面负责 LLM 客户端、模型、base URL、命令模板等算力/客户端配置。

**Tech Stack:** Node.js/Express、React/Vite、文件存储与 Postgres document storage、node:test、Docker Compose。

**Minimal Change Scope:** 允许修改 `server/index.js`、新增 `server/devtools*.js` 支持模块、`server/storage/fileStore.js`、`server/storage/postgresStore.js`、`src/main.jsx`、`src/styles.css`、`tests/devtoolsApi.test.js`、`tests/storage.test.js`、`README.md`、`docs/how-it-works.md`、`CHANGELOG.md`。避免修改 runner 的核心执行流程、queue 语义、git 快照逻辑和已有 `/api/agent/*` 行为，除非为了复用鉴权后的上报入口做薄封装。

**Success Criteria:** DevTools 页面不再把 base URL 表达为“API 算力入口”；Models 页面可以配置 base URL；本地用户可生成/撤销 DevTools API Key；外部 AI 可用 `X-Devtools-Key` 连接、心跳、断开、读取实验上下文、启动 run、查询 run/session、写入事件与 artifact；无 key 或 revoked key 返回 401；连接记录可见且可请求终止；现有 `/api/agent/events` 与 `/api/agent/artifacts` 兼容不破坏；测试、构建和 Docker 镜像重建通过；前端改动保存前后对比截图到 `tmp/img-frontend/run-<timestamp>/`。

**Verification Plan:** 运行 `npm test`、`npm run build`、`docker compose -f deploy/docker-compose.yml build app worker`。前端质检启动应用后保存 DevTools/Models 页面改动前后 jpg 截图，人工验证 key 生成、连接记录、base URL 移动位置、外部 API curl smoke test。

---

## Current Diagnosis

当前实现里 DevTools 有两个概念混在一起：

- `src/main.jsx` 的 `DevToolsView` 提供 `Base URL` 编辑，并保存到 `environments`，这会让用户理解为 DevTools 是 OpenAI/Anthropic base URL 或“算力 API”配置。
- `server/index.js` 已有 `/api/agent/events` 与 `/api/agent/artifacts`，但它们更适合“正在运行的 Codex/Claude/custom session 回调写入当前 run”，不是外部 AI 工具作为操作者管理本应用的通用 API。
- README 当前只罗列本地 API 与 agent 上报 API，没有 API Key、连接生命周期、外部工具接入变量、终止信号等 DevTools 语义。

目标形态应参考 `bensz-channel`：

- 管理页展示“API 接入信息、API 密钥、连接记录”。
- 外部工具使用基础 URL + API Key 调用 `/api/devtools/*`。
- API 受控暴露业务对象和操作，而不是提供模型推理能力。

## API Contract

新增外部 AI 专用前缀：`/api/devtools`。

公共端点：

```text
GET  /api/devtools/ping
```

本地管理端点，默认只允许本机请求，远程管理需显式设置 `DEVTOOLS_ADMIN_ALLOW_REMOTE=1`：

```text
GET  /api/devtools/admin
POST /api/devtools/admin/keys
POST /api/devtools/admin/keys/:id/revoke
POST /api/devtools/admin/connections/:id/terminate
```

外部 AI 端点，统一要求 `X-Devtools-Key: <raw key>`：

```text
POST /api/devtools/connect
POST /api/devtools/heartbeat
POST /api/devtools/disconnect

GET  /api/devtools/context
GET  /api/devtools/prompts
GET  /api/devtools/models
GET  /api/devtools/workspaces
GET  /api/devtools/runs
GET  /api/devtools/runs/:id
GET  /api/devtools/sessions/:id/transcript
GET  /api/devtools/sessions/:id/diff
GET  /api/devtools/sessions/:id/artifacts/:name

POST /api/devtools/runs
POST /api/devtools/sessions/:id/events
POST /api/devtools/sessions/:id/artifacts
```

外部 API 返回字段使用本项目语义，但对敏感字段做脱敏：

- `models` 映射自 `environments`，返回 `id`、`name`、`client`、`model`、`baseUrlConfigured`、`reasoningEffort`、`timeoutMs`，不返回 `envVars` 的值。
- `workspaces` 映射自 `states`，返回 `id`、`name`、`description`、`path`。如果后续要支持远程多租户，再增加路径脱敏；当前本地工具场景保持可诊断。
- `POST /api/devtools/runs` 复用 `startRun` 的请求体和响应体，但必须记录 `source: "devtools"`、`connectionId`、`keyPrefix` 到 run metadata 或 run 顶层字段。

## Data Model

新增两个 document collection，文件存储和 Postgres document storage 共用：

```js
devtoolsApiKeys: [
  {
    id,
    name,
    keyHash,
    keyPrefix,
    revokedAt,
    createdAt,
    updatedAt
  }
]

devtoolsConnections: [
  {
    id,
    keyId,
    clientName,
    clientVersion,
    machine,
    workdir,
    lastSeenAt,
    lastError,
    terminateRequestedAt,
    terminatedAt,
    createdAt,
    updatedAt
  }
]
```

Raw key 只在创建响应中返回一次，格式建议为 `lsm_` + 48 位随机字符；存储时只保存 SHA-256 hash 和前缀。

## Task 1: 存储层支持 DevTools Key 与连接记录

**Files:**
- Modify: `server/storage/fileStore.js`
- Modify: `server/storage/postgresStore.js`
- Modify: `tests/storage.test.js`

**Step 1: 写失败测试**

在 `tests/storage.test.js` 增加断言：新建 file store 后 `readStore()` 包含空数组。

```js
assert.deepEqual(aggregate.devtoolsApiKeys, []);
assert.deepEqual(aggregate.devtoolsConnections, []);
```

再增加 document CRUD 测试，确认 `createItem("devtoolsApiKeys", ...)`、`updateItem(...)`、`deleteItem(...)` 可用。

**Step 2: 跑测试确认失败**

Run: `node --test tests/storage.test.js`

Expected: FAIL，Postgres/file store 尚未支持新 collection 或 aggregate shape 缺字段。

**Step 3: 更新 seed 与 collection 白名单**

- 在 `createSeed()` 返回值中加入 `devtoolsApiKeys: []` 与 `devtoolsConnections: []`。
- 在 `server/storage/postgresStore.js` 的 `DOCUMENT_COLLECTIONS` 中加入 `"devtoolsApiKeys"` 和 `"devtoolsConnections"`。
- 确保 `readStore()` 返回包含两个新数组的完整 shape。

**Step 4: 跑测试确认通过**

Run: `node --test tests/storage.test.js`

Expected: PASS。

## Task 2: 实现 DevTools 鉴权与连接服务

**Files:**
- Create: `server/devtoolsAuth.js`
- Create: `server/devtoolsConnections.js`
- Create: `tests/devtoolsApi.test.js`

**Step 1: 写 key 生成与校验测试**

在 `tests/devtoolsApi.test.js` 覆盖：

- 生成 key 返回 raw key、前缀和 key record。
- store 中只保存 `keyHash`，不保存 raw key。
- `findDevtoolsKeyByRaw(raw)` 可以找到未撤销 key。
- revoked key 校验失败。

**Step 2: 实现 key 工具**

`server/devtoolsAuth.js` 暴露：

```js
export async function createDevtoolsKey({ name, createItem }) {}
export async function findDevtoolsKeyByRaw(rawKey, { listCollection }) {}
export async function revokeDevtoolsKey(id, { updateItem }) {}
export function requireDevtoolsKey({ listCollection }) {}
```

使用 `node:crypto` 的 `randomBytes` 和 `createHash("sha256")`。鉴权失败返回：

```json
{ "error": "missing_api_key" }
{ "error": "invalid_or_revoked_api_key" }
```

**Step 3: 写连接生命周期测试**

覆盖 `connect`、`heartbeat`、`disconnect` 的服务函数：

- connect 创建 `devtoolsConnections` 记录。
- heartbeat 更新 `lastSeenAt` 和 `lastError`。
- 当 `terminateRequestedAt` 存在时，heartbeat 返回 `{ "terminate": true }`。
- disconnect 写入 `terminatedAt`。

**Step 4: 实现连接服务**

`server/devtoolsConnections.js` 暴露：

```js
export async function createConnection(apiKey, payload, deps) {}
export async function heartbeatConnection(apiKey, payload, deps) {}
export async function disconnectConnection(apiKey, payload, deps) {}
export async function requestTerminateConnection(id, deps) {}
```

**Step 5: 跑测试确认通过**

Run: `node --test tests/devtoolsApi.test.js`

Expected: PASS。

## Task 3: 增加 DevTools API 路由

**Files:**
- Modify: `server/index.js`
- Modify: `tests/devtoolsApi.test.js`

**Step 1: 写 API route 测试**

用 `createApp()` 启动临时 server，覆盖：

```text
GET /api/devtools/ping -> 200
GET /api/devtools/context without key -> 401
POST /api/devtools/admin/keys from local request -> returns raw key once
GET /api/devtools/context with key -> prompts/models/workspaces/runs
POST /api/devtools/connect with key -> connectionId
POST /api/devtools/heartbeat with connectionId -> terminate false
POST /api/devtools/disconnect with connectionId -> ok true
```

**Step 2: 加本地 admin guard**

复用 `isDirectoryPickerRequestAllowed` 的本机判断模式，新增小函数：

```js
function isDevtoolsAdminRequestAllowed(req) {
  if (process.env.DEVTOOLS_ADMIN_ALLOW_REMOTE === "1") return true;
  return isDirectoryPickerRequestAllowed(req);
}
```

管理端点不使用外部 key；它们是本地 UI 管理密钥的入口。

**Step 3: 加外部 API middleware**

在 `/api/devtools/*` 外部端点前使用 `requireDevtoolsKey`，把解析出的 key 放到 `req.devtoolsApiKey`。

**Step 4: 实现 context 与 run 代理**

`GET /api/devtools/context` 聚合：

```json
{
  "prompts": [],
  "models": [],
  "workspaces": [],
  "runs": []
}
```

`POST /api/devtools/runs` 调用 `startRun()`，请求体沿用 `/api/runs`，并附加 devtools 来源信息。

**Step 5: 实现 session 读写薄封装**

- `POST /api/devtools/sessions/:id/events`：通过 session id 找到 run id 后调用 `recordAgentEvent`。
- `POST /api/devtools/sessions/:id/artifacts`：通过 session id 找到 run id 后调用 `writeAgentArtifact`。
- diff/transcript/artifact 下载复用现有路径校验逻辑，避免复制出不一致的安全判断。

**Step 6: 跑 API 测试**

Run: `node --test tests/devtoolsApi.test.js`

Expected: PASS。

## Task 4: 调整前端信息架构

**Files:**
- Modify: `src/main.jsx`
- Modify: `src/styles.css`

**Step 1: 保存改动前截图**

启动当前前端，保存：

```text
tmp/img-frontend/run-<timestamp>/before-devtools.jpg
tmp/img-frontend/run-<timestamp>/before-models.jpg
```

**Step 2: 把 Base URL 移回 Models**

在 `ModelsView` 中加入 `Base URL` 字段，保存到 `envDraft.baseUrl`。

DevToolsView 删除“Endpoint / Base URL / Save Base URL”卡片，避免继续暗示 DevTools 是 LLM provider 配置。

**Step 3: 重做 DevTools 页面**

DevTools 页面包含：

- API 接入信息：base URL、端点前缀 `/api/devtools`、认证头 `X-Devtools-Key`。
- API Key 管理：名称输入、生成按钮、一次性显示 raw key、复制按钮、撤销按钮。
- 连接记录：客户端、工作目录、最后心跳、状态、终止按钮。
- External API endpoints：按 `ping`、`connection`、`context`、`runs`、`session events/artifacts` 分组展示。
- Events：保留现有事件流，但文案改为运行事件流，不作为“算力 API”说明。

**Step 4: 前端状态与 API 调用**

新增前端请求：

```js
api.get("/devtools/admin")
api.post("/devtools/admin/keys", { name })
api.post(`/devtools/admin/keys/${id}/revoke`)
api.post(`/devtools/admin/connections/${id}/terminate`)
```

生成 key 后只在当前页面状态中保留 raw key，刷新后不再显示。

**Step 5: 保存改动后截图并视觉质检**

保存：

```text
tmp/img-frontend/run-<timestamp>/after-devtools.jpg
tmp/img-frontend/run-<timestamp>/after-models.jpg
```

检查移动端和桌面端：

- DevTools 首屏明确是外部工具接入，不出现 base URL 编辑。
- Models 页面仍能编辑 base URL。
- API key raw 值长文本不溢出。
- 连接表格在窄屏可横向滚动或自然换行。

## Task 5: 文档与兼容性说明

**Files:**
- Modify: `README.md`
- Modify: `docs/how-it-works.md`
- Modify: `CHANGELOG.md`

**Step 1: README 修正 DevTools 定义**

在 README 中说明：

- Models 是 LLM 客户端/模型/base URL 配置。
- DevTools 是给外部 AI/Agent 调用本应用的 API，不提供模型算力。
- `/api/agent/*` 是运行中 session 的兼容上报入口。
- `/api/devtools/*` 是 API Key 保护的外部工具入口。

**Step 2: 增加 curl smoke test**

README 增加最小示例：

```bash
curl http://localhost:4317/api/devtools/ping
curl -H "X-Devtools-Key: $LLM_STATUS_MACHINE_DEVTOOLS_KEY" \
  http://localhost:4317/api/devtools/context
```

**Step 3: 更新架构文档**

`docs/how-it-works.md` 增加三类 API 的边界：

- Local UI API：前端 CRUD 与运行调度。
- Session Agent API：被调用客户端写回当前 run/session。
- DevTools External Agent API：外部 AI 管理和观察本应用。

**Step 4: 更新 CHANGELOG**

在 `[Unreleased]` 记录：

- DevTools 语义纠偏。
- 新增 API Key、连接生命周期与 `/api/devtools/*`。
- Models 页面接管 base URL 配置。

## Task 6: 端到端验证与 Docker 重建

**Files:**
- No source changes unless verification exposes defects.

**Step 1: 全量测试**

Run: `npm test`

Expected: PASS。

**Step 2: 前端构建**

Run: `npm run build`

Expected: PASS。

**Step 3: Docker 镜像重建**

Run: `docker compose -f deploy/docker-compose.yml build app worker`

Expected: PASS。

**Step 4: 本地 smoke test**

启动服务后执行：

```bash
curl http://localhost:4317/api/devtools/ping
curl -X POST http://localhost:4317/api/devtools/admin/keys \
  -H 'content-type: application/json' \
  -d '{"name":"local-codex"}'
curl -H "X-Devtools-Key: <raw-key>" \
  http://localhost:4317/api/devtools/context
curl -X POST http://localhost:4317/api/devtools/connect \
  -H "X-Devtools-Key: <raw-key>" \
  -H 'content-type: application/json' \
  -d '{"clientName":"codex","workdir":"/tmp"}'
```

Expected: ping ok、key 创建返回 raw key、context 返回 prompts/models/workspaces/runs、connect 返回 connectionId。

## Rollback Notes

- `/api/devtools/*` 是新增前缀；若出现问题，可先隐藏前端 DevTools key 管理并停止公开该前缀，不影响现有实验运行。
- `/api/agent/events` 与 `/api/agent/artifacts` 保持兼容，不作为 rollback 对象。
- 存储新增 collection 是向后兼容的 document 数据；回滚代码后这些字段留在 `store.json` 或 Postgres documents 中不会影响旧逻辑。

## Open Decisions

- 是否需要为外部 AI 提供 prompt/model/workspace 的写接口：本计划 MVP 只提供读上下文、启动 run、查询结果和写入 session 事件/artifact，避免外部工具直接改配置造成意外。
- 是否需要独立发布一个 `llm-status-machine-devtools` skill：本计划先把服务端 API 和 UI 做对，后续可以按 `bensz-channel-vibe-config` 模式补 CLI/Skill。
