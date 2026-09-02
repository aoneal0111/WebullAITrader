"""Deterministic challenger metrics and factual champion comparison."""

from __future__ import annotations

from dataclasses import dataclass
from math import log

from .contracts import EvidenceStatus, LearningExample
from .dataset import latest_by_experience, target_value


@dataclass(frozen=True, slots=True)
class CalibrationBucket:
    lower: float
    upper: float
    count: int
    mean_probability: float | None
    observed_rate: float | None


@dataclass(frozen=True, slots=True)
class EvaluationMetrics:
    sample_size: int
    positive_rate: float | None
    auc: float | None
    pr_auc: float | None
    brier_score: float | None
    log_loss: float | None
    calibration_error: float | None
    calibration: tuple[CalibrationBucket, ...]
    win_rate: float | None
    loss_rate: float | None
    average_r: float | None
    median_r: float | None
    maximum_drawdown_r: float | None
    profit_factor: float | None
    tail_loss_r: float | None
    symbol_concentration: tuple[tuple[str, int], ...]
    date_concentration: tuple[tuple[str, int], ...]
    session_concentration: tuple[tuple[str, int], ...]
    setup_concentration: tuple[tuple[str, int], ...]


@dataclass(frozen=True, slots=True)
class ChampionComparison:
    same_opportunities: int
    champion_only: int
    challenger_only: int
    both: int
    neither: int
    outcomes_by_group: tuple[tuple[str, int, float | None], ...]


def evaluate_challenger(challenger, examples: tuple[LearningExample, ...]) -> EvaluationMetrics:
    usable = [item for item in latest_by_experience(examples) if item.labels is not None]
    predicted = [(item, challenger.predict(item.features)) for item in usable]
    predicted = [(item, prediction) for item, prediction in predicted if prediction.evidence_status is EvidenceStatus.SUFFICIENT]
    y = [float(target_value(item.labels, challenger.target)) for item, _ in predicted]
    p = [prediction.probability for _, prediction in predicted]
    returns = [item.labels.expected_return_r for item, _ in predicted if item.labels.expected_return_r is not None]
    return EvaluationMetrics(
        len(predicted), _mean(y), _auc(y, p), _pr_auc(y, p),
        _mean([(a - b) ** 2 for a, b in zip(y, p)]),
        _mean([-(a * log(max(b, 1e-15)) + (1 - a) * log(max(1 - b, 1e-15))) for a, b in zip(y, p)]),
        _ece(y, p), _calibration(y, p),
        None if not returns else sum(value > 0 for value in returns) / len(returns),
        None if not returns else sum(value < 0 for value in returns) / len(returns),
        _mean(returns), _median(returns), _drawdown(returns), _profit_factor(returns),
        None if not returns else sorted(returns)[max(0, int(len(returns) * .05) - 1)],
        _counts(item.features.symbol for item, _ in predicted),
        _counts(item.features.session_date.isoformat() for item, _ in predicted),
        _counts(item.features.session for item, _ in predicted),
        _counts(str(item.features.as_mapping().get("setup_type")) for item, _ in predicted),
    )


def compare_champion(challenger, examples: tuple[LearningExample, ...], threshold: float = .5) -> ChampionComparison:
    examples = latest_by_experience(examples)
    groups = {name: [] for name in ("CHAMPION_ONLY", "CHALLENGER_ONLY", "BOTH", "NEITHER")}
    same = 0
    for item in examples:
        prediction = challenger.predict(item.features)
        selected = prediction.probability is not None and prediction.probability >= threshold
        champion = item.champion_selected
        if champion == selected:
            same += 1
        name = "BOTH" if champion and selected else "CHAMPION_ONLY" if champion else "CHALLENGER_ONLY" if selected else "NEITHER"
        groups[name].append(item)
    summaries = []
    for name, rows in groups.items():
        labeled = [item for item in rows if item.labels is not None]
        rate = None if not labeled else sum(item.labels.one_r_before_stop for item in labeled) / len(labeled)
        summaries.append((name, len(rows), rate))
    return ChampionComparison(same, len(groups["CHAMPION_ONLY"]), len(groups["CHALLENGER_ONLY"]),
                              len(groups["BOTH"]), len(groups["NEITHER"]), tuple(summaries))


def select_on_validation(challengers, validation: tuple[LearningExample, ...]):
    """Select using VALIDATION only; return None when no model is evaluable."""

    if any(item.partition.value != "VALIDATION" for item in validation):
        raise ValueError("challenger selection may use VALIDATION only")
    candidates = []
    for challenger in challengers:
        metrics = evaluate_challenger(challenger, validation)
        if metrics.sample_size and metrics.brier_score is not None and metrics.log_loss is not None:
            candidates.append((metrics.brier_score, metrics.log_loss, challenger.challenger_id, challenger))
    return None if not candidates else min(candidates)[-1]


def _auc(y, p):
    if not y or len(set(y)) < 2:
        return None
    pairs = [(score, label) for score, label in zip(p, y)]
    wins = ties = 0
    positives = [item for item in pairs if item[1] == 1]
    negatives = [item for item in pairs if item[1] == 0]
    for pos in positives:
        for neg in negatives:
            wins += pos[0] > neg[0]
            ties += pos[0] == neg[0]
    return (wins + .5 * ties) / (len(positives) * len(negatives))


def _pr_auc(y, p):
    if not y or not any(y):
        return None
    ordered = sorted(zip(p, y), reverse=True)
    tp = fp = 0
    previous_recall = area = 0.0
    positives = sum(y)
    for _, label in ordered:
        tp += label
        fp += 1 - label
        recall = tp / positives
        precision = tp / (tp + fp)
        area += (recall - previous_recall) * precision
        previous_recall = recall
    return area


def _calibration(y, p):
    buckets = []
    for index in range(10):
        low, high = index / 10, (index + 1) / 10
        indices = [i for i, value in enumerate(p) if low <= value < high or index == 9 and value == 1]
        buckets.append(CalibrationBucket(low, high, len(indices),
                                         _mean([p[i] for i in indices]), _mean([y[i] for i in indices])))
    return tuple(buckets)


def _ece(y, p):
    if not y:
        return None
    return sum(bucket.count / len(y) * abs(bucket.mean_probability - bucket.observed_rate)
               for bucket in _calibration(y, p) if bucket.count)


def _drawdown(values):
    if not values:
        return None
    equity = peak = 0.0
    maximum = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        maximum = max(maximum, peak - equity)
    return maximum


def _profit_factor(values):
    gains = sum(value for value in values if value > 0)
    losses = -sum(value for value in values if value < 0)
    return None if losses == 0 else gains / losses


def _mean(values):
    return None if not values else sum(values) / len(values)


def _median(values):
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    return ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2


def _counts(values):
    result = {}
    for value in values:
        result[str(value)] = result.get(str(value), 0) + 1
    return tuple(sorted(result.items(), key=lambda item: (-item[1], item[0])))
