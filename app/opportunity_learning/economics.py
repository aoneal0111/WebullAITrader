"""Cost sensitivity for selected, hypothetical opportunities; never execution authority."""
from dataclasses import asdict, dataclass
from math import isfinite, sqrt
from statistics import mean, stdev

from .contracts import EvidenceStatus
from .dataset import latest_by_experience


@dataclass(frozen=True, slots=True)
class CostScenario:
    cost_r: float
    sample_size: int
    mean_net_r: float | None
    net_profit_factor: float | None
    maximum_drawdown_r: float | None
    approximate_mean_lower_bound_r: float | None


def selected_cost_sensitivity(challenger, examples, costs=(0.0, 0.10, 0.25, 0.50), threshold=.5):
    """Predeclared cost stress in R, not estimated real execution costs.

    Interval is a descriptive iid approximation, not a promotion criterion;
    clustered sessions and strategy selection require fresh independent data.
    Missing labels/probabilities remain unavailable, never profitable fills.
    """
    if not isfinite(threshold) or not 0 <= threshold <= 1:
        raise ValueError("threshold must be between zero and one")
    if any(not isfinite(cost) or cost < 0 for cost in costs):
        raise ValueError("cost scenarios must be finite and nonnegative")
    selected = []
    for item in latest_by_experience(tuple(examples)):
        prediction = challenger.predict(item.features)
        if (prediction.evidence_status is not EvidenceStatus.SUFFICIENT
                or prediction.probability is None or prediction.probability < threshold
                or item.labels is None or item.labels.expected_return_r is None):
            continue
        value = item.labels.expected_return_r
        if not isfinite(value):
            continue
        selected.append((item.features.decision_timestamp, item.features.experience_id, value))
    gross = [value for _, _, value in sorted(selected)]
    scenarios = []
    for cost in costs:
        net = [value - cost for value in gross]
        gains = sum(max(0, value) for value in net)
        losses = sum(max(0, -value) for value in net)
        equity = peak = drawdown = 0.0
        for value in net:
            equity += value
            peak = max(peak, equity)
            drawdown = max(drawdown, peak - equity)
        average = mean(net) if net else None
        lower = average - 1.96 * stdev(net) / sqrt(len(net)) if len(net) >= 2 else None
        scenarios.append(asdict(CostScenario(cost, len(net), average,
            gains / losses if losses else None, drawdown if net else None, lower)))
    return {"basis": "HYPOTHETICAL_PLAN_PATH_NOT_ACTUAL_EXECUTION",
            "threshold": threshold, "scenarios": scenarios,
            "execution_validated": False,
            "promotion_authorized": False}
