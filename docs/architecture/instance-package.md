# LSM 实例包契约

LSM 实例包是一个可以被发现、检查和独立运行的实验源码目录。它描述“实验源码如何组织”，与 `.lsm/` 中的 TrialPlan、Run、Episode 和 RawBundle 运行数据不是同一层契约。

## 标准目录

每个实例必须使用以下基础结构：

```text
<instance-id>/
├── lsm.yml
├── README.md
├── prompts/
├── fixture/
├── harness/
├── oracle_tests/
├── scripts/
└── results/
```

六个组件目录始终存在，名称和职责固定：

- `prompts/`：Prompt revision、模板或动态 Prompt 的说明；
- `fixture/`：episode 的 source workspace，或动态 fixture 的说明；
- `harness/`：实例专用 worker、adapter 和 runtime 辅助文件；
- `oracle_tests/`：scorer、隐藏测试或“不使用业务 oracle”的说明；
- `scripts/`：prepare、run、verify 和单 episode smoke 入口；
- `results/`：可以提交的脱敏结果摘要，不保存本地 RawBundle。

实例可以按需声明 `analysis/`、`assets/` 和 `docs/` 三个标准扩展目录。不得通过自定义顶层组件名改变上述职责。`.lsm/`、`tmp/` 和 `exports/` 是本地运行产物；校验器允许它们存在，但会报告 warning。

## `lsm.yml`

实例根目录必须包含 schema 化的 `lsm.yml`：

```yaml
schema_version: 1
id: example-study
kind: study
study:
  mode: generated
  path: study.yml
  generator: scripts/prepare_study.py
components:
  prompts: prompts
  fixture: fixture
  harness: harness
  oracle_tests: oracle_tests
  scripts: scripts
  results: results
smoke:
  script: scripts/smoke.py
  expected_episodes: 1
external_dependencies: []
```

`id` 必须使用小写连字符形式并与目录名一致。`kind` 可取 `study`、`integration` 或 `benchmark`。

Study 来源有三种模式：

- `static`：`path` 指向已经存在的 StudySpec YAML；
- `generated`：`generator` 指向生成器，`path` 是生成后的标准文件名；
- `embedded`：`entrypoint` 指向在 Python 中构造 StudySpec 的标准入口。

所有清单路径必须是实例根目录内的相对路径。解析后的路径不得通过 `..` 或符号链接逃逸实例目录。冻结 TrialPlan 时仍使用 LSM 原有的绝对路径与摘要门禁；实例包契约不会削弱运行期身份固定。

## 单 episode smoke

每个实例必须提供 `scripts/smoke.py`，并在 `lsm.yml` 中声明 `expected_episodes: 1`。标准调用形式是：

```bash
python examples/<instance-id>/scripts/smoke.py --root tmp/<instance-id>-smoke
```

smoke 必须经过至少 `StudySpec → TrialPlan → RunEngine`，并以 `completed` episode 和有效 RawBundle seal 作为成功条件。真实模型或外部服务不是 smoke 的默认依赖；需要真实运行的实例应提供本地 Simulator、确定性 qualification 或 dry-run 路径。

## CLI

创建标准骨架：

```bash
uv run lsm example init examples/my-study --id my-study --kind study --json
```

验证实例：

```bash
uv run lsm example validate examples/my-study --json
```

校验覆盖清单 schema、目录名、Study 来源、smoke 入口、路径 containment 和本地运行产物 warning。实例自己的自动化测试仍负责证明业务协议、评分和研究设计正确。
