# 评估类 subagent 数量与软件开发质量

这个示例使用 LLM Status Machine 实际比较 Codex + GPT-5.6 Sol medium 工作流中 3、6、9 个只读评估类 subagent 的表现。三个顶层 episode 串行执行，但都从同一个 `AsyncTTLCache` starter workspace 独立开始；评估完成后，恰好一个总结 subagent 汇总问题，顶层 Codex 是唯一修改代码的主体。

本轮是每个条件一次的描述性 pilot，不足以估计稳定趋势或统计显著性。已完成的 pilot 恰好按 3 → 6 → 9 固定升序运行，数量效应与顺序、时间漂移及 API 状态完全混杂；它的主要价值是展示 Prompt、pinned runtime、TrialPlan、真实模型执行、RawBundle、workspace diff、seal、外部盲化评分和 R Markdown 分析如何串成一条可审计链路。后续重新生成计划时使用预注册 seed `20260810`，执行顺序为 9 → 3 → 6，避免再次采用单调顺序。

## 安全边界

- 仓库内不复制、不软链接、不读取或输出 `config.toml` 与 `auth.json`。
- 运行时只通过外部 `CODEX_HOME` 继承现有 Codex 配置；StudySpec 和 RawBundle 只记录变量名，不记录值。
- 本目录的 `.gitignore` 防御性忽略误放的 `.codex/`、`auth.json`、`config.toml`、本地 `.lsm/` 和导出包。
- RawBundle 可能包含模型输出和代码，应按本地实验数据管理；默认不提交、不上传。

## 目录

```text
fixture/                 被三个 episode 独立复制的 starter 软件
oracle_tests/            episode 看不到的外部验收程序
prompts/                 单一模板与 3/6/9 三个物化 Prompt
scripts/prepare_study.py 生成并校验本地 StudySpec
scripts/score_run.py     对 sealed final workspace 盲化评分
analysis/                R + Rmd 可复现分析
results/                 可提交的脱敏 tidy 结果与摘要
.lsm/                    本地 TrialPlan、run 与 RawBundle，已忽略
```

## 运行

以下命令从仓库根目录执行。`CODEX_HOME` 应指向已经可用的外部 Codex 配置目录；不要把其中内容复制到本示例。

```bash
export CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"

uv run lsm harness lock \
  --surface codex_exec_cli \
  --executable "$(command -v codex)" \
  --version 0.144.0 \
  --output examples/subagent-count-quality-study/.lsm/runtime.json

uv run python examples/subagent-count-quality-study/scripts/prepare_study.py \
  --runtime examples/subagent-count-quality-study/.lsm/runtime.json \
  --output examples/subagent-count-quality-study/.lsm/study.pilot.yml

uv run lsm study validate examples/subagent-count-quality-study/.lsm/study.pilot.yml --json
uv run lsm study compile \
  examples/subagent-count-quality-study/.lsm/study.pilot.yml \
  examples/subagent-count-quality-study/.lsm/plan.pilot.jsonl --json
uv run python examples/subagent-count-quality-study/scripts/verify_plan.py \
  examples/subagent-count-quality-study/.lsm/plan.pilot.jsonl
uv run lsm run start \
  examples/subagent-count-quality-study/.lsm/plan.pilot.jsonl \
  --data-root examples/subagent-count-quality-study/.lsm --json

uv run python examples/subagent-count-quality-study/scripts/score_run.py \
  --data-root examples/subagent-count-quality-study/.lsm \
  --output examples/subagent-count-quality-study/results/pilot.csv
```

分析脚本从 `results/pilot.csv` 生成完整 RDS、PDF 图和 Rmd 报告：

```bash
cd examples/subagent-count-quality-study/analysis
Rscript quality_analysis.R
Rscript -e 'rmarkdown::render("quality_analysis.Rmd")'
```

## 解释边界

主指标是交付门禁调整后的 100 分质量分：只有 episode 端到端 `completed` 才保留 oracle code score，超时或失败记 0；未调整的 `oracle_code_score` 同时保留，用来区分“代码产物正确”与“软件交付完成”。scorer 从 sealed `workspace.final.json` 指定的 Git commit 导出临时快照，并逐文件核对内容后评分，不读取可变现场代码。episode 是统计单位，subagent、测试用例和发现条目都不是独立样本。评估数量增加同时增加计算成本，因此本示例观察的是“更多评估 agent 及其附带预算”的总效果，不能分离纯数量效应；现有 pilot 还不能把 9-evaluator 的超时归因于数量。若要做初步重复研究，可在冻结本方案后按时间 block 轮换顺序、把每臂扩展为至少 3 个 episode，并保留原始失败及 requested count 的 intention-to-treat 分组。
