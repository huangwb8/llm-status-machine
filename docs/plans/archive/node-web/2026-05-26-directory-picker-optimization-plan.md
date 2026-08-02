# Directory Picker Optimization Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 修复 Workspace 选择本地文件夹时在 Docker/Linux 无 `zenity` 环境下报 `spawn zenity ENOENT` 的体验问题，并让用户清楚知道应该输入容器内可见路径。

**Architecture:** 保留现有“浏览器请求 API，由 API host 打开原生目录选择器”的设计，但增加能力检测和更明确的错误语义。前端根据能力状态展示或禁用原生选择按钮，并在不可用时引导用户直接填写已挂载的绝对路径。Docker 场景不强行安装 GUI 依赖，避免把无显示环境的问题伪装成可用功能。

**Tech Stack:** Node.js/Express、React/Vite、node:test、Docker Compose。

**Minimal Change Scope:** 允许修改 `server/systemDialog.js`、`server/index.js`、`src/main.jsx`、`src/styles.css`、`tests/systemDialog.test.js`、必要的前端/API 测试、`README.md`、`CHANGELOG.md`。避免修改 runner、storage、queue、数据库 schema 和实验执行逻辑。

**Success Criteria:** Docker/Linux 无 `zenity` 时不再向用户暴露 `spawn zenity ENOENT`；前端仍支持手动输入 workspace 路径；macOS/Windows/Linux 有可用原生选择器时原功能保持不变；相关测试、构建和 Docker 镜像重建通过；前端改动保存前后对比截图到 `tmp/img-frontend/run-<timestamp>/`。

**Verification Plan:** 运行 `npm test`、`npm run build`、`docker compose -f deploy/docker-compose.yml build app worker`，再通过 `docker compose -f deploy/docker-compose.yml up` 人工验证 Workspace 页面。前端质检需保存改动前后 jpg 截图，并检查按钮禁用态、错误提示和手动路径保存流程。

---

### Task 1: 后端目录选择器能力检测

**Files:**
- Modify: `server/systemDialog.js`
- Modify: `tests/systemDialog.test.js`

**Step 1: 写失败测试**

在 `tests/systemDialog.test.js` 增加 Linux 缺少 `zenity` 的单元测试，目标是把 `ENOENT` 转换为可理解的不可用状态，而不是原始 spawn 错误。

```js
test("directory picker reports unavailable when linux command is missing", async () => {
  const result = await getDirectoryPickerStatus({
    platform: "linux",
    commandExists: async () => false
  });

  assert.deepEqual(result, {
    available: false,
    reason: "missing-command",
    command: "zenity",
    message: "Native directory picker is unavailable because zenity is not installed on the API host."
  });
});
```

**Step 2: 运行测试确认失败**

Run: `npm test -- tests/systemDialog.test.js`

Expected: FAIL，提示 `getDirectoryPickerStatus` 未导出或不存在。

**Step 3: 实现最小能力检测**

在 `server/systemDialog.js` 中新增 `commandExists` 和 `getDirectoryPickerStatus`。`commandExists` 使用 `execFile("sh", ["-lc", "command -v <cmd>"])`，Windows/macOS 保持现有命令判断；Linux 缺少 `zenity` 时返回结构化状态。

关键行为：
- `createDirectoryPickerCommand` 继续只负责按平台生成命令。
- `getDirectoryPickerStatus` 负责判断当前平台是否支持、命令是否存在。
- `selectDirectory` 先检查状态；不可用时抛出明确错误，不包含 `spawn zenity ENOENT`。

**Step 4: 补充选择器错误测试**

增加 `selectDirectory` 的可注入测试，模拟 `commandExists=false`，确认抛错信息为：

```text
Native directory picker is unavailable because zenity is not installed on the API host.
```

**Step 5: 跑测试确认通过**

Run: `npm test -- tests/systemDialog.test.js`

Expected: PASS。

---

### Task 2: API 暴露目录选择器状态

**Files:**
- Modify: `server/index.js`
- Test: `tests/systemDialog.test.js` 或新增轻量 API 测试

**Step 1: 写失败测试**

新增测试覆盖 API 能返回选择器状态，至少验证响应结构：

```js
{
  "available": false,
  "platform": "linux",
  "reason": "missing-command",
  "command": "zenity",
  "message": "Native directory picker is unavailable because zenity is not installed on the API host."
}
```

