# LLM Status Machine

LLM Status Machine 是一个 Python 3.12+ 本地命令行实验台。它把一次从明确 workspace baseline 开始的完整 Harness 调用视为一个 episode，固定实际 CLI runtime，并把 Prompt、原始输出、事件、工作区前后状态、Git diff、artifact、outcome 和版本指纹封存为可校验的 RawBundle。

项目首版专注单机 CLI，不再依赖 React、Express、Node.js、Postgres、Redis 或 BullMQ。

## 快速开始

```bash
uv sync --frozen --extra test
uv run lsm init .
uv run lsm study validate study.example.yml
uv run lsm study compile study.example.yml .lsm/plans/example.jsonl --json
uv run lsm run start .lsm/plans/example.jsonl --data-root .lsm --json
```

直接运行核心验收：

```bash
uv run pytest
uv run lsm smoke --root tmp/core-smoke-manual --json
```

smoke 使用 Simulator、默认诗题和 `tmp` 下的 source workspace，以 `concurrency=1 + carry_forward` 连续运行 3 次。每个 episode 都会生成 transcript、raw stdout/stderr、metadata、artifact、初始/最终 manifest、Git commit、changed files、binary diff 和 seal。

## 领域边界

```text
StudySpec -> compile/freeze -> TrialPlan -> Run -> Episode -> Attempt -> RawBundle
RawBundle -> parser/scorer -> versioned evaluation/export
```

- `concurrency` 只控制同时运行数。
- `state_policy` 独立控制 workspace 拓扑：普通重复默认 `independent`；明确的状态传递使用 `carry_forward`。
- `HarnessSurface`、`RuntimeBuild`、`ModelEndpoint`、`ExecutionProfile` 与 `WorkspaceFixture` 分开配置。
- 计划只能引用绝对 runtime 路径及冻结的 SHA-256；执行前会重新校验。
- 自定义 Harness 使用 argv 数组和 `create_subprocess_exec`，不接受 shell 模板。
- source workspace 永不直接执行或初始化 Git。
- 评分写在 bundle 外，不能改写 sealed RawBundle。

详细过程见 [docs/how-it-works.md](docs/how-it-works.md)，架构裁决见 [docs/adr/0001-python-cli-core.md](docs/adr/0001-python-cli-core.md)。

## CLI

主要命令组：

- `lsm init` / `lsm doctor`：初始化项目并检查运行条件。
- `lsm harness`：列出 adapter、probe、锁定或下载 exact runtime。
- `lsm prompt`：lint、render 和 freeze Prompt revision。
- `lsm workspace snapshot`：生成可复核 baseline manifest。
- `lsm study`：validate、estimate 和 compile 稳定 JSONL TrialPlan。
- `lsm run`：执行冻结计划、查询状态和 reconcile 中断的 run。
- `lsm episode`：读取事件、stdout、diff，并验证 bundle seal。
- `lsm evaluate`：在不改写原始证据的前提下重评分。
- `lsm export`：导出 JSONL、CSV 或可携带 tar.gz。
- `lsm store`：从自描述 run/episode manifest 重建 SQLite 索引并校验证据。
- `lsm legacy`：只读盘点、验证和导入两种旧 run 目录布局。

所有状态查询和自动化入口均提供稳定的 `--json` 输出。

## StudySpec 示例

`lsm init` 会生成可直接运行的 `study.example.yml`。关键字段如下：

```yaml
schema_version: 1
name: simulator-example
seed: 0
repeats: 3
concurrency: 1
state_policy: carry_forward
design: full_factorial
prompts:
  - id: poem
    body: 请以“新中国的美人”为题写一首七言绝句。
workspace:
  path: /absolute/path/to/workspace
runtime:
  provider: simulator
  surface: simulator
  requested: builtin
  version: "3.12.x"
  executable: /absolute/path/to/python
  sha256: <frozen executable digest>
  platform: darwin-arm64
  version_output: Python 3.12.x
endpoint:
  model_id: simulator
  provider: local
profile:
  config_mode: workspace_native
  research_mode: ecological
  timeout_seconds: 120
```

同一 frozen spec、workspace baseline 与 seed 会得到字节一致的 `plan.jsonl`。`latest`、宿主 `PATH` 或未展开矩阵不会进入可执行计划。

## Harness runtime

内置 surface：

- `simulator`
- `codex_exec_cli`
- `claude_print_cli`
- `custom_command`

先把宿主 CLI 解析为绝对路径和精确版本，再将 runtime manifest 放入 StudySpec：

```bash
uv run lsm harness lock \
  --surface codex_exec_cli \
  --executable /absolute/path/to/codex \
  --version 0.144.0 \
  --output .lsm/codex-0.144.0.json
```

`unmanaged` runtime 会显式标记不可完全复现。`harness fetch` 支持 URL + SHA-256 的内容寻址缓存和 offline 缺失即失败；OCI runtime identity 可在 schema 中记录 digest，但容器隔离能力仍取决于实际 user、mount、network 和 capability 配置。

凭据只通过 `profile.env_allowlist` 引用宿主环境变量名；计划、launch metadata 和日志不保存 secret 值。

## 数据布局

```text
.lsm/
  index.sqlite3
  plans/
  runs/<run-id>/
    run.json
    episodes/<episode-id>/
      episode.json
      workspace/
      attempts/attempt-1/raw-bundle/
      evaluations/
```

SQLite 仅作 WAL 索引；删除索引后可用 `lsm store reindex` 从文件系统 manifest 重建。未 seal 或四类 execution outcome 未全部满足约束的 episode 永不标记为 `completed`。

## Legacy 迁移

旧 `data/` 和运行中的旧 Docker volumes 不做就地升级：

```bash
uv run lsm legacy inventory data --json
uv run lsm legacy validate data --json
uv run lsm legacy import data --data-root .lsm --json
```

importer 同时识别 `<run>/<session-id>` 和 `<run>/state-N`，保留 observed metadata；缺失 runtime/model 保持 `unknown`。详见 [docs/migration-from-node.md](docs/migration-from-node.md)。

## 开发与发布

```bash
make sync
make test
make smoke
make build
```

应用版本只在 `src/llm_status_machine/version.py` 维护；RawBundle schema version 与应用版本分离。项目只提供本地 Python CLI，不维护容器镜像或容器发布链路。
