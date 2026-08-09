# Changelog

本文件记录项目重要变更，格式遵循 Keep a Changelog，并优先维护 `[Unreleased]`。

## [Unreleased]

### Changed（变更）

- 应用版本升级至 `2.1.0`。

### Added（新增）

- 新增 `examples/prior-washout-evidence-gating-study/` 确定性基础设施资格实验：以五臂三阶段符号回归协议覆盖 Prompt、pinned custom runtime、稳定 TrialPlan、独立调度、RawBundle、Git/seal、盲化评分、episode 数据集、推断、报告、store、export 与 recorder 故障注入，并明确隔离真实 LLM pilot 的行为结论边界。

### Fixed（修复）

- 强化 workspace 与 RawBundle 边界：Git 元数据移出模型可写目录，seal 拒绝 symlink、hardlink 和特殊文件，nested stdout/stderr 先在项目外捕获并脱敏后再写入证据包。
- 修复公开 `harness lock --surface simulator` 解引用虚拟环境 Python launcher、导致计划运行时丢失已安装包的问题；同时让 command scorer 编译阶段冻结 shebang 解释器、自动 pin 脚本，并让 subagent-count-quality 示例显式固定 Python 3.12 scorer runtime 与全部评分源码。
- 修复 `custom_command` 的 `{workspace}` 占位符错误展开到 episode 的 `attempts/workspace`、导致自定义 Harness 无法访问实际隔离 workspace 的问题。
- 修复宿主或 workspace Git ignore 令未显式排除的证据文件进入 final manifest 却不进入 final commit、继而破坏 sealed snapshot 评分一致性的问题；显式 `WorkspaceFixture.excludes` 现在是唯一证据排除边界。
- 修复 command scorer 把内容相同但遍历顺序不同的嵌套 final manifest 与 Git archive 误判为篡改的问题。

## [2.0.0] - 2026-08-08

### Removed（移除）

- 移除已废弃的旧 Node/Web file-store 数据迁移 CLI、实现、测试、迁移资料和历史 Node/Web 计划；本地 `var/legacy-node/` 历史数据不再保留，并将应用版本升至 `1.0.0`。

### Added（新增）

- 新增 StudySpec/TrialPlan v2 严谨研究契约：确认性门禁、平衡随机化、显式 arm/comparison set/pair/block/sequence/dispatch provenance，以及冻结的 EvaluationSpec、OutcomeSpec、ContrastSpec 与 AnalysisSpec。
- 新增计划内盲化批量评分：支持 execution-integrity 与 pinned command scorer、只读 sealed snapshot、严格 metric schema、多评分聚合、Krippendorff's alpha、幂等 evaluation manifest 和解盲映射。
- 新增 episode 级 research dataset、full-factorial/matched-pair/block 设计型 bootstrap 与随机化检验、Holm 校正、Markdown/JSON 报告和连续/二元两臂功效计算；NumPy/SciPy 位于独立 `analysis` extra。
- 新增无需密钥的 `lsm research smoke`，覆盖 compile → run → evaluate → dataset → infer → report 全链路。