**Step 2: 新增只读端点**

在 `server/index.js` 添加：

```js
app.get("/api/system/directory-picker", asyncRoute(async (_req, res) => {
  res.json(await getDirectoryPickerStatus());
}));
```

同时更新 `/api/system/select-directory`，当状态不可用时返回 `409`：

```js
return res.status(409).json({ error: status.message, picker: status });
```

**Step 3: 保留本机访问保护**

`isDirectoryPickerRequestAllowed(req)` 仍然在打开原生弹窗前执行。能力状态端点只暴露平台和可用性，不触发弹窗。

**Step 4: 跑相关测试**

Run: `npm test`

Expected: PASS。

---

### Task 3: 前端 Workspace 体验优化

**Files:**
- Modify: `src/main.jsx`
- Modify: `src/styles.css`

**Step 1: 保存改动前截图**

启动当前版本：

```bash
npm run dev
```

在 `tmp/img-frontend/run-<timestamp>/before-workspace.jpg` 保存 Workspace 页面截图，覆盖桌面视口即可；如移动端布局受影响，再补 `before-workspace-mobile.jpg`。

**Step 2: 获取选择器状态**

在应用初始化或 Workspace view 加载时调用：

```js
const picker = await api.get("/system/directory-picker");
```

将状态保存在 React state，例如：

```js
const [directoryPicker, setDirectoryPicker] = useState({ available: true, message: "" });
```

**Step 3: 调整按钮行为**

当 `directoryPicker.available === false`：
- `Choose folder` icon button 禁用。
- `Add Local Folder` 不再调用原生 picker，而是聚焦 `Folder` 输入框或创建一个空草稿。
- 保留手动输入 `/absolute/path/to/workspace` 的主流程。
- 错误提示使用 API 返回的 `message`，不要显示原始 spawn 信息。

**Step 4: 处理运行时失败**

`pickWorkspaceFolder` 和 `createWorkspaceFromFolder` 捕获 `409` 时，把 `directoryPicker` 更新为不可用状态，并显示可操作提示。普通验证错误仍然走现有 `setError(err.message)`。

**Step 5: 样式收束**

在 `src/styles.css` 中给不可用状态增加紧凑提示样式，避免新增大块说明卡片。提示应靠近 Folder 输入区，保持当前控制台式 UI 密度。

**Step 6: 保存改动后截图**

在 `tmp/img-frontend/run-<timestamp>/after-workspace.jpg` 保存后截图；如果有移动端截图，也保存 `after-workspace-mobile.jpg`。

---

### Task 4: 文档与变更记录

**Files:**
- Modify: `README.md`
- Modify: `CHANGELOG.md`

**Step 1: 更新 README**

在 Docker/Workspace 说明附近补充：
- Docker 镜像默认不安装 GUI 目录选择器。
- Workspace 路径必须是 API host 或容器内可见路径。
- Docker 用户优先手动填写 `/workspaces/...` 路径。
- 原生目录选择器仅适合本机直接运行服务时使用。

**Step 2: 更新 CHANGELOG**

在 `[Unreleased]` 下记录：

```markdown
- Improved Workspace directory picker handling for Docker/Linux environments without native GUI picker support.
```

---

### Task 5: 构建、Docker 重建与人工验收

**Files:**
- No direct source edits

**Step 1: 全量测试**

Run: `npm test`

Expected: 所有 `node:test` 用例通过。

**Step 2: 前端构建**

Run: `npm run build`

Expected: Vite build 成功，无 JSX/CSS 错误。

**Step 3: Docker 镜像重建**

Run:

```bash
docker compose -f deploy/docker-compose.yml build app worker
```

Expected: app 和 worker 镜像构建成功。

**Step 4: Docker 运行验收**

Run:

```bash
docker compose -f deploy/docker-compose.yml up
```

Manual checks:
- 打开 `http://localhost:4317`。
- 进入 Workspace。
- `Choose folder` 不触发 `spawn zenity ENOENT`。
- 手动输入 `/workspaces/examples/buggy-js` 后可保存 workspace。
- Dry Run 仍可正常启动并生成记录。

**Step 5: 回归本机模式**

如果在 macOS 或已安装 `zenity` 的 Linux 本机运行：
- `Choose folder` 仍能打开原生目录选择器。
- 取消选择时不报错。
- 选择目录后能自动填入路径。

