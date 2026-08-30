# 错误先验洗脱、记忆中介与证据门控实验

这个实验研究一个直观问题：模型一开始被错误提示带偏后，后来看到更可靠的证据，能不能纠正自己？如果下一阶段还能看到上一阶段的完整笔记，它会不会更容易坚持原来的错误？只保留经过 evaluator 验证的记录，是否比保留全部笔记更有利于纠错？

可以把它理解为一场分三轮进行的“根据数据猜公式”考试：第一轮按分组给考生中性、正确或可能误导它的研究方向；第二轮换一名没有对话记忆的新考生，提供更多数据，并按实验分组交给它不同版本的上一轮笔记；第三轮再换一名新考生，要求恢复真实规律并提交最终答案。

本目录同时提供两条严格隔离的路径：

- **确定性资格实验**不调用 LLM，只使用预先写死的候选轨迹，验证 TrialPlan、运行记录、盲化评分、推断、seal 和导出链路是否正确；
- **真实 Codex 行为实验**在每个 episode 中依次启动三个全新的 Codex CLI 进程，观察真实模型在 A → B → C 三阶段中的候选变化。

资格轨迹只能证明软件会正确记录和区分已知轨迹，不能作为模型行为结论。原始设计与预注册背景见 [`docs/plans/prior-washout-evidence-gating-study.md`](../../docs/plans/prior-washout-evidence-gating-study.md)。

## 模型在做什么题

每个阶段都包含六道符号回归题。模型会看到若干组 `x` 和 `y`，需要猜出生成这些数据的公式。

例如，模型可能看到：

```text
x=0.10, y=1.21
x=0.40, y=1.96
x=1.00, y=4.00
```

真正的公式是：

```text
y = (x + 1) ** 2
```

但只看一小段数据时，指数函数、直线或其他多项式也可能在局部看起来合理。实验正是利用这种局部歧义，观察模型会不会受到先验提示和阶段记忆的影响。

六道题覆盖多项式、有理函数、指数衰减、周期函数、饱和函数和组合函数。每道题在每个阶段都必须给出三个依次改进的候选，并保留失败思路。模型只能使用公开 evaluator 和可见数据，不能读取真正公式、隐藏 OOD 点或结构真值。

## 一个 episode 是什么

一个 episode 是一条独立的 A → B → C 状态传递轨迹，也是随机分组和统计推断的最小单位。三个阶段由三个没有共享对话上下文的 Codex 进程分别完成，不是同一个“考生”连续作答：

```mermaid
flowchart LR
    A[阶段 A<br/>先验诱导<br/>只看训练数据] --> M1{状态转换}
    M1 -->|open / gated / purged| B[阶段 B<br/>全新 Codex<br/>加入验证数据]
    B --> M2{再次转换状态}
    M2 -->|open / gated / purged| C[阶段 C<br/>全新 Codex<br/>恢复并提交]
    C --> R[54 条候选<br/>RawBundle + Git + Seal]
    R --> S[盲化评分]
    S --> Q[episode 数据集与推断]
```

三个阶段分别做以下工作：

- **A：先验诱导。** 模型只看训练观测和分配到的研究起点，为六道题各生成三个候选；
- **B：洗脱与验证。** 启动全新进程和 thread，加入独立验证观测，并按实验臂重建上一阶段状态；A 阶段的先验文字不会再次出现；
- **C：恢复与提交。** 再次启动全新进程和 thread，根据全部可见证据生成最终候选；每道题的第三个候选作为最终表达式。

正常 episode 共有 `3 个阶段 × 6 道题 × 3 个候选 = 54` 条候选记录。同一 episode 内的候选共享实验臂、任务、证据和阶段状态，彼此相关；如果把它们当成 54 个独立样本，会人为夸大样本量。因此统计数据集始终一行一个 episode。

## 五个实验臂

