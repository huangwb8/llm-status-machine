# Python CLI 核心与证据模型

状态：已接受  
日期：2026-07-31

## 背景

早期实现曾把管理界面、通用 shell runner、执行状态和存储耦合在一起，无法稳定固定 Harness 版本，也混淆了串行调度与工作区继承。

## 裁决

- 项目核心采用 Python 3.12、`uv`、Typer、Pydantic、SQLite WAL 和文件系统 RawBundle。
- 应用版本由 `src/llm_status_machine/version.py` 唯一维护；数据 schema 版本独立维护。
- `Episode` 是实验与统计单位；每次进程启动是 `Attempt`。事件仅是 episode 内观察，不是独立样本。
- `concurrency` 只表示并发上限；`state_policy` 独立取值 `independent`、`carry_forward` 或 `branch`。
- Harness surface、runtime build、model endpoint、execution profile 和 workspace fixture 分离。执行只接受已经冻结且包含绝对运行时身份的计划。
- RawBundle 先持久化原始 stdout/stderr 字节，再派生 canonical events；未知或无法解析的事件保留原始引用。
- 完成状态要求 process、protocol、capture 和 workspace 四类 outcome 都满足计划约束；评分不得改写 sealed RawBundle。
- source workspace 永不直接执行。每个 episode 使用独立副本；carry-forward 只继承已 seal 的父 episode。
- 子进程禁止默认 shell，POSIX 使用独立 process group，并在超时时先 TERM 后 KILL。

## Schema 与目录

新结果根以 `schema_version = 1` 自描述：

```text
<project>/.lsm/
  index.sqlite3
  plans/
  runs/<run-id>/
    run.json
    episodes/<episode-id>/
      workspace/
      attempts/attempt-1/raw-bundle/
      evaluations/
```

RawBundle 至少包含实际 Prompt、raw stdout/stderr、transcript、metadata、outcomes、初始/最终 workspace manifest、changed files、binary diff、artifact manifest 和 seal。

## 安全与秘密

- 自定义 Harness 只接受 argv 数组，不接受 shell 模板。
- 环境变量使用显式 allowlist；metadata 记录变量名或 secret reference，不记录 secret 值。
- 所有导入、artifact 和 bundle 路径在解析后必须仍位于允许根目录内。
- Raw transcript 可能包含敏感输出，默认由用户控制本地数据根、权限和保留周期；CLI 不声称提供加密隔离。
