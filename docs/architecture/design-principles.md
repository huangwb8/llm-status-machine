# LLM Status Machine 设计原则与取舍

这份文档解释 LSM 为什么采用当前的对象边界、执行顺序和证据模型。具体命令和操作步骤见[操作者手册](../operator-guide.md)；从配置到执行的内部流程见[工作过程说明](../how-it-works.md)。

## 先冻结，再运行

操作者编辑的是 `StudySpec`（YAML），runner 执行的是编译出的 JSONL `TrialPlan`。编译阶段会渲染 Prompt、计算摘要、扫描 source workspace 的 baseline manifest、展开因素与重复，并固定 runtime 的绝对路径、版本和 SHA-256。

运行时不会重新读取 `latest`、依赖宿主 `PATH` 或临时扩展实验矩阵，而是重新校验冻结的 runtime digest 与 workspace baseline。这样可以区分“计划的是哪次实验”和“实际跑的是哪次实验”。

确认性研究还会在计划中冻结主要 outcome、Prompt contrast、失败/缺失策略、显著性水平、bootstrap/permutation 次数和分析 seed。编译器先构造 comparison set，再用版本化的平衡轮换算法决定 set 与 arm 顺序；每个 Trial 都保存 arm、pair/block、sequence position、dispatch batch 和随机化 draw。

## 把并发和状态拆开

`concurrency` 只描述同时运行多少个 episode，`state_policy` 只描述 workspace 如何继承。两个字段分开，避免把“并行调度”和“实验状态依赖”混成一个模糊的 `serial` 开关。

| `state_policy` | 工作区关系 | 与 `concurrency` 的关系 |
| --- | --- | --- |
| `independent`（默认） | 每个 episode 从同一 frozen baseline 复制 | 可使用有界并发 |
| `carry_forward` | 下一轮只继承上一轮已完成且已 seal 的 workspace | 必须为 `1` |
| `branch` | 每个分支从共同 baseline 开始，保留分支语义 | 可使用有界并发 |

因此，“并发 8 个独立样本”和“连续 3 轮让 agent 接着改同一个项目”是两个清楚、可验证的实验。

## 原始证据优先于漂亮的解析结果

stdout/stderr 先逐字节写入 raw 文件，再增量解析成 transcript。未知 vendor event、无效 UTF-8、半行 JSON 和解析失败都会被标记，原始字节仍完整保留。两个 OS pipe 没有可靠的全局顺序，因此记录不会伪造一个不存在的混合输出全序。

每个 attempt 都会得到 process、protocol、capture、workspace 四类 outcome。只有四类 outcome 都满足计划约束，episode 才会标记为 `completed`；失败或超时也会尽力捕获最终工作区并封存已有证据。

## 保护 workspace，也保护评分独立性

source workspace 从不直接执行。每个 episode 在独立副本中初始化 Git snapshot，并记录初始/最终 manifest、changed files 和 diff。这样被测源目录不会被 runner 直接污染，工作区变更也能被复核。

评分结果写入 `evaluations/` 和研究派生目录，不修改 sealed RawBundle。升级评分规则或重跑评估时，仍然可以使用同一份原始运行证据。

## Episode 是实验与统计单位

一次进程启动是一个 `Attempt`，但 `Episode` 才是实验与统计单位。episode 内的 subagent、token、测试用例、metric 维度和评分重复不会增加样本量；重复评分先聚合到 episode 层，再进入 dataset 和 inference。

这条边界保证调度、记录和统计推断使用同一个可解释的随机化单位，也避免把一次运行内部的观测误当成相互独立的样本。

## 分层保存事实与派生结果

文件系统中的 `run.json`、`episode.json`、RawBundle、evaluation manifest、dataset manifest 和 analysis manifest 是事实来源；SQLite 只保存可重建的查询索引。评分、数据集和推断只读上游并写入独立目录，通过输入摘要生成稳定 ID。

这使得索引可以重建、派生结果可以重算，而原始运行证据保持不可变、可核验。

## 明确安全边界

- 自定义命令只接受 argv 数组，不以 shell 为入口；Prompt 中的 shell 元字符只是数据。
- 计划固定绝对 executable 与 digest；宿主 `PATH` 只用于显式 discovery，不能在运行时替换 runtime。
- 环境变量使用名称 allowlist，launch metadata 只记录变量名和 argv digest，不保存 secret 值。
- workspace、artifact 和 bundle 路径在访问前必须仍位于允许根目录内。
- native runtime、uv 和 CLI 自带 sandbox 都不是完整的操作系统隔离；高风险实验应额外控制网络、用户、文件权限和资源。
