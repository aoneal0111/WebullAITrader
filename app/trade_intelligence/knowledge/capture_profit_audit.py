"""Bounded PAPER fill reconciliation and sampled small-profit signal audit.

Signals are overlays on recorded positions, not hypothetical executions.
Missing quote intervals cannot establish the first true threshold crossing.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime
from decimal import Decimal
import json
from pathlib import Path

D = Decimal
VERSION = "CAPTURE_PROFIT_SIGNAL_AUDIT_V1"


def number(value):
    result = D(str(value))
    if not result.is_finite():
        raise ValueError("NONFINITE_VALUE")
    return result


def instant(value):
    result = datetime.fromisoformat(value)
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("NAIVE_TIMESTAMP")
    return result


def order_key(order):
    request = order["request"]
    return (order.get("paper_campaign_id", "legacy-paper-campaign"),
            request.get("strategy_lifecycle_id") or
            request.get("metadata", {}).get("lifecycle_id"))


def audit_capture(data, *, activation_r=D("0.40"), peak_retention=D("0.50"),
                  max_quote_age_seconds=D("5"), extra_cost_per_side=D("0")):
    activation_r, peak_retention = number(activation_r), number(peak_retention)
    max_quote_age_seconds, extra_cost_per_side = number(max_quote_age_seconds), number(extra_cost_per_side)
    if not activation_r > 0 or not 0 < peak_retention < 1 or max_quote_age_seconds < 0 or extra_cost_per_side < 0:
        raise ValueError("INVALID_EXPERIMENT_SETTINGS")
    records, orders = data["capture_records"], data["orders"]
    if len(records) > 5000 or len(orders) > 500:
        raise ValueError("CAPTURE_INPUT_LIMIT")
    grouped, ledger = defaultdict(list), defaultdict(list)
    for r in records:
        p = r["payload"]
        key = (p.get("paper_campaign_id", "legacy-paper-campaign"), p.get("lifecycle_id"))
        if str(key[1]).startswith("WARRIOR_MOMENTUM_V1|"):
            grouped[key].append(r)
    if len(grouped) > 20:
        raise ValueError("LIFECYCLE_LIMIT")
    order_ids = [o["order_id"] for o in orders]
    if len(order_ids) != len(set(order_ids)):
        raise ValueError("DUPLICATE_ORDER")
    for o in orders:
        ledger[order_key(o)].append(o)
    reports = []
    for key, path in sorted(grouped.items()):
        life_orders = ledger[key]
        fills, captured, fill_ids, problems = [], [], set(), []
        symbols = {r["symbol"] for r in path} | {o["request"]["symbol"] for o in life_orders}
        if len(symbols) != 1:
            problems.append("MIXED_SYMBOL")
        stops = set()
        for o in life_orders:
            request = o["request"]
            side = request["side"]
            if side not in ("BUY", "SELL"):
                problems.append("INVALID_SIDE")
                continue
            quantity = D(0)
            for f in o.get("fills", []):
                q, price, fee = number(f["quantity"]), number(f["price"]), number(f.get("commission", "0"))
                if q <= 0 or q != q.to_integral_value() or price <= 0 or fee < 0:
                    problems.append("INVALID_FILL")
                if f["fill_id"] in fill_ids:
                    problems.append("DUPLICATE_FILL")
                fill_ids.add(f["fill_id"])
                quantity += q
                fills.append((instant(f["timestamp"]), o["order_id"], side, q, price, fee))
            if quantity != number(o["filled_quantity"]):
                problems.append("ORDER_FILL_QUANTITY_MISMATCH")
            if side == "BUY" and quantity > 0:
                stop = request.get("structural_stop_price")
                if stop is not None:
                    stops.add(number(stop))
                else:
                    problems.append("ENTRY_STRUCTURAL_STOP_MISSING")
        for r in path:
            p = r["payload"]
            if p.get("action") == "FILL":
                captured.append((instant(p["fill_timestamp"]), p["order_id"], p["side"],
                                 number(p["quantity"]), number(p["price"])))
        if Counter(f[:5] for f in fills) != Counter(captured):
            problems.append("CAPTURE_LEDGER_FILL_MISMATCH")
        fills.sort()
        buys = [f for f in fills if f[2] == "BUY"]
        bought = sum((f[3] for f in buys), D(0))
        sold = sum((f[3] for f in fills if f[2] == "SELL"), D(0))
        if not bought or bought != sold:
            problems.append("NOT_CLOSED_FILLED_LIFECYCLE")
        if len(stops) != 1:
            problems.append("STRUCTURAL_STOP_UNAVAILABLE_OR_CHANGED")
        if buys and any(f[2] == "SELL" and f[0] <= buys[-1][0] for f in fills):
            problems.append("OVERLAPPING_ENTRY_EXIT_PHASES")
        result = {"campaign": key[0], "lifecycle": key[1], "symbol": next(iter(symbols), None),
                  "bought": bought, "sold": sold, "reconciliation_problems": sorted(set(problems))}
        if problems:
            result["status"] = "NOT_RECONCILED"
            reports.append(result)
            continue
        average_entry = sum((f[3] * f[4] for f in buys), D(0)) / bought
        stop = next(iter(stops))
        risk = bought * (average_entry - stop)
        if stop <= 0 or risk <= 0:
            result.update(status="INVALID_RISK_GEOMETRY")
            reports.append(result)
            continue
        cash = position = transacted = D(0)
        for f in fills:
            position += f[3] * (1 if f[2] == "BUY" else -1)
            if position < 0:
                raise ValueError("SELL_BEFORE_SUFFICIENT_POSITION")
            cash += f[3] * f[4] * (1 if f[2] == "SELL" else -1) - f[5]
            transacted += f[3]
        closed_pnl = cash - extra_cost_per_side * transacted
        excluded, timing = Counter(), []
        points = []
        fill_times = {f[0] for f in fills}
        for r in sorted(path, key=lambda r: instant(r["timestamp"])):
            p = r["payload"]
            if p.get("action") != "QUOTE":
                continue
            at, source_at = instant(r["timestamp"]), instant(p["quote_timestamp"])
            age = D(str((at - source_at).total_seconds()))
            if age < 0:
                timing.append(-age * 1000)
            reason = None
            if at < fills[0][0] or at > fills[-1][0]:
                reason = "OUTSIDE_FILL_LIFETIME"
            elif at in fill_times:
                reason = "SAME_TIME_FILL_ORDER_UNKNOWN"
            elif age < 0 or p.get("future_quote"):
                reason = "FUTURE_QUOTE"
            elif age > max_quote_age_seconds or p.get("stale_quote"):
                reason = "STALE_QUOTE"
            elif p.get("bid") is None or p.get("ask") is None:
                reason = "MISSING_SIDES"
            else:
                bid, ask = number(p["bid"]), number(p["ask"])
                if bid <= 0 or ask < bid:
                    reason = "INVALID_QUOTE_GEOMETRY"
            if reason:
                excluded[reason] += 1
                continue
            current = [f for f in fills if f[0] <= at]
            q = sum((f[3] * (1 if f[2] == "BUY" else -1) for f in current), D(0))
            cf = sum((f[3] * f[4] * (1 if f[2] == "SELL" else -1) - f[5] for f in current), D(0))
            traded = sum((f[3] for f in current), D(0))
            mark = cf + q * bid - extra_cost_per_side * (traded + q)
            points.append({"evaluated_at": at, "quote_timestamp": source_at, "bid": bid,
                           "remaining": q, "total_profit_mark": mark,
                           "entry_complete": at >= buys[-1][0]})
        peak = max(points, key=lambda p: p["total_profit_mark"], default=None)
        armed = signal = None
        observed_peak = None
        for point in points:
            if not point["entry_complete"] or point["remaining"] <= 0:
                continue
            mark = point["total_profit_mark"]
            observed_peak = mark if observed_peak is None else max(observed_peak, mark)
            if armed is None and mark >= activation_r * risk:
                armed = point
            if armed is not None and mark <= observed_peak * peak_retention:
                signal = {**point, "observed_peak_profit": observed_peak,
                          "retention_floor": observed_peak * peak_retention}
                break
        gaps = sum(bool(r["payload"].get("missing_interval")) for r in path)
        result.update(status="RECONCILED", average_entry=average_entry, structural_stop=stop,
            initial_price_risk=risk, recorded_commissions=sum((f[5] for f in fills), D(0)),
            actual_closed_pnl=cash, closed_pnl_with_declared_extra_cost=closed_pnl,
            eligible_quotes=len(points), excluded_quotes=excluded,
            sampled_peak_total_profit=peak, candidate_armed_at=armed, first_observed_signal=signal,
            candidate_fill_pnl=None, missing_interval_flags=gaps,
            max_recorded_interval_seconds=max((number(r["payload"].get("interval_seconds") or 0)
                for r in path if r["payload"].get("action") == "QUOTE"), default=D(0)),
            future_timestamp_ms={"count": len(timing), "min": min(timing) if timing else None,
                                 "max": max(timing) if timing else None},
            evidence="OBSERVED_SIGNAL_OVERLAY_ON_ACTUAL_FILLS_NOT_COUNTERFACTUAL_EXECUTION")
        reports.append(result)
    return {"version": VERSION, "configuration": {"activation_r": activation_r,
            "peak_retention": peak_retention, "max_quote_age_seconds": max_quote_age_seconds,
            "extra_cost_per_share_per_side": extra_cost_per_side}, "lifecycles": reports,
            "scope": "WARRIOR_ONLY_SAMPLED_QUOTES_NO_FILL_SIMULATION_OR_POLICY_PROMOTION"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--activation-r", type=D, default=D("0.40"))
    parser.add_argument("--peak-retention", type=D, default=D("0.50"))
    parser.add_argument("--max-quote-age-seconds", type=D, default=D("5"))
    parser.add_argument("--extra-cost-per-share-per-side", type=D, default=D("0"))
    args = parser.parse_args(argv)
    with args.input.open("rb") as source:
        raw = source.read(8_000_001)
    if len(raw) > 8_000_000:
        parser.error("Input must be at most 8 MB")
    report = audit_capture(json.loads(raw.decode("utf-8-sig")),
        activation_r=args.activation_r, peak_retention=args.peak_retention,
        max_quote_age_seconds=args.max_quote_age_seconds,
        extra_cost_per_side=args.extra_cost_per_share_per_side)
    print(json.dumps(report, default=str, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