五个实验臂只改变两件事：A 阶段收到什么先验，以及阶段之间保留什么记忆。所有组使用相同的模型、候选预算、运行时、可见数据和工具权限。

| 实验臂 | A 阶段的研究起点 | 传给下一阶段的状态 | 通俗理解 |
| --- | --- | --- | --- |
| `neutral-open` | 平等比较各种函数族 | 完整 workspace | 给中性探索原则，保留全部草稿 |
| `correct-open` | 优先探索与证据一致的方向 | 完整 workspace | 给正确原则，保留全部草稿 |
| `false-open` | 优先探索局部合理但方向错误的函数族 | 完整 workspace | 先误导，再保留全部错误笔记 |
| `false-gated` | 与 `false-open` 相同 | evaluator 签出的结构化候选、误差和失败类型 | 丢掉主观叙述，只保留客观成绩表 |
| `false-purged` | 与 `false-open` 相同 | 不含候选内容的中性占位记录 | 清空实质记忆，只保留“做过一些工作”的痕迹 |

`open` 会复制上一阶段的完整 workspace；`gated` 从干净 workspace 重建，只写入 typed evaluator ledger。ledger 会保留所有候选的表达式、训练/验证误差和失败类型，包括错误候选；“经过 evaluator”表示记录经过统一程序测量，不表示候选正确。`purged` 也从干净 workspace 重建，只按原记录数量和顺序写入中性占位项，因此保留工作量轮廓，但不保留候选内容。

所有 Prompt 都由 [`prompts/prompt-template.md`](prompts/prompt-template.md) 物化，只允许 `PRIOR` 与 `MEMORY` 两个标记区域不同。

## 三个主要比较在问什么

### 错误先验会不会持续

比较 `false-open − neutral-open`。

两个组都保留完整 workspace，主要区别是 A 阶段是否收到错误研究方向。如果错误先验在新证据出现后仍持续，`false-open` 的错误候选比例应高于 `neutral-open`。

### 证据门控能不能帮助纠错

比较 `false-gated − false-open`。

两个组在 A 阶段受到相同误导，区别是下一阶段看到完整笔记，还是只看到 evaluator 验证过的结构化记录。如果主观叙述会维持错误信念，`false-gated` 的错误候选比例应更低。

### 清空候选内容能不能帮助纠错

比较 `false-purged − false-open`。

如果错误主要通过上一阶段的候选内容传播，清除候选内容、只保留工作量轮廓后，`false-purged` 的错误候选比例应低于 `false-open`。

三个 contrast 属于同一个 Holm 多重比较 family。`correct-open` 是解释先验操纵的辅助对照，不进入这三个主要 contrast。

## 指标怎么计算

### IFO-AUC

主要指标 IFO-AUC 衡量洗脱后仍出现多少错误函数族。这里沿用预注册指标名，但当前实现不是传统 ROC-AUC，也没有对曲线积分，本质上是一个未加权比例：

```text
IFO-AUC = B/C 阶段属于预定义错误函数族的候选数 ÷ B/C 阶段全部候选数
```

正常 episode 在 B/C 共有 `2 × 6 × 3 = 36` 个候选。例如其中 4 个属于错误函数族，IFO-AUC 就是 `4 / 36 = 0.1111`。数值越低，表示错误候选洗脱得越彻底。

它统计三个候选中的失败思路，而不只统计最终答案。因此，模型最后答对了，但前两个候选仍保留错误尝试时，IFO-AUC 也会上升。这个指标描述候选轨迹中的错误 motif，不等同于最终正确率。错误函数族按 AST 中是否出现 `exp`、`sin/cos`、含 `x` 的分母以及多项式次数等固定规则分类，具体实现见 [`oracle_tests/symbolic_oracle.py`](oracle_tests/symbolic_oracle.py)。

### 次要指标

