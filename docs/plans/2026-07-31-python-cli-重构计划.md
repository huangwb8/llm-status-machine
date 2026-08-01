# LLM Status Machine Python CLI 重构计划

## 问题是什么

- **当前现象：** 项目目前是 Node.js + Express + React/Vite 的本地 Web 应用，没有 Python 包和真正的命令行入口。Prompt、Model、Workspace、Run、Session、Event、Artifact 等概念已经存在，但 Harness 只是一个会被 `shell: true` 执行的命令字符串，Codex/Claude Code 版本来自宿主环境。
- **工程影响：** 现有并行直接无界启动全部 session；超时主要终止直接子进程；原始 Harness 协议没有稳定采集；成功状态、文件证据和存储索引并非完全自描述。扩大实验规模后，会出现资源失控、孤儿进程、记录丢失、版本漂移和结果无法复核等风险。
- **方法学影响：** 当前 `serial` 同时代表“顺序执行”和“把上一轮工作区传给下一轮”。这会把普通独立重复与 carry-over 实验混在一起。工具调用等轨迹事件也不能被当成独立样本；真正的观察单位应是一次从明确初始状态开始的完整 Harness episode。
- **迁移影响：** 本地旧数据已经同时存在 `<run>/<session-id>` 与 `<run>/state-N` 两种目录布局，现有容器和 volume 也仍在运行。直接覆盖或就地迁移会带来不可逆的数据风险。
- **已知根因：** 现有实现首先围绕 Web 管理台和通用 shell runner 演进，尚未建立 Harness 协议、运行时版本、实验编译、原始证据、评分与统计分析之间的稳定边界。这次工作不能按语言逐行翻译，而应保留有效的产品语义和证据闭环，重新建立 Python 核心。

调研资料与现有代码共同支持以下判断：项目应定位为“研究完整 Harness 行为的实验基础设施”，不是普通的批量 Prompt 脚本、聊天 UI 或排行榜。MacBehaviour 值得借鉴的是条件矩阵和原始/解析数据分离；SWE-bench、SWE-agent、AgentBoard、AgentDojo、Inspect AI、EvalScope 等值得借鉴的是可复位环境、真实 CLI bridge、轨迹、过程指标和外部验收，但不应复制任何一个项目的完整产品形态或内部数据结构。

## 要达到什么目标

### 完成后的变化

- 项目成为 Python 3.12+ 的本地 CLI，源码、测试、构建和发布不再依赖 Node.js、React、Express、Redis、BullMQ 或 Postgres。
- 用户可以把 Prompt、工作区、Harness surface、Harness 精确版本、模型端点、权限/网络配置和重复策略编译为不可变实验计划，再串行或有界并行执行。
- Codex、Claude Code 和自定义 Harness 通过统一但可扩展的 `HarnessAdapter` 接入；复杂第三方适配器可作为 Python entry point 插件安装。
- Harness 不从宿主 `PATH` 隐式解析。每次运行都能说明使用了哪个发行物、精确版本、校验摘要、绝对路径、平台、能力集和实际 `--version` 输出。
- 每个 episode 都在独立或显式继承的工作区中运行，完整保留实际 Prompt、原始事件流、stdout/stderr、退出信息、工作区前后状态、Git 证据、artifact、资源消耗和版本指纹。
- 原始记录不可被评分器或解析器改写；parser、scorer 和分析方法可以在不重跑 Harness 的情况下升级、重算和审计。
- Prompt 成为可版本化、可组合、可扰动、可配对、可冻结的实验仪器，支持系统地研究措辞、顺序、上下文、权限、Harness、模型和时间批次等因素。
- 现有数据可通过只读 importer 盘点、验证和导入；切换失败时可以恢复旧服务和旧数据，而不需要把新数据逆向写回旧格式。

### 产品定位

> 一个以可复位 Harness episode 为实验单位、可固定真实 CLI 运行时、可编译 Prompt/条件矩阵，并将原始过程与工作区结果封存为可审计证据的 Python 命令行实验台。

### 不在首版处理范围

