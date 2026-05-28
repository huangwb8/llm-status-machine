# Changelog

本文件记录项目重要变更，格式遵循 Keep a Changelog，并优先维护 `[Unreleased]`。

## [Unreleased]

### Added（新增）

- 新增 `docs/how-it-works.md`，说明应用从配置实验、生成执行计划、执行 session 到保存 transcript/diff/artifact 的完整工作过程。
- 新增 Docker 多阶段构建、单容器文件存储 Compose 与完整 Postgres/Redis/API/worker Compose 栈，支持容器内健康检查和非 root 运行。
- 新增 `STORAGE_DRIVER=file|postgres`、`QUEUE_DRIVER=inline|redis`、`EVENT_BUS=memory|redis`，并加入 Postgres migration、Redis/BullMQ run queue 与独立 worker 进程。
- 新增 DockerHub 本地直推脚本与 Makefile 入口，支持 `DRY_RUN`、`PUSH=0`、`FORCE`、`SKIP_TESTS`、`ALLOW_DIRTY` 和 amd64/arm64/multiarch profile。
- 新增外部 `node_modules` 托管脚本，可将依赖软链接到 `/Volumes/2T01/Test/llm-status-machine/node_modules` 并复用 npm cache。
- 新增 storage、queue、runner 回归测试，覆盖文件存储 CRUD、inline queue 异步语义、Dry Run session、跨进程 artifact 写入。
- 明确项目目标与产品计划，补充 `docs/plans/initial-product-plan.md`。
- 增强 session transcript、stdout/stderr、artifact 与运行中 AI 客户端可调用的本地 API。
- 前端新增 transcript 与 artifact 查看区域，方便复盘模型行为。
- Dry Run 模拟器会写入 agent event 与 artifact，用于验证记录链路。
- 新增 Experiment / Prompts / Models / Workspace / DevTools 五个独立界面，Experiment 专门连接 prompt、模型与工作空间。
- 新增实验编排测试，覆盖串行链式输入与并行独立输入两种模式。
- Workspace 表单新增本机目录选择按钮，通过本地 API 调起系统目录选择器并自动填入路径。
- Workspace 页面新增一键添加本地文件夹入口，选中目录后会校验路径并自动创建 workspace。
- Workspace state 新增多文件夹支持，允许一个实验初始状态同时包含多个本地目录。
- 新增 DevTools API Key、外部连接生命周期与只读上下文 API，供外部 Agent 受控读取实验上下文。
- 新增 `/api/devtools/*` 外部 Agent API，支持 API Key 鉴权、连接心跳、run 启动、session 读取以及事件/artifact 写入。
- 新增工程规则：源代码变更后必须重新构建并部署 Docker 镜像供用户审查。

### Changed（变更）

- Workspace 页面移除顶部 `Add Local Folder` 快捷按钮，保留表单内目录选择按钮与 `Save Workspace` 作为唯一添加流程，减少重复入口造成的困惑。
- Runner 复制 Workspace 时兼容单目录与多目录状态；多目录会在隔离 workspace 中以同级子目录形式呈现，并继续保留 `path` 字段兼容旧数据。
- Docker 相关部署文件集中到 `deploy/`：移动 Dockerfile、Compose 文件与 DockerHub 发布脚本，并保留根目录 `.dockerignore` 作为 build context 规则。
- Docker Compose 的 workspace bind mount 默认改为读写，并新增 `WORKSPACES_TARGET` 与 `WORKSPACES_MOUNT_MODE` 配置；同时让 compose 使用 `.env` 中的 `DEFAULT_STATE_PATH`，便于真实本地项目作为可写工作区接入。
- 内部 session 工作区的 Git 快照统一固定在 `main` 分支，并在前端 session 详情与 metadata/env 中明确记录分支，减少多分支/多批次理解成本。
- 串行实验现在会把每轮输出工作区保存为 `state-N`，并将上一轮输出作为下一轮输入；并行实验仍从初始 Workspace 独立复制。
- DevTools 页面调整为外部 Agent API 管理入口；Models 页面接管 LLM base URL 配置。

### Fixed（修复）

- 改进 Docker/Linux 环境缺少原生 GUI 目录选择器时的 Workspace 处理：新增能力检测 API，前端禁用不可用的选择按钮并引导用户填写容器内可见路径。
- 修复本机通过 Docker 端口映射、本机网卡地址或非标准 localhost 入口访问时，Workspace 目录选择器误报只能从 localhost 使用的问题；前端 API 错误提示现在会显示可读文本而不是原始 JSON。
- 修复跨进程 artifact 写入的路径校验与 session 归属校验，避免伪造 run/session 时写入非 session artifact 目录。
- 修复 Postgres 模式下 run/session/event 使用整库快照写入导致的并发覆盖风险，改为按 run/session/event 行级更新和 append。
- 修复 Prompts 编辑时会被后台自动刷新覆盖的问题，并在删除 prompt 后同步清理 Experiment 中的对应选择。

## [1.0.0] - 2026-05-25

### Added（新增）

- 初始化 AI 项目指令文件：生成 `AGENTS.md`、`CLAUDE.md`、`README.md` 与 `.gitignore`
- 配置项目工程原则、工作流和变更记录规范

### Changed（变更）

### Fixed（修复）

---

## 记录规则

- 必须记录影响项目行为、结构、工作流、工程原则、指令文件或关键配置的变更
- 记录应说明改了什么、为什么改，以及影响范围
- 版本号遵循 SemVer：bug fix 递增修订号，新功能递增次版本号，破坏性变更递增主版本号

```markdown
## [版本号] - YYYY-MM-DD

### Added（新增）
- 新增了 XXX：用途是 YYY

### Changed（变更）
- 修改了 XXX：原因是 YYY，影响是 ZZZ

### Fixed（修复）
- 修复了 XXX：表现是 YYY，修复方式是 ZZZ
```
