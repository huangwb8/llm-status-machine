EVALUATOR_COUNT = 3

你正在修复当前 workspace 中的一个标准库 Python `AsyncTTLCache`。完整功能契约在 `README.md`，公开测试在 `tests/`。请完成代码、运行公开测试，并在最终答复中简洁说明验证结果。

必须严格执行以下 Agent 工作流：

1. 在修改产品文件前，使用已安装的 `parallel-vibe` skill 的智能模式启动恰好 `EVALUATOR_COUNT` 个只读评估类 subagent。所有 evaluator 必须收到完全相同的 broad-review brief：独立审查需求、实现、并发/取消竞态、API 边界和测试缺口；不得读取其他 evaluator 结果，不得修改任何产品文件；输出不超过 600 个中文字。evaluator 只用编号区分，不设置不同专业角色。
2. evaluator 最多同时运行 2 个，按波次完成。失败也必须记录，不得静默补成另一个条件。
3. 所有 evaluator 完成后，启动恰好 1 个只读总结 subagent。它必须读取全部 evaluator 结果，去重并按严重度汇总成不超过 1000 个中文字的实施清单；总结 subagent 不得修改产品文件。
4. 顶层 Codex 是唯一实施主体。只在总结完成后修改产品文件并运行公开测试；不得再启动实现或复核 subagent。
5. 按 `parallel-vibe` 目录契约保留 plan、每份 evaluator `RESULT.md`、完成状态、runner log 与 summary。结束前把这些公共过程文件复制到环境变量 `LLM_STATUS_MACHINE_ARTIFACTS_DIR` 下的 `experiment-evidence/`，并生成 `manifest.json`，至少记录 requested evaluator 数、completed evaluator 数、summary 数、executor 数和 protocol deviations。不得复制任何全局 skill、Codex 配置、鉴权文件、Cookie、token 或其它敏感内容，也不得读取或回显它们。

除 `EVALUATOR_COUNT` 这一行外，三个实验 Prompt 必须完全一致。不要寻找或猜测 workspace 外的验收测试；只依据公开契约完成任务。

