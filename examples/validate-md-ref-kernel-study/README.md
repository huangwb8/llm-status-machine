# validate-md-ref / bensz-skill-kernel 循环测试

这个示例验证 `validate-md-ref` 与 `bensz-skill-kernel` 在真实 Codex 调用中的协作。每一轮都是独立的 Codex 进程，并创建唯一的 `TaskID` 和 workspace：

1. 更新本地 skill 与 `bensz-skill-kernel`；
2. 生成并记录 `TaskID`；
3. 检查指定博客文章的参考文献；
4. 调查状态机/验证器是否生效，并在发现缺陷时写入 `docs/plans/plan-validate-md-ref-{TaskID}.md`；
5. 仅当计划文件存在时执行优化；不存在则标记为无需优化。

默认运行 3 轮，可使用 `--repeats N` 覆盖。阶段 prompt、脱敏 stdout/stderr 和结果均保存在每轮 `.bensz-api/task-validate-md-ref-{TaskID}/`，总清单为 `.bensz-api/validate-md-ref-kernel-study.json`。

## 运行

真实运行需要外部 Codex 配置：

```bash
export CODEX_HOME=/path/to/external/codex-home
uv run python examples/validate-md-ref-kernel-study/run_test.py
```

使用项目内置的无副作用冒烟模式：

```bash
uv run python examples/validate-md-ref-kernel-study/run_test.py \
  --dry-run --repeats 3 --output-root tmp/validate-md-ref-kernel-study
```

可通过 `--codex-executable`、`--model`、`--reasoning-effort`、`--timeout`、`--article` 和 `--skills-root` 覆盖运行参数。脚本只向 Codex 传递 `PATH`、语言环境和 `CODEX_HOME`，不会复制或归档外部凭据文件。