- 不保留 React Web UI、IDE 工作流、SSE、DevTools HTTP API 或 TUI 作为重构首版门槛。
- 不做多机调度、云控制面或默认的 Postgres/Redis worker 部署；先把单机 CLI 的正确性和可复现性做实。
- 不承诺所有历史 Harness 版本永久可下载，也不承诺闭源模型的服务端快照、路由和系统提示可以完全冻结。
- 不用 `uv`、npm cache、原生二进制或 Docker 这个名称本身来宣称安全隔离；只有实际受控的文件、进程、网络、用户和挂载策略才能构成相应边界。
- 不把 SDK、Inspect AI、EvalScope 或厂商事件 schema 作为项目永久领域模型。它们可以成为可替换 backend 或 adapter。
- 不在最小垂直切片中实现完整 Prompt 扰动库、Latin square、fractional factorial、自适应试验、LLM judge 平台或排行榜。
- 不把 Codex 与 Claude Code 的总体差异直接称为纯 Harness 因果效应。模型、隐藏提示、工具和供应商无法正交时，只报告完整 `system_condition` 的差异。

## 核心架构裁决

### Python 核心不等于 Harness 也必须由 Python 实现

项目自身使用 Python 和 `uv` 管理；Codex、Claude Code 仍是外部被测程序。`uv` 负责 Python 项目、Python CLI 和 adapter 插件的依赖锁定，它不是虚拟机，不能管理 Node/native Harness，也不提供文件系统、网络或进程安全沙箱。

建议的基础技术组合为：

- Python 3.12+、`uv`、`pyproject.toml` 和 `uv.lock`。
- Typer + Rich 提供命令、帮助、进度和结构化终端输出。
- Pydantic v2 校验配置和领域对象；Study 使用 YAML，Prompt 正文使用 Markdown，编译后的计划使用稳定 JSONL。
- 标准库 `asyncio.TaskGroup`、`asyncio.create_subprocess_exec` 和有界队列构建执行内核，禁止默认走 shell。
- SQLite WAL 保存索引、状态和查询字段；原始流、事件、workspace manifest、diff 和 artifact 保存在文件系统或 SHA-256 内容寻址存储。
- pytest、pytest-asyncio 与 Hypothesis 覆盖单元、异步、契约和计划编译性质测试。

这些库是当前推荐基线；正式实现时只有在直接验证需求的情况下才增加依赖。

### 稳定领域模型

主数据流固定为：

```text
StudySpec -> compile/resolve -> TrialPlan -> Run
                                         -> Episode -> Attempt -> RawBundle
RawBundle -> Parser/Normalizer -> Canonical Event
RawBundle -> Scorer/Annotator  -> Score/Annotation -> Analysis/Export
```

| 对象 | 职责与边界 |
| --- | --- |
| `StudySpec` | 描述研究问题、Prompt/任务材料、因素、约束、重复、主要指标、排除/重试/停止规则和 seed。 |
| `TrialPlan` | 将所有 episode、条件分配、顺序、runtime、workspace parent 和预算完全物化并冻结；runner 不得执行时临时扩矩阵。 |
| `Run` | 一次执行某个已冻结计划的运行实例，记录排队、开始、结束、取消和恢复状态。 |
| `Episode` | 一次从明确 baseline 开始的完整 Harness 行为过程，也是默认统计单位。 |
| `Attempt` | episode 的具体进程启动。基础设施重试会产生新 attempt，并用 `retry_of` 保留谱系。 |
| `Event` | episode/attempt 内部观察；带单调序号和原始字节引用，不是独立统计样本。 |
| `RawBundle` | 实际输入、原始流、厂商事件、运行时/环境指纹、退出、workspace 前后状态、diff、artifact 和 seal 的不可变证据包。 |
| `Score/Annotation` | 只读取 sealed bundle 的版本化派生结果；可以重算，不能改写原始执行事实。 |

旧 `Session` 导入后映射为 `Episode`；旧 `serial` 映射为 `concurrency=1, state_policy=carry_forward`，不继续作为新领域枚举。

### 将混合的 Model 拆成正交配置

现有 Environment 同时混合 client、model、base URL、command、env 和 timeout。新系统拆为：

