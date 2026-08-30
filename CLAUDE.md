# LLM Status Machine - Claude Code 项目指令

## 核心指令

@./AGENTS.md

## Claude Code 特定说明

- `AGENTS.md` 是通用指令源；本文件只补充 Claude Code 适配
- 引用文件用 Markdown 链接，必要时带行号：`[file.md:42](path/file.md#L42)`
- 复杂任务用 TodoWrite；改代码前先读文件，优先精确编辑
- 避免无关重构；修改 `AGENTS.md` 后更新 `CHANGELOG.md`
- Python 开发统一使用 `uv sync --frozen --extra test`、`uv run pytest` 和 `uv run lsm`

## 与 AGENTS.md 的关系

- `AGENTS.md` 是跨平台唯一通用指令源；项目级规则只在该文件维护。
- 本文件通过 `@./AGENTS.md` 自动引入通用规则，仅补充 Claude Code 的交互约定。
- 修改通用规则后无需复制粘贴；提交前确认 `@./AGENTS.md` 引用仍然存在且可解析。