- 新增 `examples/subagent-count-quality-study/` 可复现实验，比较 Codex + GPT-5.6 Sol medium 工作流中 3、6、9 个评估类 subagent 的软件开发质量，并提供绑定 sealed final commit 的盲化 oracle 评分与 R Markdown 分析。
- 新增 Python 3.12+ `lsm` CLI、Typer/Rich 输出、Pydantic typed domain、稳定 JSONL TrialPlan、SQLite WAL 索引与可重建文件存储。
- 新增 Simulator、Codex exec、Claude print 与声明式 Custom Command adapter；runtime manifest 固定绝对路径、版本、平台、probe 输出与 SHA-256。
- 新增有界 asyncio runner、独立 workspace、POSIX 进程组终止、raw-first recorder、canonical event、四类 outcome、RawBundle seal 与不可变重评分。
- 新增 Prompt lint/render/freeze、full factorial/matched pair/block 编译、legacy 两种目录布局 inventory/validate/import、JSONL/CSV/archive export。
- 新增 Python 单元、性质、异步、集成与 CLI black-box 测试，覆盖独立/继承状态、失败、超时、非法 UTF-8、路径/命令边界、seal 与重索引。
- 新增 Python 核心 ADR、legacy 盘点、Node 迁移指南及独立 Docker Compose 数据边界。
- 新增 `docs/how-it-works.md`，说明应用从配置实验、生成执行计划、执行 session 到保存 transcript/diff/artifact 的完整工作过程。
- 新增 Docker 多阶段构建、单容器文件存储 Compose 与完整 Postgres/Redis/API/worker Compose 栈，支持容器内健康检查和非 root 运行。
- 新增 `STORAGE_DRIVER=file|postgres`、`QUEUE_DRIVER=inline|redis`、`EVENT_BUS=memory|redis`，并加入 Postgres migration、Redis/BullMQ run queue 与独立 worker 进程。
- 新增 DockerHub 本地直推脚本与 Makefile 入口，支持 `DRY_RUN`、`PUSH=0`、`FORCE`、`SKIP_TESTS`、`ALLOW_DIRTY` 和 amd64/arm64/multiarch profile。
- 新增外部 `node_modules` 托管脚本，可将依赖软链接到 `/Volumes/2T01/Test/llm-status-machine/node_modules` 并复用 npm cache。
- 新增 storage、queue、runner 回归测试，覆盖文件存储 CRUD、inline queue 异步语义、Dry Run session、跨进程 artifact 写入。
- 新增核心串行冒烟回归测试，使用默认诗题 Prompt、`./tmp` 子 Workspace 和 Dry Run Model 连续运行 3 次并校验每次记录结果。
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
- 新增交付前核心冒烟测试规则，要求源代码改动收尾前确认默认诗题任务可串行 3 次跑通并完整记录。

### Changed（变更）

- 应用版本升级至 `2.0.0`，并拆分 Study、TrialPlan、Run、RawBundle、Event、Evaluation、Analysis 与 Index schema version；v1 Study/Plan 只读迁移为 exploratory，未来 schema 早期失败。
- subagent-count-quality 示例的后续复现实验改用通用 command scorer、episode dataset 与预注册 contrasts；历史 pilot 及专用评分脚本继续保留为描述性记录。

- 按生命周期收束仓库目录：迁移核验、架构、历史诉求和 Node/Web 计划分别归入稳定分类；legacy CLI 改为必须显式指定 source 路径，本地旧 file store 隔离到被忽略的 `var/legacy-node/`。
- Codex adapter 现在把 execution profile 的 sandbox 权限、ephemeral 会话和 RawBundle artifact 写入目录落实到实际 argv；对暂时无法兑现的 config/research/network 组合会在启动前明确拒绝。
- 项目从 Node.js + Express + React/Vite Web 应用重构为 Python 3.12 本地 CLI；应用版本升级为 `0.2.0`，并与 schema version 分离。
- `serial/parallel` 混合语义拆为独立的 `concurrency` 与 `state_policy`；默认统计单位从 Session 改为完整 Episode。
- Docker 镜像改为多阶段 Python/uv 构建、构建时运行 pytest、非 root CLI runtime；Compose 使用全新 `lsm_py_data` volume，不复用旧栈。
- Makefile、DockerHub 发布脚本、README、工作过程和项目指令统一改用 `uv`、pytest、Python build 与 CLI smoke。
- Experiment 页面将 Run Bench 与 Execution Ledger 从左右并排调整为上下堆叠，减少宽屏下两块核心操作区域互相挤压。
- Prompts/Models/Workspace 集合编辑器左侧记录列表改为紧凑索引条目，避免编辑区高度把记录卡片拉伸成大方格，并提高标题字号与选中态层级。
- Workspace 页面移除顶部 `Add Local Folder` 快捷按钮，保留表单内目录选择按钮与 `Save Workspace` 作为唯一添加流程，减少重复入口造成的困惑。
- Runner 复制 Workspace 时兼容单目录与多目录状态；多目录会在隔离 workspace 中以同级子目录形式呈现，并继续保留 `path` 字段兼容旧数据。
- Docker 相关部署文件集中到 `deploy/`：移动 Dockerfile、Compose 文件与 DockerHub 发布脚本，并保留根目录 `.dockerignore` 作为 build context 规则。
- Docker Compose 的 workspace bind mount 默认改为读写，并新增 `WORKSPACES_TARGET` 与 `WORKSPACES_MOUNT_MODE` 配置；同时让 compose 使用 `.env` 中的 `DEFAULT_STATE_PATH`，便于真实本地项目作为可写工作区接入。
- 内部 session 工作区的 Git 快照统一固定在 `main` 分支，并在前端 session 详情与 metadata/env 中明确记录分支，减少多分支/多批次理解成本。
- 串行实验现在会把每轮输出工作区保存为 `state-N`，并将上一轮输出作为下一轮输入；并行实验仍从初始 Workspace 独立复制。
- DevTools 页面调整为外部 Agent API 管理入口；Models 页面接管 LLM base URL 配置。