- `HarnessSurface`：例如 `codex_exec_cli`、`claude_print_cli`，未来的 `codex_app_server` 必须是另一个 surface。
- `RuntimeBuild`：发行渠道、精确版本、平台、校验摘要、绝对可执行路径或 OCI digest。
- `ModelEndpoint`：模型 ID、provider/base URL、认证引用和可见的服务端元数据。
- `ExecutionProfile`：推理强度、配置发现、权限、sandbox、工具、网络、locale、时区、超时和资源限制。
- `WorkspaceFixture`：一个或多个源目录、baseline、复制/排除策略及 hidden evaluator 边界。

这样才能让实验明确操控一个因素，并诚实记录无法正交的组合条件。

## Harness 与运行时设计

### 可插拔协议

`HarnessAdapter` 只负责厂商接口差异：能力探测、生成 argv/stdin/env、解码原生协议、生成统一事件和分类结局。`RuntimeProvider` 负责解析、下载、校验、缓存和物化版本。通用 `ProcessSupervisor`、`Recorder` 与 `WorkspaceBackend` 不写进每个 adapter。

内置 adapter：

- `CodexAdapter`：首版监督真实 `codex exec --json`。
- `ClaudeCodeAdapter`：监督真实 `claude -p --output-format stream-json --verbose`。
- `CustomCommandAdapter`：只接受声明式 argv、Prompt transport、成功退出码和 `text|jsonl` decoder；不接受任意 shell 模板作为安全扩展机制。

复杂新 Harness 通过 `llm_status_machine.harnesses` entry point 注册。Adapter 必须声明结构化流、partial message、subagent tree、resume、输出 schema、permission/sandbox、配置隔离、模型/effort 参数和 stdin 限制等能力。计划请求了不支持的能力时应预检失败；旧版本可以明确使用 `raw-only`，但不能伪装成与新版本事件等价。

### 多版本物化

每个 runtime manifest 同时保存 requested selector 与 resolved identity：发行包/asset/image、精确版本、来源 URL、registry integrity 或签名、可执行文件 SHA-256、平台/arch/libc、安装器和 Node/npm 版本、OCI digest、实际 `--version` 输出、解析时间和 adapter 版本。

运行时策略按以下顺序落地：

| Provider | 用途 | 边界 |
| --- | --- | --- |
| 官方精确 native asset | Codex/Claude 的默认快速路径，最接近真实 CLI | 固定可执行物，不固定 OS、HOME、网络和工具链。 |
| 预物化 npm 环境 | 历史/兼容版本或 native asset 不便获取时使用 | 固定 package integrity、Node/npm、隔离 cache/config；仍需审查 lifecycle script。 |
| OCI image by digest | CI、旧版本、不受信任工作区和确认性实验 | 必须同时固定 user、mount、network、capabilities 和资源限制，不能只记录 tag。 |
| unmanaged absolute path | 临时调试 | 显式标记不可复现，不能满足“宿主版本无关”的验收。 |

`latest`/`stable` 只能用于 `plan resolve`；执行前必须冻结为精确版本与 digest。缓存按内容寻址，下载到临时目录，校验与 probe 通过后原子发布，并用跨进程锁防止重复安装；离线模式缺少已冻结缓存时直接失败。

Codex 当前 Python `openai-codex` SDK 自带 pinned CLI runtime，但控制的是 app-server/JSON-RPC。它以后可以作为 `codex_app_server` 独立 adapter，不能替代 `codex_exec_cli`，也不能把两个 surface 的实验结果混在一起。

### 配置发现与研究模式

配置隔离不是固定开关，而是实验条件：

- `hermetic`：所有上下文显式传入，尽量跳过用户配置、hooks、skills、plugins 和 MCP。
- `workspace_native`：使用隔离的应用 HOME，但保留工作区内规则和说明，作为多数 Harness 行为实验的推荐 profile。
- `ambient`：显式继承宿主配置，用于生态兼容研究，并醒目标记为不可完全复现。

研究报告还要区分：

- `ecological`：观察真实 Harness、模型和工作区组成的完整生态行为。
- `controlled`：尽可能统一模型、权限、工具和上下文，以比较某个机制。

## 异步执行与证据记录

### 有界调度

