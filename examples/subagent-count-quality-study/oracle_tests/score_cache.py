from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import subprocess
import sys
import tomllib
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

WEIGHTS = {
    "basic_ttl": 15,
    "lru_capacity": 15,
    "single_flight": 20,
    "failure_cancel": 20,
    "invalidation_race": 20,
    "api_quality": 10,
}


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def load_cache_class(workspace: Path) -> type[Any]:
    source = workspace / "async_ttl_cache.py"
    spec = importlib.util.spec_from_file_location("candidate_async_ttl_cache", source)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load async_ttl_cache.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.AsyncTTLCache


async def basic_hit_and_expiry(cache_type: type[Any]) -> None:
    clock = FakeClock()
    cache = cache_type(ttl=2, clock=clock)
    calls = 0

    async def load() -> int:
        nonlocal calls
        calls += 1
        return calls

    assert await cache.get_or_load("x", load) == 1
    assert await cache.get_or_load("x", load) == 1
    clock.now = 3
    assert cache.get("x") is None
    assert await cache.get_or_load("x", load) == 2
    assert calls == 2


async def positive_parameters(cache_type: type[Any]) -> None:
    for kwargs in ({"max_size": 0}, {"max_size": -1}, {"ttl": 0}, {"ttl": -1}):
        try:
            cache_type(**kwargs)
        except ValueError:
            continue
        raise AssertionError(f"expected ValueError for {kwargs}")


async def lru_eviction(cache_type: type[Any]) -> None:
    cache = cache_type(max_size=2)
    calls: dict[str, int] = {}

    async def load(key: str) -> str:
        calls[key] = calls.get(key, 0) + 1
        return f"{key}-{calls[key]}"

    await cache.get_or_load("a", lambda: load("a"))
    await cache.get_or_load("b", lambda: load("b"))
    assert cache.get("a") == "a-1"
    await cache.get_or_load("c", lambda: load("c"))
    assert cache.get("b") is None
    assert await cache.get_or_load("a", lambda: load("a")) == "a-1"


async def expired_entries_do_not_displace_live_lru(cache_type: type[Any]) -> None:
    clock = FakeClock()
    cache = cache_type(max_size=2, ttl=2, clock=clock)

    async def load(value: int) -> int:
        return value

    await cache.get_or_load("old", lambda: load(1))
    clock.now = 1
    await cache.get_or_load("live", lambda: load(2))
    clock.now = 2.5
    await cache.get_or_load("new", lambda: load(3))
    assert cache.get("old") is None
    assert cache.get("live") == 2
    assert cache.get("new") == 3


async def same_key_single_flight(cache_type: type[Any]) -> None:
    cache = cache_type()
    calls = 0
    release = asyncio.Event()

    async def load() -> int:
        nonlocal calls
        calls += 1
        await release.wait()
        return 42

    waiters = [asyncio.create_task(cache.get_or_load("x", load)) for _ in range(20)]
    await asyncio.sleep(0)
    release.set()
    assert await asyncio.gather(*waiters) == [42] * 20
    assert calls == 1


async def different_keys_are_independent(cache_type: type[Any]) -> None:
    cache = cache_type()
    active = 0
    peak = 0
    both_started = asyncio.Event()
    release = asyncio.Event()

    async def load(value: int) -> int:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        if active == 2:
            both_started.set()
        await release.wait()
        active -= 1
        return value

    first = asyncio.create_task(cache.get_or_load("a", lambda: load(1)))
    second = asyncio.create_task(cache.get_or_load("b", lambda: load(2)))
    await asyncio.wait_for(both_started.wait(), timeout=1)
    release.set()
    assert await asyncio.gather(first, second) == [1, 2]
    assert peak == 2


async def loader_failure_is_retryable(cache_type: type[Any]) -> None:
    cache = cache_type()
    calls = 0

    async def load() -> int:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("boom")
        return 9

    try:
        await cache.get_or_load("x", load)
    except RuntimeError:
        pass
    else:
        raise AssertionError("loader failure must propagate")
    assert await cache.get_or_load("x", load) == 9
    assert calls == 2


async def waiter_cancellation_is_isolated(cache_type: type[Any]) -> None:
    cache = cache_type()
    started = asyncio.Event()
    release = asyncio.Event()

    async def load() -> int:
        started.set()
        await release.wait()
        return 11

    cancelled = asyncio.create_task(cache.get_or_load("x", load))
    await started.wait()
    survivor = asyncio.create_task(cache.get_or_load("x", load))
    await asyncio.sleep(0)
    cancelled.cancel()
    try:
        await cancelled
    except asyncio.CancelledError:
        pass
    release.set()
    assert await survivor == 11
    assert cache.get("x") == 11


async def invalidation_blocks_stale_fill(cache_type: type[Any]) -> None:
    cache = cache_type()
    started = asyncio.Event()
    release = asyncio.Event()

    async def load() -> int:
        started.set()
        await release.wait()
        return 1

    pending = asyncio.create_task(cache.get_or_load("x", load))
    await started.wait()
    cache.invalidate("x")
    release.set()
    assert await pending == 1
    assert cache.get("x") is None


async def invalidation_starts_a_new_generation(cache_type: type[Any]) -> None:
    cache = cache_type()
    old_started = asyncio.Event()
    old_release = asyncio.Event()
    new_started = asyncio.Event()
    new_release = asyncio.Event()

    async def old_load() -> str:
        old_started.set()
        await old_release.wait()
        return "old"

    async def new_load() -> str:
        new_started.set()
        await new_release.wait()
        return "new"

    old_waiter = asyncio.create_task(cache.get_or_load("x", old_load))
    await old_started.wait()
    cache.invalidate("x")
    new_waiter = asyncio.create_task(cache.get_or_load("x", new_load))
    await asyncio.wait_for(new_started.wait(), timeout=1)
    old_release.set()
    assert await old_waiter == "old"
    new_release.set()
    assert await new_waiter == "new"
    assert cache.get("x") == "new"


