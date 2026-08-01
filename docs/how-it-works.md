# LLM Status Machine 工作过程

## 从 Study 到冻结计划

StudySpec 把 Prompt revisions、因素、重复次数、workspace fixture、Harness surface、runtime build、model endpoint 和 execution profile 放在同一个可校验文档里。编译器先生成 source workspace manifest，再展开 full factorial、matched pair 或 block 条件，最后用 seed 确定稳定顺序。

编译结果是 JSONL `TrialPlan`。首行是计划 header，后续每行是一个完整 Trial。runner 不会在执行时新增 episode、解析 `latest` 或重新随机分配条件。

`concurrency` 和 `state_policy` 是正交字段：

- `independent`：每个 episode 从同一个 frozen baseline 物化。
- `carry_forward`：并发必须为 1，下一轮只继承上一轮已完成并 sealed 的 workspace。
- `branch`：首版按共同 baseline 分支，保留独立拓扑语义。

## 执行 Attempt

单个 attempt 按以下顺序执行：

1. 重新校验 runtime executable SHA-256 和 source baseline。
2. 复制 source，排除声明的 cache/config 目录，不修改 source `.git`、refs 或 index。
3. 在 episode 副本内创建独立 snapshot Git repo并提交 initial state。
4. 将实际 Prompt bytes 写入 RawBundle，通过 adapter 生成 argv、stdin 和最小环境。
5. 使用 `asyncio.create_subprocess_exec` 启动独立进程组，不经过 shell。
6. stdout/stderr byte chunk 先追加到 raw 文件，再增量做 JSONL framing 和 canonical normalization。
7. 超时先终止进程组，宽限后强杀并 reap；失败仍进入 finally capture。
8. 保存 final manifest、binary/full-index diff、changed files、artifact manifest 和四类 outcome。
9. fsync 关键文件并生成 `seal.json`；只有 seal 和 outcome 都满足约束才标记 completed。

stdout 和 stderr 分别记录 stream sequence、byte offset/hash，并记录 recorder arrival sequence。它们不声称还原两个 OS pipe 的真实全序。未知 vendor event 标为 `vendor.unknown`；非法 UTF-8、半行或错误 JSON 标为 `protocol.parse_error`，原始字节仍完整保留。

## RawBundle

每个 bundle 包含：

```text
prompt.md
trial.json
runtime.json
launch.json
stdout.raw
stderr.raw
transcript.jsonl
metadata.json
outcomes.json
workspace.initial.json
workspace.final.json
changed-files.json
diff.patch
artifacts.json
artifacts/
seal.json
```

四类 attempt outcome 分别是：

- process：exit code、timeout 和 kill stage。
- protocol：terminal event 与 parser gap。
- capture：raw streams、transcript 和 metadata 是否完成。
- workspace：post snapshot、diff 与 artifact manifest 是否完成。

evaluation 写在 episode 的 `evaluations/`，包含 scorer version 和输入 bundle digest。`lsm evaluate` 会先验证 seal，评分后再次核对 digest。

## 索引、恢复与导出

`.lsm/index.sqlite3` 使用 WAL，只保存查询索引。`run.json`、`episode.json` 和 RawBundle 是事实来源；`lsm store reindex` 可以重建数据库。

进程意外终止时，未完成 run 保持 `running`，`lsm run reconcile` 会将其标为 `orphaned`，不会猜测 completed。导出支持 JSONL、CSV 和包含原始证据的 tar.gz。

## 安全边界

- 自定义命令不能以 shell 为入口，Prompt 中的 shell 元字符只是 argv 或 stdin 数据。
- 计划必须包含 absolute executable 与 digest；宿主 PATH 只用于显式 discovery，不能在运行时替换 runtime。
- 环境变量按名字 allowlist，launch metadata 只记录名字和 argv digest。
- workspace、legacy 与 artifact 路径在访问前必须仍位于允许根目录。
- native runtime 和 uv 都不等同安全沙箱；确认性实验仍需实际控制网络、用户、文件权限和资源。
