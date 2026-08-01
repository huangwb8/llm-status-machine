# 从 Node/Web 版本迁移

## 原则

迁移采用 shadow import，不做就地 schema 修改或双写。旧服务、镜像、Postgres/Redis 和 file store 在确认新 CLI 后由操作者自行归档；Python 工具不会停止旧容器或删除旧 volumes。

## 操作

```bash
uv run lsm legacy inventory data --json
uv run lsm legacy validate data --json
uv run lsm init .
uv run lsm legacy import data --data-root .lsm --json
uv run lsm store verify --data-root .lsm --json
```

validator 同时识别 `<run>/<session-id>` 与 `<run>/state-N`。导入保持原始文件 bytes 和 observed path，缺失的 runtime/model snapshot 标为 `unknown`，不会从当前宿主环境推断历史事实。

## 对账

- 比较 inventory 中 disk run/episode 数量和 import report。
- 对源 `store.json` 与数据文件校验摘要留档。
- 对每个导入 bundle 运行 seal verify。
- 使用全新 `.lsm/` 数据根运行 Simulator smoke。

任一 count、checksum、seal、版本或核心 smoke 失败时停止切换。回滚只需继续使用旧容器与旧数据；不要把新 RawBundle 降级写回旧 store。
