# 研究层与统计 provenance

## 契约边界

Study、TrialPlan、Run、RawBundle、Event、Evaluation、Analysis 与 Index 使用独立 schema version。新写入只产生当前版本；v1 Study/TrialPlan 只在读取时生成带 warning 的 exploratory view，原文件保持不变。

TrialPlan 是分析意图的唯一冻结来源。它保存 scorer identity、metric contract、outcome policy、contrasts、alpha、随机次数与 seed；evaluation 和 inference 不接受运行时临时追加这些字段。

## 派生链

```text
TrialPlan + sealed RawBundles
          │
          ▼
blind EvaluationRecords + manifests
          │
          ▼
ObservationDataset + dataset manifest
          │
          ▼
AnalysisResult + report + analysis manifest
```

每层都只读上游、写入独立目录，并把输入摘要纳入稳定 ID。相同输入安全复用；同 ID 下文件摘要变化会失败，不会静默覆盖。SQLite 只保存可重建查询副本。

## 统计单位与有效性

episode 是唯一随机化与推断单位。评分重复会先聚合到 episode；subagent、token、测试用例、metric 维度和评委都不增加样本量。

确认性有效性同时依赖编译门禁、seal、评分完整性、预注册缺失策略、有效随机化单位数量与可选的一致性阈值。任一条件不满足时，事实数据仍可导出，但结果降级为 descriptive 或 `confirmatory_valid=false`。

## 安全边界

command scorer 不经 shell，默认看不到 arm、condition、ordinal 或 Prompt。编译器会冻结 shebang 实际解释器并把脚本转成 pinned staging input；依赖特定环境时应直接声明该环境的绝对解释器。只读 staging 是最小权限约定，不是操作系统沙箱；高风险 scorer 仍应在独立用户、容器或更强隔离中运行。blinding map 不传入 scorer，archive 可保留它用于完整复核。