- 一个 attempt 由 TaskGroup 统一管理 process wait、stdout pump、stderr pump、durable writer、decoder 和 timeout monitor；run 层 TaskGroup 管理 episode。
- 使用公平队列和全局、per-harness、per-provider/model、per-credential 多级 semaphore。并行上限、磁盘、文件描述符、token、成本和 rate limit 在 admission 阶段检查。
- `concurrency` 只决定同时运行数；`state_policy=independent|carry_forward|branch` 只决定工作区拓扑。普通重复默认 `independent`。
- 只自动重试下载、安装和明确的临时基础设施失败。Harness 非零退出、拒答、超时或解析错误可能是行为结果；重试必须产生新 attempt。

### 原始流优先

- stdout/stderr 先按 byte chunk 追加到 durable raw 文件，再增量做 UTF-8/JSONL framing；不假设一行事件很短，也不因非法 UTF-8 或未知事件丢失原始内容。
- 持久化队列按字节有界。界面进度可以合并 token delta，但 durable recorder 不能丢事件；磁盘跟不上时以背压或 `capture_failed` 终止，不能继续产生不可审计结果。
- stdout 与 stderr 只能分别记录 byte offset、stream sequence，再记录 recorder 的 arrival sequence、UTC 和 monotonic time；不得声称还原了两个 OS pipe 的真实全序。
- Canonical event 保存 schema version、run/episode/attempt、seq、source、vendor kind、raw offset/length/hash、时间、parent 和 payload。未知事件标为 `vendor.unknown`，解析失败标为 `protocol.parse_error`。

### 完成、取消和恢复

Attempt 的最终事实拆为：

- `process_outcome`：退出码、signal、timeout 和 kill stage。
- `protocol_outcome`：是否观察到 Harness terminal event、是否有 parser gap。
- `capture_outcome`：raw streams、event writer 和 manifest 是否完成 flush/seal。
- `workspace_outcome`：post snapshot、diff 和 artifact manifest 是否完成。
- `evaluation_outcome`：后续 scorer 结论，不参与改写执行状态。

只有前四项满足计划约束时 episode 才能是 `completed`。非零退出、超时、取消和异常也应在 `finally` 封存已经获得的原始流和 post-state。

POSIX 使用独立 process group，先 SIGTERM、宽限后 SIGKILL 并 reap；Windows 使用 Job Object 覆盖后代；OCI provider 用容器 stop/kill 作为额外边界。Lease 记录 PID/PGID/Job/container、process start-time 与 command hash，重启后先核对身份再回收，避免 PID 复用误杀。

### 工作区与 Git

- 永不在 source workspace 直接运行。先生成不可变 baseline manifest，再以 reflink/clone/copy 物化 episode workspace。
- Independent episode 从同一 baseline 分别开始；carry-forward 只能引用上一 episode 已 sealed 的 post snapshot。
- 多目录 Workspace 使用稳定映射。复制策略、symlink、submodule、ignored/cache/venv 和大文件排除都写入 manifest，并记录排除原因。
- 文件系统 manifest 至少记录 relative path、类型、mode、size、mtime、SHA-256 和 symlink target；Git 使用 NUL 格式处理异常文件名，并保存 binary/full-index diff、rename、untracked/ignored 和 executable bit。
- 实验内部 snapshot repo 与用户仓库分离，绝不修改 source 的 refs/index。Git 不能覆盖 xattr、ACL、稀疏文件和所有特殊文件，报告必须注明 capture policy 的真实范围。

## Prompt 与实验能力

### Prompt 是带谱系的实验仪器

Prompt 不再是数据库中的一个字符串，而是不可变 revision：

- 分层保存 task、workspace instructions、context fixtures、tool feedback、output protocol 等已知输入；不可见厂商提示标为 `opaque/unknown`。
- 类型化变量声明枚举、范围、文件/题项引用和必填条件。渲染前 lint 未绑定变量、重复 ID、条件标签泄漏、输出 schema 和预算。
- 保存实际发送 bytes、编码、尾随换行和 SHA-256；命令日志用摘要引用，不重复暴露正文。
- 变体保存 parent revision、transform 类型、算法版本、seed 和人工语义等价审查。支持措辞、拼写/标点、礼貌度、顺序、few-shot、角色、上下文长度和安全指令冲突等因素。
- 支持 `dev/pilot` 与冻结的 `confirmatory/holdout` 分区，避免同一结果既用来调 Prompt 又用来确认假设。

