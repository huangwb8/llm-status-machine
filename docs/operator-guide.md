# LSM 操作者手册

这份手册同时面向两类操作者：

- 人类研究者或开发者：按命令创建、运行和复核实验；
- AI coding agent：在受限工作区内安装 LSM、编写标准测试、运行验证并报告可复核证据。

它是一份可执行的操作指南。架构细节见 [`docs/how-it-works.md`](how-it-works.md)，标准实例目录契约见 [`docs/architecture/instance-package.md`](architecture/instance-package.md)。

## 先记住四个对象

LSM 把“写实验”和“跑实验”分开：

| 对象 | 作用 | 是否应该手工修改 |
| --- | --- | --- |
| `StudySpec`（YAML） | 描述 Prompt、workspace、runtime、重复次数和实验设计 | 可以修改，修改后重新校验和编译 |
| `TrialPlan`（JSONL） | `StudySpec` 的冻结版本，runner 只执行它 | 不要手工修改；修改 Study 后重新编译 |
| `Run` / `Episode` | 一次计划执行及其中的实验单位 | 由 LSM 生成，通过 CLI 查询 |
| `RawBundle` | Prompt、原始 stdout/stderr、transcript、快照、diff、artifact 和 seal | 不要改写；评分只能产生外部派生结果 |

标准链路是：

```text
StudySpec → validate → compile TrialPlan → run start → Episode/RawBundle → validate seal
```

不要用“直接运行 agent 后检查最终文件”替代这条链路。那样无法证明 runtime、Prompt、workspace 基线和原始输出是什么。

## 安装一次，实验目录分开

LSM 每台电脑安装一次即可；实验目录可以放在任意可写位置，不需要放在 LSM 源码根目录。

### 推荐：从源码仓库安装

```bash
git clone <仓库地址> llm-status-machine
cd llm-status-machine

python3.12 --version
uv --version

# 项目默认把环境和工具缓存放到 .bensz-api/。
make sync
source .bensz-api/.venv/bin/activate

lsm --help
```

如果没有 `make`：

```bash
export UV_PROJECT_ENVIRONMENT=.bensz-api/.venv
uv sync --frozen
source .bensz-api/.venv/bin/activate
```

Windows PowerShell 可以使用：

```powershell
$env:UV_PROJECT_ENVIRONMENT = ".bensz-api/.venv"
uv sync --frozen
.bensz-api\.venv\Scripts\Activate.ps1
```

激活环境后，`lsm` 可以从任意工作目录调用。若使用 `uv run lsm`，应在 LSM 仓库根目录执行，或改用已激活环境中的 `lsm` 命令。

### 可选：安装 wheel

不希望目标电脑保留源码仓库时，可以在一台构建机生成 wheel，再把 `dist/*.whl` 传到目标电脑：

```bash
uv build
uv tool install dist/llm_status_machine-*.whl
lsm --help
```

wheel 或仓库安装只解决 LSM 本身。真实 Codex/Claude runtime、Git 和模型凭据仍需在目标电脑单独准备。

## 选择实验工作流

先判断你要做哪一种工作：

| 目标 | 入口 | 适合场景 |
| --- | --- | --- |
| 快速验证 LSM 或写一个普通实验 | `lsm init <目录>` | Simulator、一次性探索、最小实验 |
| 创建可发现、可校验、可复用的实验实例 | `lsm example init <目录>` | 提交到仓库的研究、集成测试、benchmark |
| 验收 LSM 安装是否正常 | `lsm smoke --root <新目录>` | 安装后或代码交付前的核心冒烟 |

实验根目录、LSM 源码目录和被测 workspace 可以完全不同：

```text
/opt/llm-status-machine/       # LSM 安装
~/lsm-experiments/poem-001/    # 实验根目录，含 .lsm/
~/projects/my-app/             # 被测 source workspace
```

## 最短可复现路径：Simulator

Simulator 不需要 API Key，是 AI 自动安装后的第一条验证路径。

```bash
lsm smoke --root /absolute/path/to/tmp/core-smoke-other-pc --json
```

`--root` 必须指向尚不存在的目录。成功标准不是“命令启动了”，而是 JSON 中 run 为 `completed`，并且 3 个连续 episode 都完成、证据已封存。

也可以创建一个可继续编辑的实验目录：

```bash
lsm init ~/lsm-experiments/quickstart
cd ~/lsm-experiments/quickstart

lsm study validate study.example.yml --json
lsm study compile \
  study.example.yml \
  .lsm/plans/first-plan.jsonl \
  --json

lsm run start \
  .lsm/plans/first-plan.jsonl \
  --data-root .lsm \
  --json
```