### Fixed（修复）

- 修复研究评分与推断的完整性边界：缺失计划 episode 不再被批量评分误报为完成或提前解盲，dataset 会拒绝重复/错配 evaluation 与 episode assignment，确认性 primary outcome 必须有 contrast，量纲外 policy value 与非法 nominal 聚合会在编译前失败；同时修正缺失评委下的 Krippendorff's alpha 加权、`missing_policy=error` 语义、纯描述性分析状态及报告中的标准化效应展示。
- 修复 subagent 数量示例中评分现场 workspace、失效世代竞态漏测、scorer failure 列结构不稳定与固定升序复现实验设计问题；后续运行改用预注册非单调随机顺序，现有 pilot 明确降级为顺序混杂的描述性观察。
- 修复通用 `shell: true` 命令注入面、宿主 PATH 隐式版本漂移、无界并发、超时只终止直接子进程、parser 覆盖 raw evidence 和未 seal 仍可能完成的问题。
- 修复 Simulator runtime 解引用虚拟环境解释器 symlink 后丢失已安装包的问题，并加入回归测试。
- 修复 Docker/headless Linux 环境缺少 `zenity` 时 Workspace 文件夹按钮被禁用、导致无法通过界面新建工作区的问题；现在按钮会打开受 `WORKSPACES_TARGET`/`WORKSPACES_MOUNT` 根目录约束的 API 目录浏览器，选中后自动填入路径与名称，同时保留原生选择器和手动输入流程。
- 更新锁定的兼容依赖版本，修复 `shell-quote`、Vite、`body-parser` 等依赖链中的已公开安全漏洞，使生产依赖审计恢复为 0 项漏洞。
- 修复前后端 session 文件契约不一致的问题：缺失 `metadata`、`diff`、`stdout/stderr` 等记录文件时 API 不再返回 200 空内容，而是返回 404；runner 异常失败时也会尽力写出 `metadata.json` 与 `diff.patch`，方便前端和自动化脚本准确区分“无变更”和“记录缺失”。
- 修复 Postgres 聚合 `/api/store` 在 seed 记录全部 tombstone 后又回填默认 seed 的问题，使 `/api/store` 与集合接口返回一致；同时修复 Postgres session 更新写回 run body 时可能保留旧事件数组的问题。
- 修复前端运行状态和选中状态一致性问题：保存后草稿立即同步后端返回值，手动刷新失败会显示 toast，已删除或变化后的 run/session 会回写有效选中 id，已有 run queued/running 时禁止重复点击 Start。
- 修复前端 `run_failed` 事件缺少醒目样式、禁用 icon button 仍触发 hover 高亮的问题。
- 修复 Models 页面无法编辑后端已支持的运行环境变量问题；现在可用 `KEY=value` 多行文本配置 API Key 等额外环境变量，并由 runner 注入真实命令环境。
- 修复无可用 Model 时前端仍可启动 run、后端把可纠正输入错误返回 500 的问题；Start 按钮现在会校验 Model 存在，`POST /api/runs` 缺 state/environment/prompt 时返回 400。
- 修复运行中选中 session 的 transcript/diff 不随同一 session 事件追加或完成状态自动刷新、错误响应会被当成内容展示的问题。
- 修复后端已保存 stdout/stderr/metadata 但普通 API、DevTools API 与前端 session 详情没有直接读取入口的问题。
- 修复 Postgres 模式下 `/api/runs` 与 `/api/devtools/runs` 列表没有 hydrate sessions/events，导致列表和详情、`/api/store` 返回结构不一致的问题。
- 修复 Postgres 模式下删除内置 seed 记录后会被 seed 合并逻辑重新显示的问题；删除现在写入 tombstone，行为与文件存储的删除语义保持一致。
- 修复 `/api/store` 暴露 DevTools key hash 的问题；普通聚合接口现在只返回公开 key/connection 字段。
- 修复 run 生命周期状态过早显示为 `running` 的问题；run 创建后先进入 `queued`，worker 实际处理时再进入 `running`，完成事件 payload 与最终 run 状态保持一致。
- 修复部分 DevTools 状态缺少 pill 样式、集合 DELETE 未命中时没有统一 JSON 错误体的问题。
- 修复 Postgres 模式下首次编辑内置 seed Model 时保存后又恢复默认值的问题：seed 记录此前只作为读取兜底存在，更新时纯 `UPDATE documents` 找不到实际行却仍返回成功；现在更新文档使用 upsert，首次保存会真正写入 Postgres，并在读取时用持久化记录覆盖同 ID seed、保留其它默认 Model。
- 修复 Prompts/Models/Workspace 集合编辑器左侧记录列表的选中项被鼠标悬停时文字变浅白看不清的问题：`.recordList .record:hover` 的背景规则特异性高于 `.record.active`，会把选中态深色底覆盖成浅色，导致浅色标题/编号/箭头落在浅底上不可读；新增 `.record.active:hover` 规则让选中项悬停时保持深色底。
- 修复真实 Codex/Claude 运行输出较多时，文件存储并发写入 `store.json` 可能造成 JSON 损坏或事件丢失的问题；runner 现在会按序等待进程事件写入完成。
- 修复 Docker 部署下 Workspace 只能填写容器路径的问题：现在可直接输入 `WORKSPACES_MOUNT` 下的宿主机路径，API 会映射为容器可见路径后保存并运行。
- 改进 Docker/Linux 环境缺少原生 GUI 目录选择器时的 Workspace 处理：新增能力检测 API，前端禁用不可用的选择按钮并引导用户填写容器内可见路径。
- 修复本机通过 Docker 端口映射、本机网卡地址或非标准 localhost 入口访问时，Workspace 目录选择器误报只能从 localhost 使用的问题；前端 API 错误提示现在会显示可读文本而不是原始 JSON。
- 修复跨进程 artifact 写入的路径校验与 session 归属校验，避免伪造 run/session 时写入非 session artifact 目录。
- 修复 Postgres 模式下 run/session/event 使用整库快照写入导致的并发覆盖风险，改为按 run/session/event 行级更新和 append。
- 修复 Prompts 编辑时会被后台自动刷新覆盖的问题，并在删除 prompt 后同步清理 Experiment 中的对应选择。

### Removed（移除）

- 移除无实际消费者的 `examples/agent-workspaces/buggy-js/` 通用 Harness 工作区样例及其活跃文档入口。
- 移除已无消费者的旧 Node/Web 空目录、未引用图片、失效 workspace 配置和根目录 legacy `data/` 占位入口；历史数据原件仍保留在本地 `var/legacy-node/`。
- 移除 React UI、Express API、DevTools HTTP API、Node simulator、Postgres/Redis/BullMQ 默认栈、npm/Vite 构建与 shell command template。
- 移除 Dockerfile、Compose、DockerHub 发布脚本及对应 Makefile、文档和工程验收入口；项目仅保留本地 Python CLI 的测试、构建与核心冒烟流程。

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
