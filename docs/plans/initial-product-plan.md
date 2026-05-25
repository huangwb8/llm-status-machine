# LLM Status Machine 初始产品计划

## 目标

把现有 Vite + Express 原型推进成可实际使用的本地 LLM 行为实验台，覆盖 prompt 管理、环境管理、状态管理、批量运行、版本化记录、行为 transcript 与本地 API。

## 当前基础

- 前端已有 prompt、state、environment、run、session、diff 与 stream 面板。
- 后端已有 CRUD API、SSE 事件流、运行调度器、工作区复制、git 初始化与结果 diff。
- 默认 Dry Run 环境可以在不调用外部模型的情况下验证记录链路。

## 本轮增强

- 为每个 session 生成持久化 `transcript.ndjson`，保存所有 stdout、stderr、错误与生命周期事件。
- 为每个 session 保存 `stdout.txt`、`stderr.txt`、`metadata.json`、`diff.patch` 和 artifact 目录。
- 向被调用的 LLM 客户端注入 `LLM_STATUS_MACHINE_API`、`LLM_STATUS_MACHINE_RUN_ID`、`LLM_STATUS_MACHINE_SESSION_ID` 等环境变量。
- 增加 `/api/agent/events` 与 `/api/agent/artifacts`，方便运行中的 AI 客户端主动写入行为事件和补充产物。
- 前端增加 transcript 和 artifact 查看区域，减少只看 diff 时的信息损失。

## 后续建议

- 增加真实 Codex / Claude Code 命令模板的预检。
- 增加 run 导出功能，把 metadata、transcript、diff 与 workspace 快照打包。
- 增加评估器模块，对多个 session 的输出做标签化、聚类和评分。
- 增加 state 导入器，把上传的 zip 或目录复制到 `data/states`。