最小 compiler 先支持 full factorial、matched pair 和 block；编译前显示 episode 数量、非法组合、预计磁盘、token、费用和时间。编译产出稳定 `plan.jsonl`，相同 frozen spec + seed 必须字节一致；调度并发变化不能改变条件分配。

### 运行与评价分离

- 终局 scorer 优先使用测试、验收谓词、diff policy 和安全规则等可重放指标。
- 过程 scorer 可计算里程碑进度、read-before-edit、test 行为、错误恢复、失败位置、无效重复动作和 time-to-event。
- 稳定性报告包括同题重复一致率、`pass^k`、`pass@k`、轨迹离散度和 Prompt 成对翻转率，不能只选最好的一次。
- 效率指标包括墙钟、队列、调用、token、缓存、工具时间和成本；失败、拒答、格式错、过滤、截断和超时都作为结局保存。
- LLM judge 只能作为版本化且经过人工金标准、盲评、位置互换与一致性检查的辅助 scorer；默认不能让模型自评自己的输出。
- 结果可导出 JSONL、CSV/Parquet 和适合 Python/R 分层统计的数据表。任一 score 或图表必须能追溯到 scorer、parser、episode、raw blob、workspace 和 runtime digest。

## 建议的 CLI 形态

| 命令组 | 主要能力 |
| --- | --- |
| `lsm init` / `lsm doctor` | 初始化实验项目；检查 Python、Git、容器、runtime cache、平台、磁盘和凭据引用，不输出秘密。 |
| `lsm harness` | 列出 adapter/surface/capability；resolve、fetch、verify、probe、lock 和 offline cache 管理。 |
| `lsm prompt` | add/import、lint、render、preview、diff、variant、freeze 和 lineage。 |
| `lsm workspace` | register、snapshot、validate、show manifest 和 materialize dry-run。 |
| `lsm study` | validate、compile、estimate、freeze 和输出 TrialPlan。 |
| `lsm run` | start、status、watch、cancel、resume/reconcile 和 episode 查询。 |
| `lsm episode` | show、events、stdout/stderr、diff、artifacts、manifest 和 validate seal。 |
| `lsm evaluate` | 运行/重跑 parser、scorer、annotation 和 comparison。 |
| `lsm export` | 导出原始 bundle、规范表、统计数据和可携带归档。 |
| `lsm store` | reindex、verify、repair、retention 和安全 GC。 |
| `lsm legacy` | inventory、validate、import 与迁移对账；默认只读。 |

命令应同时提供人类可读输出和稳定的 `--json`；修改状态的命令支持 dry-run/明确确认，实验执行本身以已冻结计划为唯一输入。

## 实施范围与顺序

### 冻结事实与兼容边界

- 记录旧 HEAD、当前未提交的 `Prompts.md`、应用版本冲突、活跃容器/volume 和所有 legacy file/Postgres 数据 inventory；对 `data/store.json`、`data/runs` 与导出数据做不泄密 checksum 快照。
- 形成 ADR：领域术语、episode/attempt、concurrency/state policy、outcome、runtime/surface/profile、目录与 schema version、capture/secret policy。
- 从现有 runner 测试提取脱敏 golden fixtures，并定义两种 legacy run 目录到新 schema 的 mapping。

**阶段门禁：** inventory、备份、术语/schema ADR 与 legacy validator 设计完成前，不删除 Node 代码、不修改旧数据、不复用现有 Docker volumes。

### Python 骨架与最小实验编译

- 建立 `pyproject.toml`、`uv.lock`、typed domain、CLI、schema migration、SQLite index 和 filesystem object store 接口。
- 实现 Prompt lint/render/freeze 与最小 StudySpec/TrialPlan；`concurrency` 和 `state_policy` 成为独立字段。
- 实现 deterministic Simulator adapter，可修改工作区、输出 stdout/stderr/vendor event、产生 artifact，并注入非零退出、超时和非法 JSON。

