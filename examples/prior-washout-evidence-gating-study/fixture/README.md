# 符号回归资格测试 Workspace

这个目录是每个 episode 的只读基线。LLM Status Machine 会把它复制到隔离 workspace，三阶段资格 Harness 只在副本中写入 `protocol/` 与 `result/`。

`datasets/visible-tasks.json` 只包含训练和阶段 B 才开放的验证观测，不包含结构真值或 OOD 数据。`tools/evaluate_candidate.py` 使用 AST 白名单执行候选表达式；`tools/qualification_runner.py` 是无需模型的确定性协议替身，只用于验证记录、状态转换、盲化评分与研究分析链路，不能产生 LLM 行为结论。

