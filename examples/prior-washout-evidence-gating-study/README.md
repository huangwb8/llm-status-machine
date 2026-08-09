# 错误先验洗脱、记忆中介与证据门控实验

这个示例把 [`docs/plans/prior-washout-evidence-gating-study.md`](../../docs/plans/prior-washout-evidence-gating-study.md) 落成两条相互隔离的路径：无需模型的确定性基础设施资格实验，以及每个 episode 启动三个全新 Codex CLI 进程的真实 A → B → C 行为协议。两条路径共用五个预注册实验臂、六个符号回归任务、RawBundle、Git 快照、seal、盲化评分和 episode 级推断，但资格轨迹绝不进入真实模型结论。

资格 Harness 不调用 LLM，而是产生固定候选轨迹，因此资格结果只能证明软件链路能正确区分已知轨迹。真实路径由外部 orchestrator 在一个 episode 内依次启动三个 `codex exec --ephemeral` 进程，并把阶段身份、原始流、候选和状态转换写入同一个 sealed episode；统计单位仍是 episode，而不是 stage 或候选。

## 实验设计

一个 episode 是一条完整的 A → B → C 轨迹，统计推断单位始终是 episode：

```mermaid
flowchart LR
    P[五臂单模板 Prompt] --> C[稳定 TrialPlan\n25 个独立 episode]
    C --> A[阶段 A\n先验诱导]
    A --> M{状态转换}
    M -->|open| B[阶段 B\n洗脱与验证]
    M -->|gated| B
    M -->|purged| B
    B --> D[阶段 C\n恢复与提交]
    D --> R[RawBundle + Git + Seal]
    R --> S[盲化 symbolic oracle]
    S --> Q[Dataset → Inference → Report]
```

五臂为 `neutral-open`、`correct-open`、`false-open`、`false-gated` 和 `false-purged`。Prompt 都从 `prompts/prompt-template.md` 物化，只允许 `PRIOR` 与 `MEMORY` 两个标记区域不同。确认性资格计划使用 `seed=20260808`、`repeats=5`、`concurrency=1`、`state_policy=independent`，生成 25 个 episode；每个 arm 在 comparison set 的位置 1–5 各出现一次。

主要资格指标 IFO-AUC 是阶段 B/C 中仍落在已知错误候选上的比例。隐藏 scorer 还计算结构恢复率、远端 OOD-NMSE 和协议完整性。三个预注册 contrast 进入同一个 Holm family。固定轨迹应让 `false-open` 保持错误 motif，而 `false-gated` 和 `false-purged` 脱离；这是 scorer 与 inference 的 golden signal，不是待发表的模型效应。

## 真实 Codex 协议

真实运行入口是 `scripts/run_real_study.py`。它只接收项目外已有的 `CODEX_HOME` 环境引用，不复制或写入 `auth.json`、`config.toml`、API key、Cookie 或外部配置路径。nested Codex 使用 pinned executable digest、明确模型和 reasoning effort，并通过 Permission Profile 把模型命令限制到最小运行库和当前 stage workspace；命令网络与 web search 均禁用。

每个 episode 固定执行：

- A：只看训练观测和分配到的先验，产生每任务三个候选；
- B：使用全新进程和 thread，加入独立验证观测，并按 `open`、`gated` 或 `purged` 重建状态；
- C：再次使用全新进程和 thread，完成恢复与最终提交；
- 结束后记录 54 条候选、三个 PID/thread、raw stdout/stderr、workspace snapshot、Git commit/diff、seal 和重复盲评。

```bash
export CODEX_HOME=/path/outside/this/repository

uv run python examples/prior-washout-evidence-gating-study/scripts/run_real_study.py \
  --mode shakedown \
  --root tmp/prior-washout-real-shakedown \
  --model gpt-5.6-sol \
  --reasoning-effort low \
  --stage-timeout 900
```

`--mode pilot` 默认运行每臂 5 个 episode；`--mode confirmatory` 默认每臂 35 个。输出根必须尚不存在。含失败 episode 的 run 会继续执行 seal、盲评和 dataset 构建，失败值按预注册 intention-to-treat 规则处理，不自动 retry。

## 真实 Pilot 结果

2026-08-09 完成的探索性 pilot 使用 `gpt-5.6-sol`、low reasoning、`seed=20260811`、`concurrency=1` 和 900 秒 stage timeout。25 个 episode 全部封存，24 个 completed，1 个 `neutral-open` 在 C 阶段 timeout；74/75 个 stage 完成，25 个 seal 全部有效，重复 symbolic oracle 评分一致性为 1.0，凭据边界扫描为 0 命中。

描述性 IFO-AUC 对比为：`false-open − neutral-open = -0.1722`、`false-gated − false-open = -0.0278`、`false-purged − false-open = -0.0389`，三个 Holm-adjusted p 值均为 0.8571。neutral 对比被 timeout 的最坏值明显牵引；这些结果只用于方差、失败率和成本冻结，不能回答确认性研究问题。完整脱敏摘要见 [`results/real-codex-pilot-20260809-summary.json`](results/real-codex-pilot-20260809-summary.json)。

正式样本量按预注册 `MDE=0.25`、双侧 `alpha=0.05/3`、power 0.80 和 `sigma_used=max(0.30, pilot pooled SD 的单侧 95% 上界)` 计算。pilot pooled SD 为 0.1824，上界为 0.2477，因此 `sigma_used=0.30`；power 结果为 31/臂，按五臂 sequence-position 平衡上取整为 35/臂，即 175 episode、525 次 Codex 进程。正式运行在显式 token/费用上限获批前保持停止。

