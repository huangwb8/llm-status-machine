from __future__ import annotations

import math
from typing import Any


def estimate_power(
    *,
    metric_type: str,
    effect: float,
    alpha: float,
    power: float,
    standard_deviation: float | None = None,
    baseline_rate: float | None = None,
) -> dict[str, Any]:
    try:
        from scipy.stats import norm
    except ImportError as error:  # pragma: no cover
        raise RuntimeError("power calculation requires: uv sync --extra analysis") from error
    if effect <= 0:
        raise ValueError("minimum meaningful effect must be positive")
    if not 0 < alpha < 1 or not 0 < power < 1:
        raise ValueError("alpha and power must be between 0 and 1")
    z = norm.ppf(1 - alpha / 2) + norm.ppf(power)
    if metric_type == "continuous":
        if standard_deviation is None or standard_deviation <= 0:
            raise ValueError("continuous power requires a positive standard deviation")
        per_arm = math.ceil(2 * (standard_deviation * z / effect) ** 2)
        assumptions = {"standard_deviation": standard_deviation}
    elif metric_type == "binary":
        if baseline_rate is None or not 0 < baseline_rate < 1:
            raise ValueError("binary power requires baseline_rate between 0 and 1")
        treatment_rate = baseline_rate + effect
        if not 0 < treatment_rate < 1:
            raise ValueError("baseline_rate + effect must be between 0 and 1")
        pooled = (baseline_rate + treatment_rate) / 2
        numerator = (
            norm.ppf(1 - alpha / 2) * math.sqrt(2 * pooled * (1 - pooled))
            + norm.ppf(power)
            * math.sqrt(baseline_rate * (1 - baseline_rate) + treatment_rate * (1 - treatment_rate))
        ) ** 2
        per_arm = math.ceil(numerator / effect**2)
        assumptions = {"baseline_rate": baseline_rate, "treatment_rate": treatment_rate}
    else:
        raise ValueError("metric_type must be continuous or binary")
    return {
        "status": "completed",
        "confirmatory_valid": None,
        "metric_type": metric_type,
        "minimum_meaningful_effect": effect,
        "alpha": alpha,
        "power": power,
        "required_episodes_per_arm": per_arm,
        "required_episodes_total": per_arm * 2,
        "assumptions": assumptions,
        "warnings": ["result assumes two independent, equally sized prompt arms"],
        "errors": [],
    }
