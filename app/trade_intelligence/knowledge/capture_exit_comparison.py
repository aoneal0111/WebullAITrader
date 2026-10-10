"""Offline full-size bid exit proxies after reconciled actual Warrior entries."""
import argparse
from collections import defaultdict
from decimal import Decimal, localcontext
from hashlib import sha256
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from app.asset_modules.strategy_comparison import summarize
from app.trade_intelligence.knowledge.capture_profit_audit import audit_capture, instant, number, order_key

D = Decimal
VERSION = "CAPTURE_FULL_SIZE_EXITS_V1"
POLICIES = [VERSION + "|STRUCTURAL_STOP_AND_TIME", VERSION + "|PEAK_RETENTION_AND_TIME"]
COVERAGE_FAILURES = {"NONINCREASING_PROVIDER_TIMESTAMP", "NONINCREASING_OBSERVATION",
                     "QUOTE_GAP", "FUTURE_QUOTE", "STALE_QUOTE", "INVALID_QUOTE"}


def digest(value):
    return sha256(json.dumps(value, sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()


def compare_captures(captures, *, activation_r=D(".4"), peak_retention=D(".5"),
                     max_age_seconds=D("5"), max_gap_seconds=D("5"),
                     hold_seconds=D("3600"), exit_fee_per_share=D("0"), extra_cost_per_side=D("0")):
    """No actual SELL fills enter proxy state; incomplete paths stay unresolved."""
    with localcontext() as ctx:
        ctx.prec = 50
        return _compare(captures, **dict(activation_r=activation_r, peak_retention=peak_retention,
            max_age_seconds=max_age_seconds, max_gap_seconds=max_gap_seconds, hold_seconds=hold_seconds,
            exit_fee_per_share=exit_fee_per_share, extra_cost_per_side=extra_cost_per_side))


def _compare(captures, **settings):
    settings = {k: number(v) for k, v in settings.items()}
    if not 1 <= len(captures) <= 20:
        raise ValueError("Provide one to twenty captures")
    if (any(settings[k] <= 0 for k in ("activation_r", "max_age_seconds", "max_gap_seconds", "hold_seconds"))
            or not 0 < settings["peak_retention"] < 1
            or any(settings[k] < 0 for k in ("exit_fee_per_share", "extra_cost_per_side"))):
        raise ValueError("Invalid settings")
    details, rows, seen = [], [], set()
    for data in captures:
        audit = audit_capture(data)
        paths, ledger = defaultdict(list), defaultdict(list)
        for record in data["capture_records"]:
            p = record["payload"]
            paths[(p.get("paper_campaign_id", "legacy-paper-campaign"), p.get("lifecycle_id"))].append(record)
        for order in data["orders"]:
            ledger[order_key(order)].append(order)
        for life in audit["lifecycles"]:
            key = life["campaign"], life["lifecycle"]
            if key in seen:
                raise ValueError("Duplicate lifecycle across captures")
            seen.add(key)
            detail = {k: life[k] for k in ("campaign", "lifecycle", "symbol", "status", "reconciliation_problems")}
            details.append(detail)
            if life["status"] != "RECONCILED":
                continue
            buys = [f for o in ledger[key] if o["request"]["side"] == "BUY" for f in o["fills"]]
            all_fills = [f for o in ledger[key] for f in o["fills"]]
            start = max(instant(f["timestamp"]) for f in buys)
            end = max(instant(f["timestamp"]) for f in all_fills)
            fill_times = {instant(f["timestamp"]) for f in all_fills}
            quantity, entry, stop = life["bought"], life["average_entry"], life["structural_stop"]
            buy_fees = sum((number(f.get("commission", 0)) for f in buys), D(0))
            cost = buy_fees + quantity * (settings["exit_fee_per_share"] + 2 * settings["extra_cost_per_side"])
            quotes = [r for r in paths[key]
                      if (r["payload"].get("action") == "QUOTE"
                          or (r["payload"].get("action") == "PROFIT_SHADOW_UNAVAILABLE"
                              and r["payload"].get("reason") in COVERAGE_FAILURES))
                      and start <= instant(r["timestamp"]) <= end]
            states = [{"policy": p, "status": "UNRESOLVED", "reason": "CAPTURE_ENDED", "net_pnl": None}
                      for p in POLICIES]
            previous, peak, armed = start, None, False
            for r in quotes:
                at, p = instant(r["timestamp"]), r["payload"]
                if p.get("action") == "PROFIT_SHADOW_UNAVAILABLE":
                    for state in states:
                        if state["reason"] == "CAPTURE_ENDED":
                            state["reason"] = p["reason"]
                    break
                source = instant(p["quote_timestamp"])
                age = D(str((at - source).total_seconds()))
                reason = None
                if at <= previous:
                    reason = "NONINCREASING_OBSERVATION"
                elif at in fill_times:
                    reason = "SAME_TIME_FILL_ORDER_UNKNOWN"
                elif p.get("missing_interval") or D(str((at - previous).total_seconds())) > settings["max_gap_seconds"]:
                    reason = "QUOTE_GAP"
                elif age < 0 or p.get("future_quote"):
                    reason = "FUTURE_QUOTE"
                elif age > settings["max_age_seconds"] or p.get("stale_quote"):
                    reason = "STALE_QUOTE"
                else:
                    try:
                        bid, ask = number(p["bid"]), number(p["ask"])
                        if bid <= 0 or ask < bid:
                            reason = "INVALID_QUOTE"
                    except (KeyError, ValueError, ArithmeticError):
                        reason = "INVALID_QUOTE"
                if reason:
                    for state in states:
                        if state["reason"] == "CAPTURE_ENDED":
                            state["reason"] = reason
                    break
                mark = quantity * (bid - entry) - cost
                peak = mark if peak is None else max(peak, mark)
                armed |= peak >= settings["activation_r"] * life["initial_price_risk"]
                for index, state in enumerate(states):
                    if state["status"] == "CLOSED":
                        continue
                    exit_reason = ("STOP" if bid <= stop else
                        "MAX_HOLD" if D(str((at - start).total_seconds())) >= settings["hold_seconds"] else
                        "PEAK_RETENTION" if index == 1 and armed and mark <= peak * settings["peak_retention"] else None)
                    if exit_reason:
                        state.update(status="CLOSED", reason=exit_reason, net_pnl=str(mark),
                                     exit_at=at.isoformat(), exit_bid=str(bid))
                previous = at
                if all(s["status"] == "CLOSED" for s in states):
                    break
            detail.update(actual_closed_pnl=str(life["actual_closed_pnl"]), actual_entry_complete=start.isoformat(),
                          quantity=str(quantity), results=states)
            for state in states:
                rows.append(dict(engine="WARRIOR_MOMENTUM_V1", policy=state["policy"], episode_id=digest(key),
                    instrument=life["symbol"], trading_date=start.astimezone(ZoneInfo("America/New_York")).date().isoformat(),
                    status=state["status"], net_pnl=state["net_pnl"]))
    comparison = dict(version="ENGINE_COMPARISON_V1", experiment_id=digest([VERSION, settings]),
        dataset_id=digest(sorted(digest(c) for c in captures)),
        cost_model_id=digest(["RECORDED_BUY_FEES_PLUS_DECLARED_PROXY_EXIT_COST", settings["exit_fee_per_share"], settings["extra_cost_per_side"]]),
        evidence_kind="QUOTE_PROXY", engines={"WARRIOR_MOMENTUM_V1": dict(champion=POLICIES[0], policies=POLICIES)}, results=rows)
    return dict(version=VERSION, settings=settings, lifecycles=details, comparison=summarize(comparison),
        scope="FULL_SIZE_BID_EXIT_PROXY_NOT_EXACT_WARRIOR_PARTIALS_OR_EXECUTABLE_FILLS", promotion="NONE")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", nargs="+", type=Path, help="Explicit capture JSON files; duplicate lifecycles rejected")
    for key, default in (("activation-r", ".4"), ("peak-retention", ".5"), ("max-age-seconds", "5"),
                         ("max-gap-seconds", "5"), ("hold-seconds", "3600"),
                         ("exit-fee-per-share", "0"), ("extra-cost-per-side", "0")):
        parser.add_argument("--" + key, type=D, default=D(default))
    args = parser.parse_args()
    if len(args.inputs) > 20 or sum(p.stat().st_size for p in args.inputs) > 20_000_000:
        parser.error("Input limit: twenty files, 20 MB combined")
    captures = [json.loads(p.read_text(encoding="utf-8-sig")) for p in args.inputs]
    settings = vars(args).copy()
    del settings["inputs"]
    print(json.dumps(compare_captures(captures, **settings), default=str, indent=2))


if __name__ == "__main__":
    main()
