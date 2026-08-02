from __future__ import annotations

import unittest

from async_ttl_cache import AsyncTTLCache


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class AsyncTTLCachePublicTests(unittest.IsolatedAsyncioTestCase):
    async def test_caches_loader_result_until_ttl(self) -> None:
        clock = FakeClock()
        cache: AsyncTTLCache[str, int] = AsyncTTLCache(ttl=10, clock=clock)
        calls = 0

        async def load() -> int:
            nonlocal calls
            calls += 1
            return calls

        self.assertEqual(await cache.get_or_load("a", load), 1)
        self.assertEqual(await cache.get_or_load("a", load), 1)
        self.assertEqual(calls, 1)
        clock.now = 11
        self.assertEqual(await cache.get_or_load("a", load), 2)

    async def test_invalidate_and_clear_remove_cached_values(self) -> None:
        cache: AsyncTTLCache[str, int] = AsyncTTLCache()

        async def load() -> int:
            return 7

        await cache.get_or_load("a", load)
        await cache.get_or_load("b", load)
        cache.invalidate("a")
        self.assertIsNone(cache.get("a"))
        self.assertEqual(cache.get("b"), 7)
        cache.clear()
        self.assertIsNone(cache.get("b"))


if __name__ == "__main__":
    unittest.main()

