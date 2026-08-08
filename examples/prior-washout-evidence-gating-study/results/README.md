# 结果目录

本目录不提交本地 RawBundle、运行时锁、模型输出或未脱敏结果。`scripts/run_qualification.py` 默认把全部输出写到调用者指定的 `./tmp` 子目录；其中 `qualification-summary.json` 是确定性基础设施资格摘要，不是错误先验洗脱的行为学结论。

只有在真实 nested LLM runtime、Prompt 操纵、隐藏数据隔离和 pilot 方差全部冻结后，才应把脱敏的 episode 级结果复制到这里。