- **结构恢复率**：C 阶段六个最终表达式中，有多少属于正确函数族；它可能把“函数族正确但系数错误”算作恢复；
- **OOD-NMSE**：使用模型从未见过的远端 `x` 检验最终表达式，区分真正恢复公式与局部拟合；越低越好；
- **协议完整性**：检查 54 条候选、三个新 PID/thread、最终结果和阶段证据是否齐全。

隐藏 `symbolic_oracle` 不接收 Prompt、arm、condition 或执行顺序。评分实现只从 sealed snapshot 读取统一路径下的候选记录、最终表达式和协议证据，并使用 pinned hidden-task support file，不根据 memory 目录布局或实验臂分支计分。episode 执行失败或 timeout 时，预注册 outcome 使用该指标的最坏边界值；计划内指标缺失则直接报错，不会因为结果不好而删除 episode。

## 真实 Pilot 结果

2026-08-09 完成的探索性 pilot 使用 `gpt-5.6-sol`、low reasoning、`seed=20260811`、`concurrency=1` 和 900 秒 stage timeout。五个实验臂各运行 5 个独立 episode，共计划 25 个 episode 和 75 个 Codex stage。计划使用平衡轮换，使每个实验臂在五个 comparison set 的执行位置 1–5 各出现一次，减少固定先后顺序造成的混杂。

### 运行与记录完整性

- 25 个 episode 全部封存，24 个 completed；
- 1 个 `neutral-open` 在 C 阶段 timeout，因此 run 状态为 failed，但后续 seal、评分、数据集和报告流水线均完成；
- 74/75 个 stage 完成，25 个 seal 全部有效；
- 重复 symbolic oracle 评分一致性为 1.0；
- 凭据名称、外部路径和凭据片段扫描均为 0 命中。

这说明基础设施已能保存失败 episode，并按预注册规则继续完成盲评和推断。

### A 阶段的错误提示确实生效

按 A 阶段候选记录进行描述性汇总，三个 false 臂约有 30%–33% 的候选落入预定义错误函数族，而中性组约为 9%。这说明错误先验成功改变了模型第一轮探索方向。

| 实验臂 | A 阶段错误族候选比例 |
| --- | ---: |
| `neutral-open` | 8.9% |
| `correct-open` | 13.3% |
| `false-open` | 31.1% |
| `false-gated` | 30.0% |
| `false-purged` | 33.3% |

### 洗脱后的错误候选比例普遍较低

主要 IFO-AUC 汇总如下：

| 实验臂 | B/C IFO-AUC 均值 | 说明 |
| --- | ---: | --- |
| `neutral-open` | 28.3% | 包含一个按最坏值 `1.0` 计分的 timeout；仅完成 episode 为 10.4% |
| `correct-open` | 13.3% | 5/5 完成 |
| `false-open` | 11.1% | 5/5 完成 |
| `false-gated` | 8.3% | 5/5 完成 |
| `false-purged` | 7.2% | 5/5 完成 |

三个预注册的描述性对比为：

- `false-open − neutral-open = -0.1722`；
- `false-gated − false-open = -0.0278`；
- `false-purged − false-open = -0.0389`。

三个 Holm-adjusted p 值均为 `0.8571`。第一项的大幅负值主要由 `neutral-open` 的 timeout 最坏值造成；只看完成 episode 的敏感性描述时，`false-open − neutral-open` 约为 `+0.0069`，接近没有差异。

因此，这次 pilot 最清楚的行为信号是：错误提示能在 A 阶段把模型带向错误函数族，但合并 B/C 后，所有 false 臂都降到约 7%–11%。由于主指标把 B 和 C 合并，它不能单独说明纠错具体发生在哪一阶段。`gated` 和 `purged` 相对 `open` 的方向符合预期，绝对差异只有约 3–4 个百分点，当前样本不能证明这是稳定的记忆处理效应。`correct-open` 的 A 阶段错误比例略高于 `neutral-open`，在每臂 5 个 episode 的探索性样本中也不能据此判断正确先验无效。

