from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from typing import Generic, TypeVar

K = TypeVar("K")
V = TypeVar("V")


class AsyncTTLCache(Generic[K, V]):
    def __init__(
        self,
        *,
        max_size: int = 128,
        ttl: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.max_size = max_size
        self.ttl = ttl
        self._clock = clock
        self._values: OrderedDict[K, tuple[V, float]] = OrderedDict()
        self._inflight: dict[K, asyncio.Task[V]] = {}

    def get(self, key: K) -> V | None:
        item = self._values.get(key)
        if item is None:
            return None
        value, expires_at = item
        if expires_at <= self._clock():
            del self._values[key]
            return None
        self._values.move_to_end(key)
        return value

    async def get_or_load(self, key: K, loader: Callable[[], Awaitable[V]]) -> V:
        cached = self.get(key)
        if cached is not None:
            return cached
        task = self._inflight.get(key)
        if task is None:
            task = asyncio.create_task(loader())
            self._inflight[key] = task
        try:
            value = await task
            self._values[key] = (value, self._clock() + self.ttl)
            self._values.move_to_end(key)
            while len(self._values) > self.max_size:
                self._values.popitem(last=False)
            return value
        finally:
            self._inflight.pop(key, None)

    def invalidate(self, key: K) -> None:
        self._values.pop(key, None)

    def clear(self) -> None:
        self._values.clear()

