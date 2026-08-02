# 从 Node/Web 版本迁移

## 原则

迁移采用 shadow import，不做就地 schema 修改或双写。旧服务、镜像、Postgres/Redis 和 file store 在确认新 CLI 后由操作者自行归档；Python 工具不会停止旧容器或删除旧 volumes。

## 操作

命令不猜测旧数据位置，必须显式提供 source。仓库旁的本地暂存约定为被忽略的 `var/legacy-node/`；也可以使用仓库外绝对路径。

```bash
LEGACY_SOURCE=/absolute/path/to/legacy-node

uv run lsm legacy inventory "$LEGACY_SOURCE" --json
uv run lsm legacy validate "$LEGACY_SOURCE" --json
uv run lsm init .
uv run lsm legacy import "$LEGACY_SOURCE" --data-root .lsm --json
uv run lsm store verify --data-root .lsm --json
```

validator 同时识别 `<run>/<session-id>` 与 `<run>/state-N`。它会严格报告缺少的 legacy 记录文件并返回非零状态；先保存报告并确认缺口符合历史盘点，再决定是否按 observed evidence 导入。导入保持现有文件 bytes 和 observed path，缺失的 runtime/model snapshot 标为 `unknown`，不会从当前宿主环境推断历史事实。

## 对账

- 比较 inventory 中 disk run/episode 数量和 import report。
- 对源 `store.json`、文件数与数据文件校验摘要留档；移动本地暂存时在移动前后各核对一次。
- 对每个导入 bundle 运行 seal verify。
- 使用全新 `.lsm/` 数据根运行 Simulator smoke。

任一 count、checksum、seal、版本或核心 smoke 失败时停止切换。回滚只需继续使用旧容器与旧数据；不要把新 RawBundle 降级写回旧 store。