最终恢复整体较好：24 个完成 episode 中，23 个六任务结构恢复率为 100%；一个 `false-gated` episode 为 5/6。OOD-NMSE 大多接近零，但各有一个 `false-open` 和 `false-gated` 离群 episode，说明结构分类正确不等于公式数值完全正确。

完整脱敏摘要见 [`results/real-codex-pilot-20260809-summary.json`](results/real-codex-pilot-20260809-summary.json)。本结果是探索性 pilot，不是确认性证据。

这些结果只适用于当前模型、low reasoning、六道固定任务、Prompt 和 runtime build，不能直接推广到其他模型、推理强度或开放式软件任务。

## Pilot 暴露出的设计限制

### 记忆处理没有形成强制暴露

当前实现把 open、gated 或 purged 状态写入 stage workspace，并在 Prompt 中告诉模型可以读取既有状态，但没有强制读取，也没有把规范化记忆载荷直接注入 B/C Prompt。模型可以只根据 Prompt 中再次提供的训练和验证数据从头解题。

因此，workspace 中存在三种不同记忆，并不等于模型在每次 episode 中都实际消费了这些记忆。当前组间差异不能直接解释为记忆策略的因果效应。

### 验证数据过于容易恢复真公式

B 和 C 都能看到完整训练与验证观测，六道题又常能从这些点精确识别真实表达式。进入这些阶段后观察到很强的洗脱，留给记忆策略发挥作用的空间很小。不过 A → B 同时改变了进程、阶段指令和可见数据，当前设计不能把洗脱单独归因于新增验证数据。

### 主要终点出现地板效应

`false-open` 的 pilot IFO-AUC 只有 0.111。对于目标方向为“降低 IFO-AUC”的干预，理论最大绝对降幅也只有 0.111；而原冻结功效方案使用的最小有意义效应是 0.25。增加样本量不能修复终点灵敏度和目标效应尺度不匹配的问题。

### 小样本容易被单次失败主导

pilot 每臂只有 5 个 episode。一个 timeout 按最坏值计分后，就把 `neutral-open` 的均值从完成 episode 的约 10.4% 拉高到 28.3%。ITT 规则本身应保留，但结论必须同时展示失败来源和敏感性描述。

## 正式实验停止门禁

原功效冻结使用 `MDE=0.25`、双侧 `alpha=0.05/3`、power 0.80，以及 `sigma_used=max(0.30, pilot pooled SD 的单侧 95% 上界)`。pilot pooled SD 为 0.1824，上界为 0.2477，因此得到 `sigma_used=0.30`、每臂 31 个 episode；按五臂 sequence-position 平衡上取整后为每臂 35 个，共 175 episode 和 525 个 Codex 进程。

这个数字只保留为原预注册方案的可追溯记录，**不应按当前协议直接启动正式实验**。在新的确认性运行前，至少需要：

- 强制把规范化记忆载荷提供给 B/C，或把可验证的状态读取作为协议要求；
- 将 A 阶段诱导强度纳入正式 manipulation check；
- 重新评估终点，例如使用 A → B 的错误候选保留率或 episode 内变化量；
- 调整验证任务难度，避免模型完全绕过阶段记忆从头精确求解；
- 完成新的小规模 pilot，再根据新的基线、方差和目标效应重新计算 power；
- 显式冻结 token/费用停止阈值。

新的正式实验仍应使用独立 seed，不得合并资格或现有 pilot 数据；运行中不得按 arm、候选质量或临时 p 值提前停止。

## 运行真实 Codex 协议

真实入口是 `scripts/run_real_study.py`。它只接收项目外已有的 `CODEX_HOME` 环境引用，不复制或写入 `auth.json`、`config.toml`、API key、Cookie 或外部配置路径。nested Codex 使用 pinned executable digest、明确模型和 reasoning effort；命令网络与 web search 均禁用。

先用 shakedown 检查环境：