async def clear_blocks_all_stale_fills(cache_type: type[Any]) -> None:
    cache = cache_type()
    started = asyncio.Event()
    release = asyncio.Event()

    async def load() -> int:
        started.set()
        await release.wait()
        return 5

    pending = asyncio.create_task(cache.get_or_load("x", load))
    await started.wait()
    cache.clear()
    release.set()
    assert await pending == 5
    assert cache.get("x") is None


async def clear_starts_new_generations(cache_type: type[Any]) -> None:
    cache = cache_type()
    old_a_started = asyncio.Event()
    old_b_started = asyncio.Event()
    old_release = asyncio.Event()
    new_release = asyncio.Event()

    async def old_load(started: asyncio.Event, value: str) -> str:
        started.set()
        await old_release.wait()
        return value

    async def new_load(value: str) -> str:
        await new_release.wait()
        return value

    old_a = asyncio.create_task(cache.get_or_load("a", lambda: old_load(old_a_started, "old-a")))
    old_b = asyncio.create_task(cache.get_or_load("b", lambda: old_load(old_b_started, "old-b")))
    await asyncio.gather(old_a_started.wait(), old_b_started.wait())
    cache.clear()
    new_a = asyncio.create_task(cache.get_or_load("a", lambda: new_load("new-a")))
    new_b = asyncio.create_task(cache.get_or_load("b", lambda: new_load("new-b")))
    await asyncio.sleep(0)
    old_release.set()
    assert await asyncio.gather(old_a, old_b) == ["old-a", "old-b"]
    new_release.set()
    assert await asyncio.gather(new_a, new_b) == ["new-a", "new-b"]
    assert cache.get("a") == "new-a"
    assert cache.get("b") == "new-b"


def public_tests_pass(workspace: Path) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-q"],
        cwd=workspace,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr[-1000:])


def no_runtime_dependencies(workspace: Path) -> None:
    project = tomllib.loads((workspace / "pyproject.toml").read_text(encoding="utf-8"))
    assert project["project"].get("dependencies", []) == []


async def score(workspace: Path) -> dict[str, Any]:
    cache_type = load_cache_class(workspace)
    checks: list[tuple[str, str, Callable[[], Awaitable[None]]]] = [
        ("basic_hit_and_expiry", "basic_ttl", lambda: basic_hit_and_expiry(cache_type)),
        ("positive_parameters", "basic_ttl", lambda: positive_parameters(cache_type)),
        ("lru_eviction", "lru_capacity", lambda: lru_eviction(cache_type)),
        (
            "expired_entries_do_not_displace_live_lru",
            "lru_capacity",
            lambda: expired_entries_do_not_displace_live_lru(cache_type),
        ),
        ("same_key_single_flight", "single_flight", lambda: same_key_single_flight(cache_type)),
        (
            "different_keys_are_independent",
            "single_flight",
            lambda: different_keys_are_independent(cache_type),
        ),
        ("loader_failure_is_retryable", "failure_cancel", lambda: loader_failure_is_retryable(cache_type)),
        (
            "waiter_cancellation_is_isolated",
            "failure_cancel",
            lambda: waiter_cancellation_is_isolated(cache_type),
        ),
        (
            "invalidation_blocks_stale_fill",
            "invalidation_race",
            lambda: invalidation_blocks_stale_fill(cache_type),
        ),
        (
            "invalidation_starts_a_new_generation",
            "invalidation_race",
            lambda: invalidation_starts_a_new_generation(cache_type),
        ),
        (
            "clear_blocks_all_stale_fills",
            "invalidation_race",
            lambda: clear_blocks_all_stale_fills(cache_type),
        ),
        (
            "clear_starts_new_generations",
            "invalidation_race",
            lambda: clear_starts_new_generations(cache_type),
        ),
    ]
    results = []
    for name, category, check in checks:
        try:
            await check()
            results.append({"name": name, "category": category, "passed": True, "error": ""})
        except (Exception, asyncio.CancelledError) as error:  # noqa: BLE001 - score candidate failures
            results.append(
                {
                    "name": name,
                    "category": category,
                    "passed": False,
                    "error": f"{type(error).__name__}: {error}"[:500],
                }
            )
    for name, check in (
        ("public_tests_pass", lambda: public_tests_pass(workspace)),
        ("no_runtime_dependencies", lambda: no_runtime_dependencies(workspace)),
    ):
        try:
            check()
            results.append({"name": name, "category": "api_quality", "passed": True, "error": ""})
        except Exception as error:  # noqa: BLE001 - score candidate failures
            results.append(
                {
                    "name": name,
                    "category": "api_quality",
                    "passed": False,
                    "error": f"{type(error).__name__}: {error}"[:500],
                }
            )
    category_scores = {}
    for category, weight in WEIGHTS.items():
        selected = [item for item in results if item["category"] == category]
        category_scores[category] = weight * sum(item["passed"] for item in selected) / len(selected)
    return {
        "scorer_version": "async-ttl-cache-v2",
        "score": round(sum(category_scores.values()), 2),
        "category_scores": category_scores,
        "tests": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("workspace", type=Path)
    args = parser.parse_args()
    print(json.dumps(asyncio.run(score(args.workspace.resolve())), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
