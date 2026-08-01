# Legacy 数据与运行环境清单

盘点日期：2026-07-31  
旧 Git HEAD：`0605a89707ea6225d391f9e8d35dd895608e3ac5`

## 仓库内 file store

- `data/store.json`：6 个 runs、1 个 prompt、3 个 environments、1 个 state。
- `data/runs`：12 个顶层 run 目录；检测到 2 个 `state-N` episode 目录和 11 个 session-id episode 目录。文件索引和目录可能存在历史不一致，导入时按 observed evidence 报告，不猜测缺失关系。
- `data/store.json` SHA-256：`ae9dc3430414c3542b9c5be2ebc6362c0afc7d1a20854973966303bb155f2e99`。
- 排除 `.git` 内部对象与 `.DS_Store` 后的数据文件校验清单摘要：`7055b78266006b98bf61832c1875d85120a93841f3dcaed5ca7e38e75b921742`。
- 本次重构不修改 `data/`。

## 活跃旧栈

以下容器在盘点时运行，本次不停止、不覆盖：

- `llm-status-machine-app-1`：镜像 digest `sha256:de26672b...`
- `llm-status-machine-worker-1`：镜像 digest `sha256:7148c7c3...`
- `llm-status-machine-postgres-1`：镜像 digest `sha256:96d56f7f...`
- `llm-status-machine-redis-1`：镜像 digest `sha256:d146f83b...`

旧 volumes：`llm-status-machine_llm_status_data`、`llm-status-machine_llm_status_postgres`、`llm-status-machine_llm_status_redis`。另有 review 栈 volume，均不复用。

## 迁移边界

- 新 Python 默认数据根为 `.lsm/`，不复用旧栈 volumes。
- Legacy inventory/validate/import 默认只读，并同时识别 `<run>/<session-id>` 与 `<run>/state-N`。
- 未发现仓库内消费者不等于不存在仓库外 API consumer；首版不重建旧 HTTP API。
- `Prompts.md` 与本次计划是用户已有未提交内容，不属于迁移器输入，也不得进入实验 diff。
