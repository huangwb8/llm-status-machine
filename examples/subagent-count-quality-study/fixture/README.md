# AsyncTTLCache repair task

修复 `async_ttl_cache.py` 中的 `AsyncTTLCache`，只使用 Python 标准库并保持现有公开 API。

必须满足：

- 每个条目按可注入的 monotonic clock 和 TTL 过期；命中会更新 LRU 顺序。
- 容量达到 `max_size` 时逐出最近最少使用的有效条目。
- 同一 key 的并发 miss 只调用一次异步 loader，所有等待者共享结果。
- loader 失败不缓存；一个等待者被取消时不得取消共享 loader 或其他等待者。
- `invalidate(key)` 和 `clear()` 在 loader 运行期间调用后，旧 loader 可以向既有等待者返回结果，但不得把旧值重新写回缓存。
- `max_size` 和 `ttl` 必须为正数；`get`、`get_or_load`、`invalidate`、`clear` 的行为应保持确定性。

运行公开测试：

```bash
python -m unittest discover -s tests -v
```