初始化的样例默认使用 `concurrency: 1` 和 `state_policy: carry_forward`，会连续运行 3 个 episode。编译后不要随意移动实验目录、修改 workspace 或替换 runtime；这些路径和摘要已经成为冻结计划的一部分。

## 创建标准实验实例

标准实例适合让 AI 持续维护，也适合提交到 Git。创建和验证：

```bash
lsm example init ~/lsm-experiments/my-study \
  --id my-study \
  --kind study \
  --json

lsm example validate ~/lsm-experiments/my-study --json
```

实例根目录至少包含：

```text
my-study/
├── lsm.yml
├── README.md
├── prompts/
├── fixture/
├── harness/
├── oracle_tests/
├── scripts/
└── results/
```

各目录的职责、路径 containment 规则和 `scripts/smoke.py` 契约见 [`instance-package.md`](architecture/instance-package.md)。实例自己的 smoke 必须至少经过 `StudySpec → TrialPlan → RunEngine`，并检查 `completed` episode 和有效 seal；独立业务脚本不算标准 LSM 测试。

## 编写标准 LSM 测试

当 AI 或人类为 `examples/` 添加测试时，测试对象应是 LSM 的可观测行为，而不是只断言业务文件最终内容。最小覆盖应包括：

- 固定 Prompt、fixture、runtime、`concurrency` 和 `state_policy`；
- 通过 `StudySpec → TrialPlan → RunEngine` 运行；
- 检查 run/episode 状态；
- 检查适用的 RawBundle 文件和 seal；
- 使用新的临时输出根，避免读取上一次运行的残留。

下面是标准实例测试的骨架，可放在 `tests/` 中并按实际实例路径调整：

```python
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from llm_status_machine.recording.bundle import resolve_bundle_path, validate_seal
from llm_status_machine.utils import read_json


ROOT = Path(__file__).resolve().parents[1]


def test_instance_smoke_produces_one_sealed_episode(tmp_path: Path) -> None:
    instance = ROOT / "examples/my-study"
    output = tmp_path / "smoke-output"

    subprocess.run(
        [sys.executable, str(instance / "scripts/smoke.py"), "--root", str(output)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )

    episode_paths = list((output / "data/runs").glob("*/episodes/*/episode.json"))
    assert len(episode_paths) == 1
    episode_path = episode_paths[0]
    episode = read_json(episode_path)
    seal_valid, seal_errors = validate_seal(
        resolve_bundle_path(episode_path.parent, episode)
    )

    assert episode["status"] == "completed"
    assert seal_valid, seal_errors
```

如果测试需要验证更完整的证据，可以继续断言 RawBundle 中存在 `prompt.md`、`stdout.raw`、`stderr.raw`、`transcript.jsonl`、`metadata.json`、`outcomes.json`、workspace 快照、`diff.patch`、`artifacts.json` 和 `seal.json`。不要把模型自然语言内容当作唯一断言，也不要把同一 episode 内的 subagent、token 或测试用例当成独立样本。

## 接入真实 runtime

真实 runtime 必须先 probe，再 lock。不要依赖运行时的 `PATH` 自动选择版本：

```bash
lsm harness probe /absolute/path/to/codex --json

lsm harness lock \
  --surface codex_exec_cli \
  --executable /absolute/path/to/codex \
  --version 0.144.0 \
  --output /absolute/path/to/study/.lsm/codex-0.144.0.json
```

把 lock 输出的完整 runtime 对象填入 `StudySpec` 后，再执行 `validate` 和 `compile`。Claude 使用 `claude_print_cli`；其他程序使用 `custom_command`，但必须是明确的 argv 数组，不能写 shell 模板。

在新电脑上通常必须重新 lock，因为以下内容可能变化：

- 可执行文件绝对路径；
- 版本和 SHA-256；
- 操作系统和架构；
- workspace 绝对路径。

凭据只在目标电脑的环境变量中提供。`profile.env_allowlist` 只记录变量名，不要把 API Key、Cookie 或配置文件内容写进 StudySpec、计划或日志。

## 运行后如何取证

所有自动化调用优先使用 `--json`，保存命令输出中的实际 `run_id` 和 `episode_id`。随后用这些 ID 查询，不要猜目录名：

```bash
lsm run status <run-id> --data-root /absolute/path/to/study/.lsm --json
lsm episode validate <episode-id> --data-root /absolute/path/to/study/.lsm --json
lsm episode events <episode-id> --data-root /absolute/path/to/study/.lsm
lsm episode stdout <episode-id> --data-root /absolute/path/to/study/.lsm
lsm episode diff <episode-id> --data-root /absolute/path/to/study/.lsm
```