**阶段门禁：** 相同 spec + seed 生成字节一致计划；无效组合预检失败；runner 不能自行新增 episode。

### 执行与证据垂直切片

- 实现 ProcessSupervisor、Recorder、WorkspaceBackend、RawBundle seal、reindex 和一个确定性 scorer。
- 同时通过兼容烟测 `concurrency=1 + carry_forward` 与方法学烟测 `concurrency=1 + independent`。
- 证明失败、超时和解析错误不丢 raw，source workspace 零修改，重新评分不改变 RawBundle hash。

**阶段门禁：** RawBundle 可独立重建索引；未 seal 的 episode 永不显示 completed；项目规定的三次诗题核心 smoke 全部通过。

### 真实 Harness 与多版本

- 先接 Codex CLI exact native provider，再接 Claude Code CLI；随后补预物化 npm provider和声明式 Custom Command。
- 每个 adapter 保存较旧/当前版本的脱敏黄金事件 fixture；离线 parser 测试与真实付费 smoke 分开。
- 增加 OCI provider；只有出现明确研究价值时，再把 Codex Python SDK 作为独立 app-server surface 加入。

**阶段门禁：** Codex 与 Claude 至少各一个 pinned runtime 完成真实最小 smoke；宿主 `PATH` 中安装冲突版本仍执行 manifest 的绝对路径；能力不足明确失败或 raw-only，不静默换 surface/flags。

### 有界并发与故障恢复

- 增加公平调度、多级 semaphore、资源/成本 admission、run cancellation、attempt retry lineage、crash reconcile 和 offline cache。
- 分平台实现并验证进程树终止；高压情况下保证 durable recorder 无 gap。

**阶段门禁：** 100 个 Simulator episode、并发上限 8 时最大活跃进程不超过 8，内存/FD 有界，所有 writer flush/seal；各生命周期强杀后能恢复或标记 orphaned/lost。

### 高级 Prompt、评价与研究导出

- 扩展 Prompt family、item bank、typed variables、变体谱系、matched pair、block、预算预估和 plan export。
- 加入终局、过程、稳定性、效率和安全 scorer，支持盲评/版本化 judge 与 Python/R 统计导出。
- 生态模式和受控模式分开报告；并发、缓存、时间批次和闭源服务漂移成为可追踪条件。

**阶段门禁：** 任一派生结果可追溯到完整数据谱系；事件不被当成独立样本；无法正交的比较标为 `system_condition`。

### 数据迁移与最终切换

- 完成 legacy inventory/validate/import/reindex、两种目录布局、file/Postgres 导出与 run/session/artifact/checksum 对账。
- 完成 Python CLI black-box tests、完整自动化测试、Docker image 构建和使用全新 project/volume 的部署 smoke。
- 更新 README、how-it-works、CHANGELOG `[Unreleased]`、AGENTS/CLAUDE、发布链和迁移指南；应用版本与数据 schema 版本分别设单一真相来源。
- 所有门禁通过并保留明确回滚窗口后，才归档/删除 React、Express、Node simulator、Redis/BullMQ/Postgres 默认路径和旧部署入口。

## 每个源码阶段的统一交付门禁

项目进入实现后，每个产生源码变更的阶段在交付前都必须满足：

- 当前阶段的单元、契约、集成和 CLI black-box 测试通过。
- 默认诗题 `请以“新中国的美人”为题写一首七言绝句。` 使用 `./tmp` 子 Workspace 和 Simulator，按兼容 carry-forward 模式连续运行 3 次并全部 `completed`。
- 每次 episode 都有 transcript、raw stdout/stderr、metadata、artifact、initial/final snapshot、changed files 与 diff；失败注入也有可审计证据。
- Diff 不含用户未提交的 `Prompts.md` 或其它无关修改。
- 重新构建 Python Docker image，并使用独立 Compose project/volume 完成部署 smoke；不得覆盖当前运行中的旧栈。
- 文档、版本、schema migration 和 CHANGELOG 与行为一致。

## 迁移与回滚

### 迁移策略

