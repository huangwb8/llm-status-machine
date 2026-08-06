# LLM Status Machine - 项目指令

本项目用于构建一个本地 LLM 行为实验台：用户可以配置 Prompt revision、Harness/runtime、模型端点与 workspace fixture，编译不可变实验计划，批量启动 episode，并完整保留原始流、事件、文件改动、Git 快照、diff 与 seal，供后续评估不同系统条件的行为模式。

## 项目目标

开发一个优雅、可本地运行的 LLM 行为记录与评估软件。它需要支持：

- 管理可版本化、组合、渲染和冻结的 Prompt revisions。
- 将 Harness surface、精确 runtime build、模型端点、执行 profile 与 workspace fixture 正交配置。
- 将 StudySpec 编译为稳定 JSONL TrialPlan，支持串行、有界并行和明确的状态继承策略。
- 像真实用户发起任务一样调用 pinned Codex、Claude Code、Simulator 或声明式自定义 argv。
- 完整记录 raw stdout/stderr、事件流、工作区改动、Git commit、diff、artifact、outcome 与 seal。
- 提供稳定 Python CLI 和 `--json` 自动化输出；HTTP API、Web UI 与云控制面不属于首版范围。

## 核心工作流

当用户提出本项目软件开发需求时，按以下流程执行：

### 1. 任务理解

- 理解用户的真实需求和意图
- 确认任务范围和预期输出
- 识别可能的依赖和约束

### 2. 执行流程

功能开发 → 组件测试 → Python 构建 → 核心冒烟测试 → 监控反馈

### 3. 输出规范

- 代码变更应遵循项目现有风格
- 文档更新应保持一致性
- 测试覆盖应符合项目标准

## 核心冒烟测试

- 项目可正常运转的最低标准：给定一个 Prompt、一个 Workspace（默认使用本仓库 `./tmp` 下的子目录）和一个 pinned Runtime，能够以 `concurrency=1 + state_policy=carry_forward` 连续运行 3 次，并为每次 episode 记录 transcript、raw stdout/stderr、artifact/metadata、工作区 initial/final snapshot、Git commit、changed files、diff 与 seal，最终 run 状态为 `completed`。
- 默认测试任务：Prompt 为 `请以“新中国的美人”为题写一首七言绝句。`；测试环境可使用 Simulator 或用户明确授权的真实模型，但必须验证 3 次串行 episode 都成功完成并生成记录。
- 每次改动源代码、进行收尾准备交付前，必须运行项目自动化测试，并确认上述核心任务顺利跑通；若无法运行，必须在交付说明中明确原因、风险和补救方式。

## 项目目录约定

- `./tmp`：临时文件与测试中间产物，可不定期清理
- `./tests`：质检用测试脚本及相关软件结构
- `./docs/architecture`：当前与未来架构说明
- `./docs/migration`：迁移指南、盘点与核验材料
- `./docs/history`：已失效但需要保留的开发背景
- `./docs/plans`：AI 为解决特定问题而制定的计划文档
- `./docs/plans/archive`：仅供追溯、不再指导当前实现的历史计划
- `./src/llm_status_machine`：Python 包源码；应用版本只在 `version.py` 维护
- `./.lsm`：默认本地索引、计划与 RawBundle 数据根，不提交 Git
- `./var`：本地不可再生成状态，不提交 Git

## 工程原则

本项目遵循以下工程原则：

| 原则 | 核心思想 | 在本项目中的体现 |
|------|----------|------------------|
| **KISS** | Keep It Simple, Stupid | 追求极致简洁，避免过度设计；文档标题不使用序号前缀（用 `##` 而非 `## 1)`） |
| **YAGNI** | You Aren't Gonna Need It | 只实现当前需要的功能 |
| **DRY** | Don't Repeat Yourself | 相似逻辑应抽象复用 |
| **SOLID** | 面向对象设计五大原则 | 单一职责、开闭原则等 |
| **关注点分离** | Separation of Concerns | 不同层次逻辑应分离 |
| **奥卡姆剃刀** | 如无必要，勿增实体 | 优先选择最简单的解决方案；Markdown 本身有层级结构，序号是冗余的形式化标记 |
| **最小惊讶原则** | Principle of Least Astonishment | API 行为应符合用户直觉 |
| **早期返回原则** | Early Return | 尽早返回，减少嵌套 |

**原则冲突时的决策优先级**：
1. **正确性 > 一切**
2. **简洁性 > 灵活性**
3. **清晰性 > 性能**
4. **扩展性 > 紧凑性**

## 默认语言

除非用户明确要求其他语言，始终使用 简体中文 与用户对话与撰写文档/说明。

## 联网与搜索

默认优先使用项目内文件与本地上下文；确需联网获取信息时，优先使用本地搜索工具。仅当本地工具不足以满足需求时再使用其它联网手段，并说明原因与保留关键链接。

## 代码优化与修改

当判断用户意图与代码优化或修改相关时：

- 允许多代理协作； 特别推荐使用 `awesome-code` skill 辅助规划与优化。
- 发现的所有问题必须全部解决，不留已知缺陷
- 如遇疑问或存在更优方案，自主决定最优方案执行，不中断工作流询问用户
- 不得破坏已有功能，确保最终成品正常、稳定、高效地工作
- 源代码发生变更后，必须完成 Python 构建与核心冒烟测试，供用户审查变更效果

### 前端优化后质检

当代码更改涉及前端 UI 优化时，应在 `./tmp/img-frontend/run-{时间戳}` 目录下保存改动前后的对比截图（jpg 格式），利用模型的视觉智能判断任务完成情况来决定是否需要进一步优化。

## Codex CLI 特定说明

### 文件与输出

- 引用文件时使用可点击路径，并尽量带起始行号：`src/main.py:42`
- 不输出刚写入的大文件内容，只引用路径并说明变更
- 简单确认避免复杂格式；结束时给出简短后续步骤

### 编辑原则

- 只修改与当前任务直接相关的文件
- 保持现有代码风格、结构和类型安全
- 读取足够上下文后再批量处理相关修改
- 无效输入早返回，遵循项目既有日志/通知模式

## 变更记录与版本

- 影响项目行为、结构、工作流、工程原则或指令文件的变更，必须更新 `CHANGELOG.md`
- 修改 `AGENTS.md` 后，应同步检查 `CLAUDE.md` 的核心内容是否一致
- `CHANGELOG.md` 遵循 Keep a Changelog；优先记录到 `[Unreleased]`
- 如项目启用版本号，以配置文件为唯一来源，并遵循 SemVer：bug fix 递增修订号，新功能递增次版本号，破坏性变更递增主版本号

## 有机更新原则

当需要更新本文档时：

- 先理解变更意图，再把规则放到最合适的章节，避免重复堆叠
- 更新工作流、输出规范或术语时，同步检查相关示例、验证清单和引用位置
- 计划文档统一保存在 `./docs/plans/`
- 代码变化导致 `docs/` 中非 `plans/` 文档过时时，必须同步更新
- 文档标题不使用序号前缀，保持 Markdown 层级清晰
