# Legacy 数据与运行环境清单

盘点日期：2026-07-31  
旧 Git HEAD：`0605a89707ea6225d391f9e8d35dd895608e3ac5`

## 仓库内 file store

- `data/store.json`：6 个 runs、1 个 prompt、3 个 environments、1 个 state。
- `data/runs`：12 个顶层 run 目录；检测到 2 个 `state-N` episode 目录和 11 个 session-id episode 目录。文件索引和目录可能存在历史不一致，导入时按 observed evidence 报告，不猜测缺失关系。
- `data/store.json` SHA-256：`ae9dc3430414c3542b9c5be2ebc6362c0afc7d1a20854973966303bb155f2e99`。
- 排除 `.git` 内部对象与 `.DS_Store` 后的数据文件校验清单摘要：`7055b78266006b98bf61832c1875d85120a93841f3dcaed5ca7e38e75b921742`。
- 2026-07-31 的 Python 重构未修改 `data/`。

## 目录收束复核

复核日期：2026-08-01

- 本地 file store 已从容易与当前数据根混淆的 `data/` 原样移动到被忽略的 `var/legacy-node/`；空的 `data/states/.gitkeep` 不属于 legacy 原件，已移除。
- 移动前后 inventory 均为 6 个索引 run、12 个磁盘 run、13 个磁盘 episode，其中 2 个 `state-N`、11 个 session-id；`store.json` SHA-256 均为 `ae9dc3430414c3542b9c5be2ebc6362c0afc7d1a20854973966303bb155f2e99`。
- 严格 validator 在移动前后均报告同一组 8 个不完整 episode。这些缺口是既有历史事实，迁移未补写、删除或猜测原始记录。
- `legacy inventory`、`legacy validate` 与 `legacy import` 现在要求调用者显式传入 source 路径，不再隐式依赖仓库根目录的 `data/`。

## 活跃旧栈

以下容器在盘点时运行，本次不停止、不覆盖：

- `llm-status-machine-app-1`：镜像 digest `sha256:de26672b...`
- `llm-status-machine-worker-1`：镜像 digest `sha256:7148c7c3...`
- `llm-status-machine-postgres-1`：镜像 digest `sha256:96d56f7f...`
- `llm-status-machine-redis-1`：镜像 digest `sha256:d146f83b...`

旧 volumes：`llm-status-machine_llm_status_data`、`llm-status-machine_llm_status_postgres`、`llm-status-machine_llm_status_redis`。另有 review 栈 volume，均不复用。

## 迁移边界

- 新 Python 默认数据根为 `.lsm/`，不复用旧栈 volumes。
- Legacy inventory/validate 默认只读，import 只读 source，并同时识别 `<run>/<session-id>` 与 `<run>/state-N`。
- 未发现仓库内消费者不等于不存在仓库外 API consumer；首版不重建旧 HTTP API。
- 历史开发诉求已归档为 `docs/history/development-requests.md`，不属于迁移器输入，也不得进入实验 diff。