1. 旧代码、file data、Postgres 导出、`data/runs` 和 volume 分别做只读 inventory/checksum；凭据值和私有环境变量不进入报告。
2. Python 数据根、Compose project、volume 和镜像 tag 使用新名称，不做就地升级。
3. Legacy validator 同时识别 `<run>/<session-id>` 和 `<run>/state-N`，报告路径安全、数量、缺失文件和 checksum。
4. 导入时保留 observed metadata；缺失的 runtime/model snapshot 标为 `unknown`，不推断。Secret-like env 默认不进入新索引。
5. 采用影子验证而非双写：旧系统只读，新运行只写新存储，用 golden fixture 和 Simulator 做行为对账。
6. 自动化、核心 smoke、真实 pinned Harness smoke、legacy 对账、独立 Docker 部署和文档全部通过后，才切换默认入口。

### 回滚策略

- 保留旧 commit/tag、镜像、只读前数据快照、Compose 配置和恢复说明；切换期不做不可逆旧 schema migration。
- 回滚通过重新启用隔离的旧服务并指向旧快照完成，不把新 RawBundle 降级写回旧 store。
- Import count/checksum、核心 smoke、版本契约或数据 seal 任一失败时停止切换，继续修复新栈，不删除旧数据或旧镜像。
- Node 代码最终删除晚于明确回滚窗口，并以迁移报告、仓库外 API consumer 调查和用户签收为前提。

## 如何确认完成

| 领域 | 最低验收 |
| --- | --- |
| 计划确定性 | 相同 frozen spec + seed 生成字节一致 TrialPlan；改变并发不改变条件分配与 baseline hash。 |
| 状态语义 | `concurrency` 与 `state_policy` 独立；carry-forward 和 independent 各有端到端烟测。 |
| 核心 smoke | 默认诗题、一个 `./tmp` Workspace、Simulator、3 次 carry-forward 全完成并保留所有指定记录。 |
| 原始保真 | chunk 边界、半行/超大 JSON、非法 UTF-8、未知事件、解析错误时 raw bytes 可重组，canonical seq 单调。 |
| 完成语义 | 仅 process/protocol/capture/workspace 满足约束时 completed；失败/超时/取消仍可 seal 已有证据。 |
| 工作区 | Source workspace/refs 零写入；independent episode 不可见彼此；carry-forward 只引用 sealed parent。 |
| 版本固定 | Exact resolve、digest/signature mismatch、cache/offline、并发安装锁、平台不支持和 version mismatch 均有测试。 |
| 宿主独立 | 宿主安装冲突版本后仍执行 manifest 绝对路径并记录相同 digest。 |
| Harness 契约 | Codex/Claude 各有旧/新 JSON fixture；各至少一个 pinned runtime 真实 smoke；未知字段不丢。 |
| 有界并发 | 100 Simulator episode、并发 8 时最大进程不超 8，内存/FD 有界，durable event 无 gap。 |
| 取消恢复 | 多层后代可回收；重复取消幂等；强杀后不误杀复用 PID，未 seal 不 completed。 |
| 文件证据 | ignored、异常文件名、rename、binary、symlink、executable、nested Git 和失败后修改正确进入 manifest/diff。 |
| 安全 | Prompt/model/path 的 shell 元字符不产生注入；环境 allowlist；secret 不进入 argv/manifest/framework log；路径逃逸被拒绝。 |
| 重新评分 | Scorer/parser 可升级重算，RawBundle 及其 hash 不变，结果可追溯。 |
| Legacy | 两种 run 布局均可 inventory/import，前后 count/checksum 对账，缺失字段保持 unknown。 |
| 方法学 | Episode 是统计单位；普通重复独立；时间批次/负载可追踪；失败/拒答/格式错不静默删除。 |
| 交付 | 全自动测试、Python build、独立 Docker deploy smoke、文档/CHANGELOG/AGENTS/CLAUDE 一致，旧 volume 未被触碰。 |

## 风险与待确认事项

### 实施前必须盘点

