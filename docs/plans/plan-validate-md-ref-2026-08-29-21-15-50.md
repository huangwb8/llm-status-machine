# validate-md-ref 与 bensz-skill-kernel 优化计划

## 背景与结论

本计划基于任务目录
`.bensz-api/task-validate-md-ref-2026-08-29-21-15-50/` 的一次真实运行，以及
`/Volumes/2T01/Github/skills/packages/bensz-skill-kernel` 和
`/Volumes/2T01/Github/skills/skills/beta/validate-md-ref` 的源代码核对。

结论是：两部分本次都实际生效，但目前是“状态快照治理”和“事件账本中的
Verifier 事实”两条相邻链路，尚未成为一个不可绕过的统一执行入口。本次运行
并未改动源 Markdown；11 个站内锚点通过，13 个外链因当前环境 DNS 无法解析而
被记录为失败，链接 Gate 为 `reject`，语义引用 Verifier 为 `unchecked`。

## 已确认的问题

### 强制状态机可以被直接脚本调用绕过（高优先级）

`validate-md-ref/SKILL.md` 和 README 声明每次运行必须经过状态机，但
`scripts/validate_links.py` 的 CLI 只在传入 `--events` 时记录 Verifier 事件，
没有要求任务根目录、没有读取 `meta-state.json`，也没有执行
`input-ready → checking → reported` 转移。因而直接运行
`python3 scripts/validate_links.py file.md` 可以产生完整 JSON，却完全没有状态
机门禁。这与“强制运行门禁”及“每次运行都会强制经过”相矛盾。

**优化方向**

- 增加统一的 `run`/执行器入口：打开任务工作区，校验并转移
  `input-ready`、`checking`，运行链接 Verifier 和语义 Verifier，写入事件，最后
  只在报告已生成且不确定性已披露时转移到 `reported`。
- 将现有脚本改为该入口的薄适配器；若保留无工作区的诊断模式，必须明确标记为
  非交付运行，不能声称满足强制门禁。
- 为“缺少工作区、非法顺序、Verifier/Gate 写入失败”增加失败测试，并验证源文件
  哈希不变。

### Skill 与 Kernel 存在两套可产生不同事实的实现（中高优先级）

`validate_links.py` 仍公开保留 `extract_references`、`validate_references` 和
`validate_url`（其中使用 curl），而 Kernel Pack 的
`verifiers/markdown-link-integrity/scripts/collector.py` 使用另一套正则、相对文件
逻辑、重定向和 SSRF 检查。当前 CLI 已选择 Kernel 结果作为唯一事实，这是正确的
缓解措施；但直接导入旧函数的调用者仍可能得到不同的引用数量、跳过状态或 URL
结果，且没有一致性契约。

**优化方向**

- 删除旧网络/解析实现，或让兼容函数严格委托 Kernel collector，并标记弃用期限。
- 将解析和探测行为的测试集中在 Pack；增加适配器与 Pack 的结果等价性测试（标准
  链接、HTML href、相对文件、fragment、重定向和黑/白名单）。
- 文档中明确唯一事实来源和兼容 API 的版本策略，避免用户误把 legacy 结果写入
  账本。

### 网络环境错误被当作确定性链接失败（中优先级）

本次 13 条外链全部返回 `nodename nor servname provided`。collector 将这类
`URLError/OSError` 留在 `valid=false`，Verifier 随后输出 `verdict=fail`，Gate
因此为 `reject`。这能阻止无证据放行，但把“无法探测”与“HTTP 404/500”混成同一
类事实，导致离线、DNS 故障或临时网络故障被误报为坏链接。现有摘要虽人工补充了
“不能据此断定网页失效”，结构化 Gate 却没有保留这种不确定性。

**优化方向**

- 在 collector 结果中区分 `probe_error`（DNS、连接、TLS、超时）与确认的 HTTP
  状态失败，并保留错误类型和重试建议。
- 默认将环境性探测错误规范化为 `uncertain`/`manual_review`；确认的 4xx/5xx
  仍为 `fail`。如需严格 CI 阻断，增加显式 `strict_reachability` 配置而非隐式
  混用。
- 为 DNS 失败、超时、403/405 GET 回退、确认 404 分别增加 verifier 和 Gate
  测试，确保指标能区分 `invalid` 与 `uncertain`。

### 运行时解释器可能发生版本漂移（中优先级）

`validate_links.py` 的 `_load_verifier_runtime()` 强制把仓库相对的 Kernel
`src` 插入 `sys.path`；记录事件的 `_kernel_command()` 却优先选择 PATH 中任意
`bsk`。因此同一进程可能用一份源码运行 Verifier，再用另一份已安装包写事件。
本次环境恰好显示版本均为 0.11.0 且代表性哈希一致，未触发错误，但这不是代码
层面的保证。

**优化方向**

- 默认使用当前 `sys.executable -m bensz_skill_kernel.cli` 写事件，或在选择外部
  `bsk` 前校验其模块路径、版本和 Pack 哈希与当前 registry 一致。
- 在结果和事件中记录 Kernel 包版本、模块路径和 Pack/State 版本，发现不一致时
  直接失败，不生成“看似完整”的账本。
- 增加双环境/不同 PATH 的集成测试。

### 辅助定位的环境变量名称有明显拼写问题（低优先级）

脚本读取的是 `VALIDATE_MD_REF Skill_PATH`（含空格），这不是正常 shell 环境
变量命名；该分支目前通常被 `__file__` 定位或常见安装路径掩盖，属于失效的备用
入口。

**优化方向**

- 改为稳定的 `VALIDATE_MD_REF_SKILL_PATH`，短期兼容旧键但发出弃用提示。
- 对安装目录、仓库源码目录和不可定位场景分别增加路径解析测试。

## 不应误判为缺陷的事项

- 本次外链失败主要由运行环境 DNS 限制造成；不能据此断言 13 个网页已失效。
- `meta-state.json` 与 `events.ndjson` 分层是 Kernel README 明确的架构选择；当前
  `bsk status` 不展示 Skill 元状态属于接口边界问题，不应把生命周期投影误当成
  状态快照损坏。后续可考虑增加只读联合查询，但不应破坏现有分层。
- 现有 Kernel 与 beta QA 测试共 86 项全部通过，说明上述问题是契约/集成缺口，
  不是当前测试集中的回归失败。

## 实施顺序与验收

1. 先实现统一执行入口和不可绕过的状态机门禁，补齐失败路径测试。
2. 再收敛 legacy 解析/网络实现，建立 Pack 唯一事实源和等价性测试。
3. 增加网络不确定性分类及严格模式配置，更新 Gate、摘要和文档示例。
4. 最后固定 Python/Kernel 运行时解析，补充版本与哈希一致性审计字段。
5. 完成后运行 Kernel 全量测试、beta skill QA、无网络环境回归，以及项目要求的
   核心冒烟测试；确认事件哈希链完整、状态顺序可重放、报告与源文档哈希一致。

本轮只提交本计划，没有直接修改上述源代码；实施时仍需按项目变更规范更新
`CHANGELOG.md`。