`episode validate` 通过才说明 seal 对应的文件摘要仍然一致。`events` 是解析后的 transcript；要查看严格的原始进程输出，使用 `episode stdout`。`.lsm/` 可能包含 Prompt、模型输出和 workspace 内容，应按敏感实验数据管理，不要默认提交或上传。

常见结果目录如下：

```text
<study>/.lsm/
├── plans/
└── runs/<run-id>/
    ├── run.json
    └── episodes/<episode-id>/
        ├── episode.json
        ├── workspace/
        └── attempts/attempt-1/raw-bundle/
            ├── stdout.raw
            ├── stderr.raw
            ├── transcript.jsonl
            ├── workspace.initial.json
            ├── workspace.final.json
            ├── changed-files.json
            ├── diff.patch
            └── seal.json
```

需要评分或研究分析时，按冻结计划继续执行：

```bash
lsm evaluate run <run-id> --data-root .lsm --json
lsm research dataset <run-id> --data-root .lsm --json
lsm research infer <run-id> --data-root .lsm --json
lsm research report <analysis-id> --data-root .lsm --json
```

评分和推断只能读取 sealed evidence 及其冻结的计划声明，不应直接遍历或修改现场 workspace。

## AI 操作协议

AI agent 开始任务时，按以下顺序执行：

### 开始前

- 确认当前工作目录、Python 版本、Git 和 `lsm --help`；
- 确认实验输出根目录尚不存在，或使用一个带唯一标识的新目录；
- 默认使用 Simulator，不因“想测试模型”而自动读取凭据；
- 区分 LSM 源码目录、实验根目录和被测 workspace；
- 若是 `examples/` 变更，先阅读该实例 README、`lsm.yml` 和现有 smoke。

### 执行中

- 先 `study validate`，再 `study compile`，最后 `run start`；
- 保存 JSON 输出中的 ID，并等待 run 进入终态；
- `carry_forward` 必须配合 `concurrency=1`；
- 不手工改写 TrialPlan、RawBundle、seal 或评分输入；
- 不把 shell、API Key、Cookie 或整个外部凭据目录放入 Prompt、argv、artifact 或日志；
- 遇到失败时保留现场，先检查 `run status`、`episode validate`、outcomes 和 raw streams，不要删除后重跑覆盖证据。

### 交付前

- 运行相关的 pytest；
- 运行 Python 构建：`make build` 或 `uv build`；
- 运行核心冒烟：`lsm smoke --root <全新目录> --json`；
- 对每个实验 episode 验证 seal；
- 报告修改文件、命令、run/episode ID、状态和验证结果；
- 不把 `.lsm/`、模型输出或凭据写入提交内容，除非用户明确要求并已脱敏。

项目级标准是：默认诗题 Prompt、`concurrency=1`、`state_policy=carry_forward` 连续完成 3 个 episode，并保留完整 transcript、raw stdout/stderr、artifact/metadata、workspace 初末快照、Git commit、changed files、diff 和 seal。

## 常见问题

| 现象 | 优先检查 |
| --- | --- |
| `lsm: command not found` | 是否激活安装环境；或是否应在 LSM 仓库根目录运行 `uv run lsm` |
| `already initialized` | 目标目录已有 `.lsm/`；换一个新目录，或确认后显式使用 `--force` |
| `source workspace changed after plan freeze` | 编译后修改了 workspace；恢复原基线或重新编译计划 |
| runtime digest/version 不匹配 | 目标电脑的 executable 已更新、路径改变或需要重新 lock |
| `carry_forward requires concurrency=1` | 将 `concurrency` 改为 1，或改用 `independent` / `branch` |
| run 长时间保持 `running` | 先停止继续派发，检查进程和日志；进程已中断时运行 `lsm run reconcile`，不要猜测成功 |
| seal 验证失败 | 保留 RawBundle，检查是否有文件被手工修改、删除或出现非法链接；不要覆盖原 bundle |
| 真实 Codex/Claude 无法启动 | 检查 CLI、绝对 executable、版本、`CODEX_HOME` 和允许的环境变量；先用 Simulator 排除 LSM 本身问题 |

## 相关入口

- [README 快速开始](../README.md#快速开始先跑一个无需密钥的实验)
- [LSM 工作过程](how-it-works.md)
- [LSM 实例包契约](architecture/instance-package.md)
- [研究证据与溯源](architecture/research-provenance.md)
- [`examples/` 中的完整实验](../examples/)
