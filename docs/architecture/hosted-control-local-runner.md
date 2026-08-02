# Hosted Control 与本地 Runner：后续架构方向

当前版本是本地 Python CLI，不包含 Web UI、HTTP API、云控制面或多租户鉴权。完整 RawBundle 和 workspace 默认只留在本地数据根。

如果未来增加 Hosted Control，必须保持以下边界：

- 本地 runner 独占 workspace 访问、Harness 进程和完整 RawBundle。
- 云端只保存用户明确授权的计划索引、状态和指标。
- 任何 transcript、diff、artifact 或源文件上传都需要独立、可审计的授权。
- 部署者模型网关只能约束“通过平台发起的运行”，不能阻止用户在本机绕过平台使用其它工具。
- 使用远程模型时，实际发送给模型的上下文会离开本机，不能宣传为“整个过程完全不上云”。

该方向不属于 Python CLI 首版验收范围。未来若实施，应新增独立 ADR、runner 协议、短期凭据、内容分级与威胁模型，不应把旧 Node DevTools API 直接恢复为公网控制面。
