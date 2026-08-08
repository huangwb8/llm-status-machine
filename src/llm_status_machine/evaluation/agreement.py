from __future__ import annotations

from collections.abc import Sequence
from typing import Any


def krippendorff_alpha(rows: Sequence[Sequence[Any]], metric_type: str) -> float | None:
    """Return Krippendorff's alpha for unit-by-rater values, ignoring missing ratings."""
    clean = [
        values
        for row in rows
        if len(values := [value for value in row if value is not None]) >= 2
    ]
    values = [value for row in clean for value in row]
    expected_pairs = [(left, right) for index, left in enumerate(values) for right in values[index + 1 :]]
    if not clean or not expected_pairs:
        return None
    categories = {value: rank for rank, value in enumerate(sorted(set(values), key=str))}

    def distance(left: Any, right: Any) -> float:
        if metric_type in {"nominal", "binary"}:
            return float(left != right)
        if metric_type == "ordinal":
            denominator = max(len(categories) - 1, 1)
            return ((categories[left] - categories[right]) / denominator) ** 2
        return (float(left) - float(right)) ** 2

    observed = (
        sum(
            2
            * sum(distance(left, right) for index, left in enumerate(row) for right in row[index + 1 :])
            / (len(row) - 1)
            for row in clean
        )
        / len(values)
    )
    expected = sum(distance(*pair) for pair in expected_pairs) / len(expected_pairs)
    if expected == 0:
        return 1.0 if observed == 0 else None
    return 1.0 - observed / expected
