# validate-md-ref / bensz-skill-kernel LSM 集成回归测试

这个示例验证 `validate-md-ref` 与 `bensz-skill-kernel` 在真实 Codex 调用中的协作，并完整走 LSM 标准链路：

`StudySpec → TrialPlan → RunEngine → Episode → sealed RawBundle`

LSM 负责冻结计划、串行调度和 `carry_forward` workspace；每个 episode 通过 `custom_command` harness 调用本文件的 worker，worker 再启动外部 Codex 阶段。每轮都会创建唯一的 `TaskID` 和 workspace：

1. 更新本地 skill 与 `bensz-skill-kernel`；
2. 生成并记录 `TaskID`；
3. 检查指定博客文章的参考文献；
4. 调查状态机/验证器是否生效，并在发现缺陷时写入 `docs/plans/plan-validate-md-ref-{TaskID}.md`；
5. 仅当计划文件存在时执行优化；不存在则标记为无需优化。

默认运行 3 轮，固定为 `concurrency=1`、`state_policy=carry_forward`，可使用 `--repeats N` 覆盖。LSM 计划和运行证据保存在输出根的 `plan.jsonl` 与 `data/runs/<run-id>/`；每个 RawBundle 都包含 prompt、原始 stdout/stderr、transcript、workspace 前后快照、Git commit、changed files、diff 和 seal。外部工作流阶段日志位于 episode workspace，并通过 `artifacts/workflow-summary.json` 挂入 RawBundle。

## 运行

真实运行会按以下顺序解析 Codex 配置目录：`--codex-home`、环境变量 `CODEX_HOME`、本机 `~/.codex`。因此本地已经登录的 Codex 通常无需额外导出变量；只有这三个位置都不可用时才会拒绝启动。

```bash
export CODEX_HOME=/path/to/external/codex-home
uv run python examples/validate-md-ref-kernel-study/run_test.py
```

使用项目内置的无副作用冒烟模式：

```bash
uv run python examples/validate-md-ref-kernel-study/run_test.py \
  --dry-run --repeats 3 --output-root tmp/validate-md-ref-kernel-study
```

真实运行结束后，可用标准 LSM 命令核验：

```bash
uv run lsm run status <run-id> --data-root tmp/validate-md-ref-kernel-study/data --json
uv run lsm episode validate <episode-id> --data-root tmp/validate-md-ref-kernel-study/data --json
```

可通过 `--codex-home`、`--codex-executable`、`--model`、`--reasoning-effort`、`--timeout`、`--article` 和 `--skills-root` 覆盖运行参数。脚本只向嵌套 Codex 传递 `PATH`、语言环境和 `CODEX_HOME`，不会复制或归档外部凭据文件。
