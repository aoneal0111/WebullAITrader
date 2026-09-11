"""Read-only corpus validation and research summaries."""

from __future__ import annotations

from collections import Counter
from datetime import datetime
import json
from pathlib import Path
from statistics import median

from .models import ACTIVE_STRATEGIES
from .storage import KnowledgeStore


def validate_corpus(root: Path) -> tuple[str, ...]:
    errors: list[str] = []
    store = KnowledgeStore(root, create=False)
    seen = set()
    for row in store.iter_episodes():
        identity = row.get("episode_id")
        if not identity or identity in seen:
            errors.append("EXACT_DUPLICATE")
        seen.add(identity)
        members = row.get("strategy_memberships", ())
        if not members or any(item not in ACTIVE_STRATEGIES for item in members):
            errors.append("INVALID_STRATEGY_MEMBERSHIP")
        if row.get("trigger_price") is None or row.get("structural_stop") is None:
            errors.append("MISSING_GEOMETRY")
        if row.get("risk_per_share") in (None, "0", 0):
            errors.append("NON_POSITIVE_RISK")
        for bar in row.get("bar_window", ()):
            if datetime.fromisoformat(bar["timestamp"]) > datetime.fromisoformat(row["detected_timestamp"]):
                errors.append("LOOKAHEAD_VIOLATION")
                break
    return tuple(errors)


def report(root: Path) -> dict[str, object]:
    store = KnowledgeStore(root, create=False)
    per = Counter()
    symbols = {strategy: set() for strategy in ACTIVE_STRATEGIES}
    dates = {strategy: [] for strategy in ACTIVE_STRATEGIES}
    for row in store.iter_episodes():
        for strategy in row.get("strategy_memberships", ()):
            per[strategy] += 1; symbols[strategy].add(row["symbol"]); dates[strategy].append(row["trading_date"])
    rows = tuple(store.iter_episodes())
    result = {}
    for strategy in ACTIVE_STRATEGIES:
        selected = [row for row in rows if strategy in row.get("strategy_memberships", ())]
        mfe = [float(row["outcomes"]["mfe_percent"]) for row in selected if row.get("outcomes", {}).get("mfe_percent") is not None]
        mae = [float(row["outcomes"]["mae_percent"]) for row in selected if row.get("outcomes", {}).get("mae_percent") is not None]
        max_r = [float(row["outcomes"]["maximum_R"]) for row in selected if row.get("outcomes", {}).get("maximum_R") is not None]
        targets = {str(pct): sum(bool(row.get("outcomes", {}).get("percent_targets", {}).get(str(pct), {}).get("hit")) for row in selected)
                   for pct in (2, 3, 5, 8, 10, 15)}
        result[strategy] = {"accepted": len(selected), "unique_symbols": len({row["symbol"] for row in selected}),
                            "date_start": min((row["trading_date"] for row in selected), default=None),
                            "date_end": max((row["trading_date"] for row in selected), default=None),
                            "median_mfe_percent": None if not mfe else median(mfe),
                            "median_mae_percent": None if not mae else median(mae),
                            "median_maximum_R": None if not max_r else median(max_r),
                            "target_hits": targets,
                            "stop_before_target": {
                                str(pct): sum(row.get("outcomes", {}).get("percent_targets", {}).get(str(pct), {}).get("first_plan_event") == "STOP_FIRST" for row in selected)
                                for pct in (2, 3, 5, 8, 10, 15)
                            }}
    return {"unique_episodes": len(rows), "strategy_memberships": sum(per.values()), "per_strategy": result}


def expectancy(results: list[float]) -> float | None:
    """Return the arithmetic mean of explicit research returns only."""
    return None if not results else sum(results) / len(results)


def profit_factor(winners: list[float], losers: list[float]) -> float | None:
    gross_loss = abs(sum(item for item in losers if item < 0))
    return None if gross_loss == 0 else sum(item for item in winners if item > 0) / gross_loss


def maximum_drawdown(returns: list[float]) -> float:
    equity = peak = drawdown = 0.0
    for value in returns:
        equity += value; peak = max(peak, equity); drawdown = min(drawdown, equity - peak)
    return drawdown
