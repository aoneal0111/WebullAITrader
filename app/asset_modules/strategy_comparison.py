"""Offline paired policy summaries; no broker, strategy execution or promotion."""
import argparse
from collections import Counter
from datetime import date
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path

from app.asset_modules.engine_catalog import EngineId

EVIDENCE_KINDS = {"ACTUAL_PAPER_FILLS", "QUOTE_PROXY", "MINUTE_BAR_PROXY"}
STATUSES = {"CLOSED", "UNRESOLVED", "NO_ENTRY"}


def _label(value, name):
    if not isinstance(value, str) or not value.strip() or len(value) > 256:
        raise ValueError(f"Invalid {name}")
    return value


def summarize(data):
    """Pair only CLOSED episodes shared by all declared policies per engine.

    Caller-supplied net PnL must already include its declared cost model. This
    validates identities and comparability, not source fills or profitability.
    """
    if not isinstance(data, dict) or data.get("version") != "ENGINE_COMPARISON_V1":
        raise ValueError("Expected ENGINE_COMPARISON_V1 input")
    experiment = _label(data.get("experiment_id"), "experiment_id")
    dataset = _label(data.get("dataset_id"), "dataset_id")
    costs = _label(data.get("cost_model_id"), "cost_model_id")
    evidence = data.get("evidence_kind")
    if evidence not in EVIDENCE_KINDS:
        raise ValueError("Unknown evidence_kind")
    specs, rows = data.get("engines"), data.get("results")
    if not isinstance(specs, dict) or not 1 <= len(specs) <= len(EngineId):
        raise ValueError("Declare one to five engines")
    if not isinstance(rows, list) or len(rows) > 100_000:
        raise ValueError("Provide at most 100000 result rows")
    declared = {}
    for engine, spec in specs.items():
        EngineId(engine)
        if not isinstance(spec, dict):
            raise ValueError("Invalid engine specification")
        policies = spec.get("policies")
        if not isinstance(policies, list) or not 2 <= len(policies) <= 20:
            raise ValueError("Declare two to twenty frozen policy versions")
        policies = [_label(p, "policy") for p in policies]
        if len(set(policies)) != len(policies):
            raise ValueError("Duplicate declared policy")
        champion = spec.get("champion")
        if champion not in policies:
            raise ValueError("Champion must be declared")
        declared[engine] = (policies, champion)
    results = {e: {} for e in declared}
    metadata = {}
    for row in rows:
        if not isinstance(row, dict) or set(row) != {
            "engine", "policy", "episode_id", "instrument", "trading_date", "status", "net_pnl"
        }:
            raise ValueError("Invalid result row")
        engine, policy = row.get("engine"), row.get("policy")
        if engine not in declared or policy not in declared[engine][0]:
            raise ValueError("Undeclared engine or policy")
        episode = _label(row.get("episode_id"), "episode_id")
        instrument = _label(row.get("instrument"), "instrument")
        day = row.get("trading_date")
        if not isinstance(day, str) or date.fromisoformat(day).isoformat() != day:
            raise ValueError("Use an ISO trading date")
        # Same episode cannot silently change symbol/date across policies.
        identity = (engine, episode)
        context = (instrument, day)
        if identity in metadata and metadata[identity] != context:
            raise ValueError("Episode context mismatch")
        metadata[identity] = context
        key = (episode, policy)
        if key in results[engine]:
            raise ValueError("Duplicate episode/policy result")
        status = row.get("status")
        if status not in STATUSES:
            raise ValueError("Unknown result status")
        pnl = row.get("net_pnl")
        if status == "CLOSED":
            # Decimal strings preserve exact amounts and avoid JSON floats/bools.
            if not isinstance(pnl, str) or len(pnl) > 64:
                raise ValueError("Closed net_pnl must be a decimal string")
            try:
                amount = Decimal(pnl)
            except InvalidOperation as exc:
                raise ValueError("Invalid net_pnl") from exc
            if (not amount.is_finite() or abs(amount) > Decimal("1e12")
                    or not -12 <= amount.as_tuple().exponent <= 12):
                raise ValueError("Invalid net_pnl")
        else:
            if pnl is not None:
                raise ValueError("Unresolved/no-entry net_pnl must be null")
            amount = None
        results[engine][key] = (status, amount)
    summaries = []
    for engine, (policies, champion) in declared.items():
        records = results[engine]
        episodes = sorted({episode for episode, _ in records})
        paired = [ep for ep in episodes if all(
            records.get((ep, policy), (None,))[0] == "CLOSED" for policy in policies)]
        policy_summaries = []
        for policy in policies:
            amounts = [records[(ep, policy)][1] for ep in paired]
            total = sum(amounts, Decimal(0))
            gains = sum((x for x in amounts if x > 0), Decimal(0))
            losses = -sum((x for x in amounts if x < 0), Decimal(0))
            deltas = [records[(ep, policy)][1] - records[(ep, champion)][1] for ep in paired]
            counts = Counter(records.get((ep, policy), ("MISSING",))[0] for ep in episodes)
            policy_summaries.append({
                "policy": policy, "status_counts": dict(counts),
                "paired_net_pnl": str(total) if paired else None,
                "paired_mean_net_pnl": str(total / len(paired)) if paired else None,
                "paired_positive_rate": str(Decimal(sum(x > 0 for x in amounts)) / len(paired)) if paired else None,
                "paired_profit_factor": str(gains / losses) if losses else None,
                "profit_factor_state": "DEFINED" if losses else "NO_PAIRED_LOSSES" if paired else "NO_PAIRED_DATA",
                "paired_delta_vs_champion": str(sum(deltas, Decimal(0))) if paired else None,
                "episodes_better_than_champion": sum(x > 0 for x in deltas),
            })
        summaries.append({"engine": engine, "champion": champion,
                          "observed_episodes": len(episodes), "paired_closed_episodes": len(paired),
                          "excluded_from_paired_totals": len(episodes) - len(paired),
                          "paired_symbol_dates": len({metadata[(engine, ep)] for ep in paired}),
                          "paired_trading_dates": len({metadata[(engine, ep)][1] for ep in paired}),
                          "policies": policy_summaries})
    return {"version": "ENGINE_COMPARISON_V1", "experiment_id": experiment,
            "dataset_id": dataset, "cost_model_id": costs, "evidence_kind": evidence,
            "engines": summaries, "promotion": "NONE",
            "scope": "OFFLINE_PAIRED_EPISODES_NOT_PORTFOLIO_OR_EXECUTION_VALIDATION"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    args = parser.parse_args()
    if args.input.stat().st_size > 20_000_000:
        parser.error("Input exceeds 20 MB")
    try:
        report = summarize(json.loads(args.input.read_text(encoding="utf-8-sig")))
    except (ValueError, KeyError, TypeError) as exc:
        parser.error(str(exc))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