```bash
export CODEX_HOME=/path/outside/this/repository

uv run python examples/prior-washout-evidence-gating-study/scripts/run_real_study.py \
  --mode shakedown \
  --root tmp/prior-washout-real-shakedown \
  --model gpt-5.6-sol \
  --reasoning-effort low \
  --stage-timeout 900
```

输出根必须尚不存在。`--mode pilot` 默认每臂运行 5 个 episode；`--mode confirmatory` 仍保留原每臂 35 个 episode 的历史参数，但在上述设计门禁完成前不得把它视为可直接执行的有效确认性方案。含失败 episode 的 run 会继续 seal、盲评和 dataset 构建，失败值按预注册 intention-to-treat 规则处理，不自动 retry。

## 一键资格测试

先用标准实例门禁和单 episode smoke 检查目录与最小执行链：

```bash
uv run lsm example validate examples/prior-washout-evidence-gating-study --json
uv run python examples/prior-washout-evidence-gating-study/scripts/smoke.py \
  --root tmp/prior-washout-one-episode
```

smoke 只运行一个确定性 qualification episode；下面的一键资格测试仍覆盖完整的 25 episode 研究与评分链路。

资格实验无需模型密钥：

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

最终入口是 `tmp/prior-washout-qualification/qualification-summary.json`。资格 runner 使用内置固定答案，是 trusted infrastructure test double；其结果不能进入真实模型结论。

## 分步运行资格实验

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

- episode workspace 只包含训练/验证观测和 AST 白名单 evaluator；结构真值与 OOD 点只作为 pinned scorer support file，在运行结束后的只读 sealed snapshot 评分阶段提供；
- expression grammar 只允许有界数字、`x`、算术运算和 `sin/cos/exp/log`，同时限制文本长度、AST 节点数与常数幂指数；
- `symbolic_oracle` 设置 `include_prompt: false`，评分 manifest 不接收 arm、condition、ordinal 或 Prompt；评分程序只读 sealed final commit，并在评分后复核 workspace digest；
- `.lsm/`、runtime lock、RawBundle、导出、凭据和本地结果默认忽略；不要把 `auth.json`、`config.toml`、API key 或 Cookie 放进示例目录；
- `custom_command` 资格 profile 如实记录 `network=inherit`，但资格脚本自身不执行网络操作；真实模型 pilot 不得复用其固定候选轨迹。

## 主要功能覆盖

| 能力 | 资格证据 |
| --- | --- |
| Prompt lint/render/freeze | 单模板物化五臂，规范化摘要一致，冻结文件有 SHA-256 |
| Harness/runtime | custom surface 可发现；Python 绝对路径、版本、platform 与 digest 锁定 |
| Workspace | 编译时 baseline、每 episode initial/final manifest、Git commit、changed files、diff |
| Study | validate、estimate/power、稳定 JSONL compile、平衡随机化 provenance |
| Run | 25 个串行 independent episode，planned/actual dispatch 可核对 |
| Recording | JSONL event、raw stdout/stderr、artifact、四类 outcome、失败与 timeout seal |
| Evaluation | execution-integrity + prompt-blind pinned command scorer，各重复两次 |
| Research | 一行一个 episode、三个预注册 contrast、bootstrap、置换检验、Holm、报告 |
| Store/export | seal verify、可重建 SQLite index、archive/JSONL/CSV |

## 目录

```text
lsm.yml                  实例类型、组件、Study 来源与单 episode smoke 契约
fixture/                 可复制 workspace、可见数据、公开 evaluator、资格 runner
harness/                 真实 nested runtime 的本地锁模板（不含凭据）
oracle_tests/            模型不可见的 OOD/结构真值与 command scorer
prompts/                 单模板与五个物化 Prompt
scripts/                 fixture、StudySpec、计划门禁、smoke、recorder 与一键资格脚本
results/                 只保存可提交的脱敏 episode 级结果摘要
```