## 一键资格测试

从仓库根目录运行：

```bash
uv sync --frozen --extra test --extra analysis
uv run python examples/prior-washout-evidence-gating-study/scripts/run_qualification.py \
  --root tmp/prior-washout-qualification
```

输出根必须尚不存在。脚本会依次执行：

- doctor、Harness list/probe/lock；
- fixture 生成、Prompt lint/freeze、workspace snapshot；
- Study validate/estimate/power、两次稳定编译与计划门禁；
- 25 个串行 independent custom-command episode；
- 每个 seal 验证、execution-integrity 与重复 blind command scoring；
- research dataset/infer/report；
- archive/JSONL/CSV export；
- index 备份后 reindex，以及前后 store verify；
- partial JSON、未知事件、非法 UTF-8、大流和 timeout recorder 资格场景。

最终入口是 `tmp/prior-washout-qualification/qualification-summary.json`。完整 run、RawBundle、评分和报告保留在同一临时根中，不提交 Git。

## 分步运行

如需观察每个 CLI 边界，可先锁定当前 Python 3.12 custom runtime：

```bash
PYTHON_BIN="$(uv run python -c 'import sys; print(sys.executable)')"
PYTHON_VERSION="$(uv run python -c 'import platform; print(platform.python_version())')"

uv run lsm harness lock \
  --surface custom_command \
  --executable "$PYTHON_BIN" \
  --version "$PYTHON_VERSION" \
  --output tmp/prior-washout-runtime.json

uv run python examples/prior-washout-evidence-gating-study/scripts/build_fixture.py
uv run python examples/prior-washout-evidence-gating-study/scripts/prepare_study.py \
  --runtime tmp/prior-washout-runtime.json \
  --output tmp/prior-washout-study.yml
uv run lsm study compile tmp/prior-washout-study.yml tmp/prior-washout-plan.jsonl --json
uv run python examples/prior-washout-evidence-gating-study/scripts/verify_plan.py \
  tmp/prior-washout-plan.jsonl
uv run lsm run start tmp/prior-washout-plan.jsonl \
  --data-root tmp/prior-washout-data --json
```

随后使用输出的 `run_id` 执行 `lsm evaluate run`、`lsm research dataset`、`lsm research infer` 和 `lsm research report`。一键脚本是这组命令的可审查实现。

## 数据与安全边界

- episode workspace 只包含训练/验证观测和 AST 白名单 evaluator；结构真值与 OOD 点只作为 pinned scorer support file，在运行结束后的只读 sealed snapshot 评分阶段提供。
- expression grammar 只允许有界数字、`x`、算术运算和 `sin/cos/exp/log`；同时限制文本长度、AST 节点数与常数幂指数，拒绝属性访问、导入、下标、反射、任意调用和明显的资源耗尽表达式。
- `symbolic_oracle` 设置 `include_prompt: false`，评分 manifest 不接收 arm、condition、ordinal 或 Prompt；评分程序只读 sealed final commit，并在评分后复核 workspace digest。
- `.lsm/`、runtime lock、RawBundle、导出、凭据和本地结果默认忽略。不要把 `auth.json`、`config.toml`、API key 或 Cookie 放进示例目录。
- 资格 runner 内置答案是显式的 trusted infrastructure test double。`custom_command` 当前不提供网络或 OS 权限沙箱，因此资格 profile 如实记录 `network=inherit`；脚本自身不执行网络操作。真实模型 pilot 不得使用它，也不得把其固定 effect 当作先验研究证据。

## 主要功能覆盖

| 能力 | 资格证据 |
| --- | --- |
| Prompt lint/render/freeze | 单模板物化五臂，规范化摘要一致，冻结文件有 SHA-256 |
| Harness/runtime | custom surface 可发现；Python 绝对路径、版本、platform 与 digest 锁定 |
| Workspace | 编译时 baseline、每 episode initial/final manifest、Git commit、changed files、diff |
| Study | validate、estimate、power、稳定 JSONL compile、平衡随机化 provenance |
| Run | 25 个串行 independent episode，planned/actual dispatch 可核对 |
| Recording | JSONL event、raw stdout/stderr、artifact、四类 outcome、失败与 timeout seal |
| Evaluation | execution-integrity + prompt-blind pinned command scorer，各重复两次 |
| Research | 一行一个 episode、三个预注册 contrast、bootstrap、置换检验、Holm、报告 |
| Store/export | seal verify、可重建 SQLite index、archive/JSONL/CSV |

## 目录

```text
fixture/                 可复制 workspace、可见数据、公开 evaluator、资格 runner
harness/                 真实 nested runtime 的本地锁模板（不含凭据）
oracle_tests/            模型不可见的 OOD/结构真值与 command scorer
prompts/                 单模板与五个物化 Prompt
scripts/                 fixture、StudySpec、计划门禁、recorder 与一键资格脚本
results/                 只允许未来提交脱敏 episode 级结果
```

## 正式实验停止门禁

正式实验使用独立 `seed=20260812`，不得合并资格或 pilot 数据。启动前还必须显式冻结 token/费用停止阈值；运行中不得按 arm、候选质量或临时 p 值提前停止。若完整预算不足，研究保持探索性 pilot 标签，不把 25 条 pilot 轨迹冒充正式证据。
