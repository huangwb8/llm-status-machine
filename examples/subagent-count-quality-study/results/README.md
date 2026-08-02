# Pilot 结果摘要

实际 run：`run-856b289d45e9356b5377`，顶层顺序为 3 → 6 → 9 个 requested evaluator，三个 episode 使用独立 workspace、相同 Codex 0.144.0、`gpt-5.6-sol`、medium、workspace-write、ephemeral 与 30 分钟 timeout。该固定升序使 evaluator 数与运行位置、时间及潜在 API 状态完全混杂。

| Requested evaluator | Completed evaluator | Summary | Episode | Oracle code score | 交付门禁质量分 | Wall time |
|---:|---:|---:|---|---:|---:|---:|
| 3 | 3 | 1 | completed | 100 | 100 | 1660.663 s |
| 6 | 5 | 1 | completed | 100 | 100 | 1769.974 s |
| 9 | 9 | 0 | timed_out | 100 | 0 | 1800.022 s |

三个 sealed final commit 均通过全部预注册 oracle 类别，包括 invalidate/clear 后新请求必须进入新世代的竞态，说明当前任务出现功能分天花板；当前 rollout 没有观察到 evaluator 增加所对应的额外代码正确性收益。9-evaluator 条件在完成全部评估后未能在固定预算内完成总结和终态协议，因此端到端交付失败。但由于它同时位于最后一个运行位置，这次超时不能归因于 evaluator 数量。当前单次/组结果只提示“边际收益可能快速饱和、协调成本值得重点检验”，不支持“越多越好”或相反的因果结论。

这个结论只适用于本机、当前 Codex build、当前时间窗与单个 AsyncTTLCache fixture。`pilot.csv` 是 episode 级 tidy 数据；`pilot.manifest.json` 保留 scorer 版本、bundle digest、sealed workspace digest、实际被评分的 commit/content digest 和逐测试证据。完整原始过程位于本地已忽略的 `../.lsm/`，不会提交 Git。