- 本地 file store 的 6 个 runs、Postgres/volume 中的额外记录和必须迁移的范围。
- 仓库外是否存在依赖 `/api/devtools/*` 或 `/api/agent/*` 的消费者；没有证据前不为它们重建新 HTTP 内核。
- Python 首版版本号如何衔接 `package.json` 的 `0.1.1` 与 CHANGELOG 的 `1.0.0`；应用版本与 schema 版本必须分开。
- 首批正式支持的 OS、arch 和 libc，以及 Docker/Podman 是否为确认性实验硬依赖。
- 主要实验采用 ecological、controlled 或两者，进而决定默认配置发现策略。
- Raw transcript 的敏感等级、加密/保留期限、磁盘/费用预算和真实凭据 smoke 的授权范围。
- 第一批评价任务、主要指标、人工盲评资源与 hidden evaluator 的隔离位置。

### 无法彻底消除的风险

- 厂商 flags、事件 schema、stdin 限制、auto-update 和进程树行为会变；必须依靠 capability probe、版本 fixture 和 raw fallback。
- 历史发行物可能下架或缺少校验资料；“任意版本”只对已解析、验证和缓存的版本成立。
- 闭源模型服务端 snapshot、路由和账号策略通常不可冻结；只能记录可见 model ID、请求、日期与批次，并限制结论外推。
- Raw-first 与秘密永不落盘存在真实冲突；默认最小环境和 secret reference 能降低风险，但不能替代数据分类、加密和保留策略。
- Native runtime 不固定 OS，OCI 也不能消除宿主内核、外部网络和供应商服务变化。
- 跨平台进程树、文件系统和 Git 语义不同，Windows/macOS/Linux 的关键路径必须分别验证。

## 技术补充（按需阅读）

建议的 Python 包边界如下，实际文件名可以在实现时按现有风格微调，但职责不能重新耦合：

```text
src/llm_status_machine/
  cli/              # 命令与展示，不承载业务规则
  domain/           # Study、Plan、Episode、Attempt、Outcome 等稳定模型
  prompts/          # revision、render、transform、compiler
  harnesses/        # Adapter 协议与 Codex/Claude/Custom 实现
  runtimes/         # native/npm/OCI/unmanaged provider 与 cache
  execution/        # scheduler、supervisor、cancel、recovery
  workspaces/       # baseline、materialize、manifest、git evidence
  recording/        # raw stream、vendor/canonical events、bundle seal
  storage/          # SQLite index、object store、migration、reindex
  evaluation/       # parser、scorer、annotation、comparison、export
  legacy/           # 只读 inventory、validator、importer
```

建议的新结果根目录保持自描述：SQLite 只是索引，run/episode manifest 和 RawBundle 足以在数据库丢失后执行 `reindex`。所有 metadata 使用临时文件 + fsync + 原子 rename，结束时生成 seal/hash；大对象以内容哈希去重，GC 只删除没有引用且超过保留期的对象。

## 调研与设计依据

- 本地报告：`/Volumes/2T01/winE/PythonCloud/Agents/pipelines/deep_research/reports/Codex-Claude-Code-非交互命令行模式/`。
- 本地报告：`/Volumes/2T01/winE/PythonCloud/Agents/pipelines/deep_research/reports/LLM行为实验方法学调研/`。
- OpenAI Codex 非交互模式：<https://developers.openai.com/codex/noninteractive>。
- OpenAI Codex SDK：<https://developers.openai.com/codex/sdk>。
- Anthropic Claude Code headless：<https://code.claude.com/docs/en/headless>。
- Anthropic Claude Code setup：<https://code.claude.com/docs/en/setup>。
- MacBehaviour：<https://doi.org/10.3758/s13428-024-02524-y>。
- SWE-bench：<https://doi.org/10.48550/arxiv.2310.06770>；SWE-agent：<https://doi.org/10.48550/arxiv.2405.15793>。
- AgentBoard：<https://doi.org/10.52202/079017-2365>；AgentDojo：<https://doi.org/10.52202/079017-2636>。
- PromptRobust：<https://doi.org/10.1145/3689217.3690621>；POSIX：<https://doi.org/10.18653/v1/2024.findings-emnlp.852>。
- Inspect AI：<https://github.com/UKGovernmentBEIS/inspect_ai>；EvalScope：<https://github.com/modelscope/evalscope>。

正式实现开始前，应再次核验 Codex/Claude 的最新 CLI 发行和协议文档，并把核验日期与选定版本写入 runtime manifest；本文中的具体 flags 不应被当成跨版本永久承诺。
