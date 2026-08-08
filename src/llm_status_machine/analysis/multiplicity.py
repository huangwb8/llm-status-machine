from __future__ import annotations


def holm_adjust(p_values: list[float]) -> list[float]:
    adjusted = [1.0] * len(p_values)
    running = 0.0
    for rank, index in enumerate(sorted(range(len(p_values)), key=p_values.__getitem__)):
        candidate = min(1.0, (len(p_values) - rank) * p_values[index])
        running = max(running, candidate)
        adjusted[index] = running
    return adjusted
