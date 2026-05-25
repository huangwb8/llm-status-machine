# Changelog

本文件记录项目重要变更，格式遵循 Keep a Changelog，并优先维护 `[Unreleased]`。

## [Unreleased]

### Added（新增）

- 明确项目目标与产品计划，补充 `docs/plans/initial-product-plan.md`。
- 增强 session transcript、stdout/stderr、artifact 与运行中 AI 客户端可调用的本地 API。
- 前端新增 transcript 与 artifact 查看区域，方便复盘模型行为。
- Dry Run 模拟器会写入 agent event 与 artifact，用于验证记录链路。
- 新增 Experiment / Prompts / Models / Workspace / DevTools 五个独立界面，Experiment 专门连接 prompt、模型与工作空间。
- 新增实验编排测试，覆盖串行链式输入与并行独立输入两种模式。

### Changed（变更）

- 内部 session 工作区的 Git 快照统一固定在 `main` 分支，并在前端 session 详情与 metadata/env 中明确记录分支，减少多分支/多批次理解成本。
- 串行实验现在会把每轮输出工作区保存为 `state-N`，并将上一轮输出作为下一轮输入；并行实验仍从初始 Workspace 独立复制。

## [1.0.0] - 2026-05-25

### Added（新增）

- 初始化 AI 项目指令文件：生成 `AGENTS.md`、`CLAUDE.md`、`README.md` 与 `.gitignore`
- 配置项目工程原则、工作流和变更记录规范

### Changed（变更）

### Fixed（修复）

---

## 记录规则

- 必须记录影响项目行为、结构、工作流、工程原则、指令文件或关键配置的变更
- 记录应说明改了什么、为什么改，以及影响范围
- 版本号遵循 SemVer：bug fix 递增修订号，新功能递增次版本号，破坏性变更递增主版本号

```markdown
## [版本号] - YYYY-MM-DD

### Added（新增）
- 新增了 XXX：用途是 YYY

### Changed（变更）
- 修改了 XXX：原因是 YYY，影响是 ZZZ

### Fixed（修复）
- 修复了 XXX：表现是 YYY，修复方式是 ZZZ
```
