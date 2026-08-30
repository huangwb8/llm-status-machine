# LLM Status Machine

**把一次 LLM CLI 运行变成可以复盘、比较和核验的实验。**

LLM Status Machine（`lsm`）是一个 Python 3.12+ 本地命令行实验台，面向开发者、研究者和评估工程师。它把 Prompt、模型端点、可执行 runtime 和 workspace 一起冻结，批量运行 LLM CLI，并保存足以复核每次行为的原始证据。

它适合回答这类问题：

- 两次运行是否真的只改变了 Prompt？
- agent 修改了哪些文件，失败或超时时还留下了哪些证据？
- 连续运行是否继承了上一轮 workspace？
- 几周后能否证明当时使用的是哪一个 runtime 和哪一份输入？

LSM 不是聊天界面、云端控制台或完整安全沙箱。它专注于有副作用的 LLM CLI 实验：不仅记录模型说了什么，也记录它在隔离 workspace 中做了什么。

## 快速开始

下面使用内置 Simulator，不需要模型密钥。前置条件是 Python 3.12+、[uv](https://docs.astral.sh/uv/) 和 Git。

```bash
# 在项目根目录安装依赖
make sync

# 创建一个独立实验目录，并生成 StudySpec 示例
uv run lsm init tmp/quickstart

# 校验并冻结执行计划
uv run lsm study validate tmp/quickstart/study.example.yml --json
uv run lsm study compile \
  tmp/quickstart/study.example.yml \
  tmp/quickstart/.lsm/plans/first-plan.jsonl --json

# 执行冻结计划
uv run lsm run start \
  tmp/quickstart/.lsm/plans/first-plan.jsonl \
  --data-root tmp/quickstart/.lsm --json
```

初始化的样例会以 `concurrency: 1` 和 `state_policy: carry_forward` 连续运行 3 个 episode。命令输出中的 JSON 会给出实际的 `run_id` 和 `episode_id`，用它们查询结果：

```bash
uv run lsm run status <run-id> --data-root tmp/quickstart/.lsm --json
uv run lsm episode validate <episode-id> --data-root tmp/quickstart/.lsm --json
uv run lsm episode events <episode-id> --data-root tmp/quickstart/.lsm
uv run lsm episode diff <episode-id> --data-root tmp/quickstart/.lsm
```

`episode validate` 通过表示 RawBundle 的 seal 与文件摘要一致。只想验证安装是否正常，也可以直接运行核心冒烟：

```bash
uv run lsm smoke --root tmp/core-smoke-manual --json
```

`--root` 必须指向一个尚不存在的目录。成功标准是 run 为 `completed`，并且 3 个连续 episode 都完成并封存证据。

如果直接使用 `uv` 而不是项目的 Makefile 入口，请在项目根目录设置工具状态目录：

```bash
export UV_PROJECT_ENVIRONMENT=.bensz-api/.venv
export HYPOTHESIS_STORAGE_DIRECTORY=.bensz-api/.hypothesis
```

## 你会得到什么

实验按以下链路执行：

```text
StudySpec → validate/compile → frozen TrialPlan → Run/Episode
                                      ↓
                              sealed RawBundle
                                      ↓
                         evaluation → dataset → inference
```

每个 episode 的 RawBundle 至少包含：

- 实际 Prompt、runtime 和 launch metadata；
- 原始 `stdout.raw` / `stderr.raw` 与解析后的 `transcript.jsonl`；
- workspace 初始/最终快照、Git commit、changed files 和 diff；
- artifact、四类 outcome、metadata 和 `seal.json`。

评分、数据集和推断写入 RawBundle 之外的派生目录，不改写已经封存的原始证据。

## 常见入口

| 目标 | 命令或文档 |
| --- | --- |
| 安装、创建实验、运行和排错 | [LSM 操作者手册](docs/operator-guide.md) |
| 理解为什么这样设计 | [设计原则与取舍](docs/architecture/design-principles.md) |
| 了解内部执行流程 | [工作过程说明](docs/how-it-works.md) |
| 创建或维护标准实验实例 | [LSM 实例包契约](docs/architecture/instance-package.md) |
| 理解评分、数据集和统计推断 | [研究证据与溯源](docs/architecture/research-provenance.md) |
| 查看可复现实例 | [`examples/`](examples/) |
| 查看核心架构决策 | [Python CLI ADR](docs/adr/0001-python-cli-core.md) |

第一次接触项目时，建议按“快速开始 → 操作者手册 → 具体示例”的顺序阅读。想了解系统取舍，再阅读设计原则和工作过程说明。

## 接入真实 CLI

真实 Codex、Claude Code 或其他 CLI 需要先锁定可执行文件，再把完整 runtime 对象填入 StudySpec：

```bash
uv run lsm harness probe /absolute/path/to/codex --json
uv run lsm harness lock \
  --surface codex_exec_cli \
  --executable /absolute/path/to/codex \
  --version 0.144.0 \
  --output .lsm/codex-0.144.0.json
```

内置 adapter 包括 `simulator`、`codex_exec_cli`、`claude_print_cli` 和 `custom_command`。自定义命令使用明确的 argv 数组，不经过 shell；凭据只通过环境变量名称 allowlist 传入，不会写入计划或日志。完整流程见[操作者手册](docs/operator-guide.md)。

## 标准实验实例

需要提交到仓库、持续维护或供 AI 自动验证的实验，使用标准实例包：

```bash
uv run lsm example init examples/my-study --id my-study --kind study --json
uv run lsm example validate examples/my-study --json
```

实例源码与 `.lsm/`、`tmp/` 下的运行数据分离。目录职责、`lsm.yml` 和单 episode smoke 要求见[实例包契约](docs/architecture/instance-package.md)。

## 开发与验证

```bash
make sync
make test
make smoke
make build
```

应用版本只在 [`src/llm_status_machine/version.py`](src/llm_status_machine/version.py) 维护；RawBundle schema version 与应用版本彼此独立。

## 数据与安全边界

- `.lsm/` 可能包含 Prompt、模型输出和 workspace 内容，应按本地实验数据管理，不要默认提交或上传。
- LSM 会隔离 source workspace 与 episode 副本，但 native runtime、`uv` 和 CLI 自带 sandbox 不等于完整安全隔离。
- 自定义 runtime 不经过 shell，计划会固定绝对 executable、版本和 SHA-256；高风险实验仍需操作者自行控制用户、网络、权限和资源边界。

## 许可证

本项目使用 MIT License，许可证元数据维护在 [`pyproject.toml`](pyproject.toml)。
