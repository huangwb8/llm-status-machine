# 错误先验洗脱与证据门控三阶段任务

你将通过一个外部 orchestrator 依次完成 A（先验诱导）、B（洗脱与独立证据）和 C（恢复与提交）三个全新上下文阶段。候选必须使用公开 evaluator，隐藏 OOD 与结构真值不可查询。

<!-- PRIOR_START -->
PRIOR_CONDITION={{PRIOR_CONDITION}}
{{PRIOR_TEXT}}
<!-- PRIOR_END -->

阶段 B/C 不会再次提供阶段 A 的先验。每个任务的候选预算、runtime、模型端点、可见数据和工具权限完全相同。

<!-- MEMORY_START -->
MEMORY_POLICY={{MEMORY_POLICY}}
{{MEMORY_TEXT}}
<!-- MEMORY_END -->

在阶段 C 提交每个任务的最终表达式、置信度和未解决问题。不要声称看过隐藏数据；失败和无效候选也必须保留。

