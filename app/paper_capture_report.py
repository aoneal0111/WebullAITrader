"""Read-only PAPER execution, capture-health, and lifecycle report."""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable, Mapping

ZERO = Decimal("0")
NONTERMINAL = frozenset({"NEW", "ACCEPTED", "PARTIALLY_FILLED"})
HYPOTHETICAL_COSTS = (Decimal("0.005"), Decimal("0.010"))


def _read_only(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise FileNotFoundError(path)
    connection = sqlite3.connect(
        f"file:{path.resolve().as_posix()}?mode=ro", uri=True,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def _tables(connection: sqlite3.Connection) -> set[str]:
    return {str(row[0]) for row in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}


def _decimal(value: object) -> Decimal:
    return Decimal(str(value or "0"))


def _timestamp(value: object) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _fill_identity(order_id: str, fill: Mapping[str, Any]) -> tuple[object, ...]:
    return (
        order_id, str(fill.get("quantity")), str(fill.get("price")),
        str(fill.get("timestamp")), str(fill.get("commission") or "0"),
        str(fill.get("slippage") or "0"),
    )


def _execution_lifecycles(path: Path) -> tuple[
    list[dict[str, Any]], list[dict[str, Any]], set[tuple[str, str]],
    list[dict[str, Any]], dict[str, Any],
]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    unmatched: list[dict[str, Any]] = []
    seen_fills: dict[str, tuple[object, ...]] = {}
    duplicate_count = 0
    conflicting_ids: set[str] = set()
    campaign_rows: list[dict[str, Any]] = []
    campaigns: dict[str, dict[str, Any]] = {}
    all_orders: list[dict[str, Any]] = []
    with _read_only(path) as connection:
        for row in connection.execute(
            "SELECT campaign_id,status,started_at,ended_at,reason "
            "FROM paper_campaigns ORDER BY started_at,campaign_id"
        ):
            campaign = dict(row)
            campaign_rows.append(campaign)
            campaigns[str(row["campaign_id"])] = campaign
        for row in connection.execute("SELECT order_id,payload FROM orders"):
            payload = json.loads(row["payload"])
            payload["_order_id"] = str(row["order_id"])
            payload["_unique_fills"] = []
            request = payload.get("request") or {}
            campaign = str(payload.get("paper_campaign_id") or "").strip()
            lifecycle = str(request.get("strategy_lifecycle_id") or "").strip()
            for fill in payload.get("fills") or ():
                fill_id = str(fill.get("fill_id") or "").strip()
                if not fill_id:
                    unmatched.append(_unmatched(payload, fill, "MISSING_FILL_ID"))
                    continue
                identity = _fill_identity(str(row["order_id"]), fill)
                if fill_id in seen_fills:
                    if seen_fills[fill_id] == identity:
                        duplicate_count += 1
                    else:
                        conflicting_ids.add(fill_id)
                        unmatched.append(_unmatched(
                            payload, fill, "CONFLICTING_DUPLICATE_FILL_ID"
                        ))
                    continue
                seen_fills[fill_id] = identity
                payload["_unique_fills"].append(fill)
                if not campaign or not lifecycle:
                    unmatched.append(_unmatched(
                        payload, fill,
                        "MISSING_CAMPAIGN_ID" if not campaign else "MISSING_LIFECYCLE_ID",
                    ))
            all_orders.append(payload)
            if campaign and lifecycle:
                grouped[(campaign, lifecycle)].append(payload)

    if conflicting_ids:
        for order in all_orders:
            removed = [
                str(fill.get("fill_id") or "") for fill in order["_unique_fills"]
                if str(fill.get("fill_id") or "") in conflicting_ids
            ]
            order["_unique_fills"] = [
                fill for fill in order["_unique_fills"]
                if str(fill.get("fill_id") or "") not in conflicting_ids
            ]
            order["_conflicting_fill_ids"] = removed

    lifecycles: list[dict[str, Any]] = []
    symbol_inventory: dict[tuple[str, str], Decimal] = defaultdict(lambda: ZERO)
    for (campaign_id, lifecycle_id), orders in sorted(grouped.items()):
        fills: list[dict[str, Any]] = []
        symbols: set[str] = set()
        statuses: dict[str, int] = defaultdict(int)
        buy_orders = sell_orders = 0
        for order in orders:
            request = order.get("request") or {}
            symbol = str(request.get("symbol") or "").strip().upper()
            symbols.add(symbol)
            side = str(request.get("side") or "").upper()
            buy_orders += side == "BUY"
            sell_orders += side == "SELL"
            statuses[str(order.get("status") or "UNKNOWN").upper()] += 1
            fills.extend({
                **fill, "order_id": order["_order_id"], "side": side,
                "symbol": symbol,
            } for fill in order["_unique_fills"])
        fills.sort(key=lambda fill: (
            str(fill.get("timestamp") or ""), str(fill.get("fill_id") or ""),
        ))
        lifecycle = _account_lifecycle(
            campaign_id, lifecycle_id, orders, fills, campaigns.get(campaign_id),
            symbols, statuses, buy_orders, sell_orders, symbol_inventory,
        )
        lifecycles.append(lifecycle)

    working_sells = []
    for order in all_orders:
        request = order.get("request") or {}
        status = str(order.get("status") or "").upper()
        if status not in NONTERMINAL or str(request.get("side") or "").upper() != "SELL":
            continue
        metadata = request.get("metadata") or {}
        working_sells.append({
            "order_id": order["_order_id"],
            "paper_campaign_id": order.get("paper_campaign_id"),
            "lifecycle_id": request.get("strategy_lifecycle_id"),
            "symbol": request.get("symbol"), "order_type": request.get("order_type"),
            "status": order.get("status"),
            "remaining_order_quantity": str(
                _decimal(request.get("quantity")) - _decimal(order.get("filled_quantity"))
            ),
            "stop_price": request.get("stop_price"),
            "limit_price": request.get("limit_price"),
            "reservation_mode": metadata.get("reservation_mode"),
            "oco_group_id": metadata.get("oco_group_id"),
            "execution_reason": request.get("execution_reason"),
        })
    audit = {
        "unique_fill_ids": len(seen_fills),
        "exact_duplicate_fill_records_suppressed": duplicate_count,
        "conflicting_duplicate_fill_ids": sorted(conflicting_ids),
        "active_campaign_symbol_inventory": [
            {"paper_campaign_id": campaign, "symbol": symbol, "quantity": str(quantity)}
            for (campaign, symbol), quantity in sorted(symbol_inventory.items())
            if campaigns.get(campaign, {}).get("status") == "ACTIVE" and quantity != ZERO
        ],
        "working_sell_orders": working_sells,
    }
    return campaign_rows, lifecycles, set(grouped), unmatched, audit


def _unmatched(
    order: Mapping[str, Any], fill: Mapping[str, Any], reason: str,
) -> dict[str, Any]:
    request = order.get("request") or {}
    return {
        "order_id": order.get("_order_id"), "fill_id": fill.get("fill_id"),
        "symbol": request.get("symbol"),
        "paper_campaign_id": order.get("paper_campaign_id"),
        "lifecycle_id": request.get("strategy_lifecycle_id"), "reason": reason,
    }


def _account_lifecycle(
    campaign_id: str, lifecycle_id: str, orders: list[dict[str, Any]],
    fills: list[dict[str, Any]], campaign: Mapping[str, Any] | None,
    symbols: set[str], statuses: Mapping[str, int], buy_orders: int,
    sell_orders: int, symbol_inventory: dict[tuple[str, str], Decimal],
) -> dict[str, Any]:
    inventory = buy_qty = sell_qty = buy_notional = sell_notional = ZERO
    commissions = slippage = ZERO
    violations: list[str] = []
    started = _timestamp(None if campaign is None else campaign.get("started_at"))
    ended = _timestamp(None if campaign is None else campaign.get("ended_at"))
    if campaign is None:
        violations.append("UNKNOWN_CAMPAIGN")
    if any(order.get("_conflicting_fill_ids") for order in orders):
        violations.append("CONFLICTING_DUPLICATE_FILL_ID_EXCLUDED")
    for fill in fills:
        quantity, price = _decimal(fill.get("quantity")), _decimal(fill.get("price"))
        observed = _timestamp(fill.get("timestamp"))
        if observed is None:
            violations.append("INVALID_FILL_TIMESTAMP")
        elif (started and observed < started) or (ended and observed > ended):
            violations.append("FILL_OUTSIDE_CAMPAIGN_BOUNDARY")
        commissions += _decimal(fill.get("commission"))
        slippage += _decimal(fill.get("slippage")) * quantity
        if fill["side"] == "BUY":
            inventory += quantity
            buy_qty += quantity
            buy_notional += quantity * price
            symbol_inventory[(campaign_id, fill["symbol"])] += quantity
        elif fill["side"] == "SELL":
            inventory -= quantity
            sell_qty += quantity
            sell_notional += quantity * price
            symbol_inventory[(campaign_id, fill["symbol"])] -= quantity
            if inventory < ZERO:
                violations.append("SELL_EXCEEDED_PRIOR_LIFECYCLE_INVENTORY")
        else:
            violations.append("UNKNOWN_ORDER_SIDE")
    if len(symbols - {""}) != 1:
        violations.append("LIFECYCLE_SYMBOL_MISMATCH")
    violations = sorted(set(violations))
    completed = buy_qty > ZERO and inventory == ZERO and not violations
    state = "COMPLETED" if completed else "OPEN" if inventory > ZERO else "INVALID" if violations else "NO_POSITION"
    gross = sell_notional - buy_notional if completed else None
    net = None if gross is None else gross - commissions
    return {
        "paper_campaign_id": campaign_id, "lifecycle_id": lifecycle_id,
        "symbols": sorted(symbol for symbol in symbols if symbol), "state": state,
        "buy_quantity": str(buy_qty), "sell_quantity": str(sell_qty),
        "remaining_quantity": str(inventory), "fill_count": len(fills),
        "entry_fill_count": sum(fill["side"] == "BUY" for fill in fills),
        "exit_fill_count": sum(fill["side"] == "SELL" for fill in fills),
        "entry_order_count": buy_orders, "exit_order_count": sell_orders,
        "scale_in": buy_orders > 1,
        "partial_fill_orders": int(statuses.get("PARTIALLY_FILLED", 0)),
        "orders_with_multiple_fill_executions": sum(
            len(order["_unique_fills"]) > 1 for order in orders
        ),
        "order_status_counts": dict(sorted(statuses.items())),
        "first_fill_at": None if not fills else fills[0].get("timestamp"),
        "last_fill_at": None if not fills else fills[-1].get("timestamp"),
        "gross_fill_pnl": None if gross is None else str(gross),
        "recorded_commissions": str(commissions),
        "recorded_net_pnl": None if net is None else str(net),
        "recorded_measured_slippage_cost": str(slippage),
        "unavailable_costs": ["REGULATORY_FEES", "BORROW_COSTS", "MARKET_IMPACT"],
        "integrity_violations": violations,
        "zero_remaining_inventory_verified": completed,
        "mae": None, "mfe": None,
        "metric_availability": {
            "gross_fill_pnl": "AVAILABLE_FROM_COMPLETE_AUTHORITATIVE_FILLS" if completed else "UNAVAILABLE_OPEN_OR_INVALID_LIFECYCLE",
            "recorded_commissions": "AVAILABLE_FROM_AUTHORITATIVE_FILLS",
            "recorded_measured_slippage_cost": "RECORDED_IN_FILL_PRICES_AND_REPORTED_SEPARATELY_NOT_SUBTRACTED_TWICE",
            "mae": "UNAVAILABLE_NO_COMPLETE_AUTHORITATIVE_PRICE_PATH",
            "mfe": "UNAVAILABLE_NO_COMPLETE_AUTHORITATIVE_PRICE_PATH",
        },
    }


def _forward_correlations(
    path: Path, execution_keys: set[tuple[str, str]],
) -> tuple[dict[str, Any], set[tuple[str, str]], dict[tuple[str, str], dict[str, Any]]]:
    exact: set[tuple[str, str]] = set()
    attribution: dict[tuple[str, str], dict[str, Any]] = {}
    unmatched = missing_campaign = missing_lifecycle = records = 0
    with _read_only(path) as connection:
        for row in connection.execute(
            "SELECT payload_json FROM capture_records WHERE record_type='PAPER_FILL'"
        ):
            records += 1
            payload = json.loads(row[0])
            campaign = str(payload.get("paper_campaign_id") or "").strip()
            lifecycle = str(payload.get("lifecycle_id") or "").strip()
            missing_campaign += not bool(campaign)
            missing_lifecycle += not bool(lifecycle)
            if campaign and lifecycle:
                key = campaign, lifecycle
                if key in execution_keys:
                    exact.add(key)
                    if payload.get("action") == "ENTRY":
                        value = {"session": payload.get("session"), "setup": payload.get("setup")}
                        prior = attribution.get(key)
                        attribution[key] = value if prior is None or prior == value else {
                            "session": None, "setup": None, "conflict": True,
                        }
                else:
                    unmatched += 1
    return ({
        "paper_fill_records": records,
        "exact_execution_lifecycle_matches": len(exact),
        "unmatched_records_with_authoritative_keys": unmatched,
        "missing_campaign_id": missing_campaign,
        "missing_lifecycle_id": missing_lifecycle,
        "matching_rule": "EXACT_CAMPAIGN_AND_LIFECYCLE_ID_ONLY",
    }, exact, attribution)


def _execution_path_metrics(path: Path) -> dict[tuple[str, str], dict[str, Any]]:
    """Compute only prospectively observed excursions; reject incomplete paths."""
    grouped: dict[tuple[str, str], list[tuple[str, dict[str, Any]]]] = defaultdict(list)
    with _read_only(path) as connection:
        columns = {
            str(row[1]) for row in connection.execute(
                "PRAGMA table_info(capture_records)"
            )
        }
        if not {"timestamp", "record_id"}.issubset(columns):
            return {}
        for row in connection.execute(
            "SELECT timestamp,payload_json FROM capture_records "
            "WHERE record_type='EXECUTION_PRICE_PATH' ORDER BY timestamp,record_id"
        ):
            payload = json.loads(row["payload_json"])
            campaign = str(payload.get("paper_campaign_id") or "").strip()
            lifecycle = str(payload.get("lifecycle_id") or "").strip()
            if campaign and lifecycle:
                grouped[(campaign, lifecycle)].append((str(row["timestamp"]), payload))
    results: dict[tuple[str, str], dict[str, Any]] = {}
    for key, records in grouped.items():
        quantity = cost = ZERO
        mae_per_share = mfe_per_share = None
        mae_position = mfe_position = None
        quotes = stale = missing = 0
        recovered = limited = unmatched = False
        for _, payload in records:
            action = payload.get("action")
            if action == "RECOVERY":
                recovered = True
                quantity = _decimal(payload.get("remaining_quantity"))
                cost = ZERO
            elif action == "UNMATCHED_FILL":
                unmatched = True
            elif action == "CAPTURE_LIMIT_REACHED":
                limited = True
            elif action == "FILL":
                fill_qty, price = _decimal(payload.get("quantity")), _decimal(payload.get("price"))
                if payload.get("side") == "BUY":
                    quantity += fill_qty
                    cost += fill_qty * price
                elif payload.get("side") == "SELL" and quantity > ZERO:
                    average = cost / quantity
                    removed = min(fill_qty, quantity)
                    cost -= average * removed
                    quantity -= fill_qty
            elif action == "QUOTE" and quantity > ZERO:
                quotes += 1
                stale += int(bool(payload.get("stale_quote")))
                missing += int(bool(payload.get("missing_interval")))
                price_value = payload.get("midpoint") or payload.get("last")
                if price_value is None or cost <= ZERO:
                    continue
                excursion = _decimal(price_value) - cost / quantity
                position_excursion = excursion * quantity
                mae_per_share = min(ZERO, excursion) if mae_per_share is None else min(mae_per_share, excursion)
                mfe_per_share = max(ZERO, excursion) if mfe_per_share is None else max(mfe_per_share, excursion)
                mae_position = min(ZERO, position_excursion) if mae_position is None else min(mae_position, position_excursion)
                mfe_position = max(ZERO, position_excursion) if mfe_position is None else max(mfe_position, position_excursion)
        complete = (
            quantity == ZERO and quotes > 0 and not recovered and not limited
            and not unmatched and stale == 0 and missing == 0
        )
        results[key] = {
            "coverage": "COMPLETE" if complete else "INCOMPLETE",
            "quote_samples": quotes, "stale_quote_samples": stale,
            "missing_intervals": missing,
            "pre_restart_interval_unavailable": recovered,
            "capture_limit_reached": limited, "unmatched_fill": unmatched,
            "observed_mae_per_share": None if mae_per_share is None else str(mae_per_share),
            "observed_mfe_per_share": None if mfe_per_share is None else str(mfe_per_share),
            "observed_mae_position_pnl": None if mae_position is None else str(mae_position),
            "observed_mfe_position_pnl": None if mfe_position is None else str(mfe_position),
            "reported_as_complete_mae_mfe": complete,
        }
    return results


def _performance(trades: list[dict[str, Any]]) -> dict[str, Any]:
    ordered = sorted(trades, key=lambda item: (
        item.get("last_fill_at") or "", item["lifecycle_id"],
    ))
    pnls = [_decimal(item["recorded_net_pnl"]) for item in ordered]
    wins = [pnl for pnl in pnls if pnl > ZERO]
    losses = [pnl for pnl in pnls if pnl < ZERO]
    cumulative = peak = drawdown = ZERO
    for pnl in pnls:
        cumulative += pnl
        peak = max(peak, cumulative)
        drawdown = max(drawdown, peak - cumulative)
    count = len(pnls)
    return {
        "sample_size": count,
        "win_rate": None if not count else str(Decimal(len(wins)) / count),
        "average_win": None if not wins else str(sum(wins, ZERO) / len(wins)),
        "average_loss": None if not losses else str(sum(losses, ZERO) / len(losses)),
        "expectancy": None if not count else str(sum(pnls, ZERO) / count),
        "profit_factor": None if not losses else str(sum(wins, ZERO) / abs(sum(losses, ZERO))),
        "cumulative_recorded_net_pnl": str(sum(pnls, ZERO)),
        "maximum_closed_trade_cumulative_pnl_drawdown": str(drawdown),
        "drawdown_scope": "CLOSED_TRADE_CUMULATIVE_RECORDED_NET_PNL_NOT_MARK_TO_MARKET_ACCOUNT_EQUITY",
    }


def _sensitivity(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    results = []
    for rate in HYPOTHETICAL_COSTS:
        adjusted, total_cost = [], ZERO
        for trade in trades:
            cost = (_decimal(trade["buy_quantity"]) + _decimal(trade["sell_quantity"])) * rate
            total_cost += cost
            adjusted.append(_decimal(trade["recorded_net_pnl"]) - cost)
        results.append({
            "label": f"HYPOTHETICAL_ADDITIONAL_{rate}_PER_SHARE_PER_FILL_SIDE",
            "assumption": "ADDITIONAL_COST_APPLIED_TO_EACH_FILLED_SHARE; NOT_MEASURED_SLIPPAGE",
            "additional_cost": str(total_cost),
            "adjusted_cumulative_pnl": str(sum(adjusted, ZERO)),
            "adjusted_expectancy": None if not adjusted else str(sum(adjusted, ZERO) / len(adjusted)),
            "adjusted_win_rate": None if not adjusted else str(Decimal(sum(value > ZERO for value in adjusted)) / len(adjusted)),
        })
    return results


def _experiment_health(path: Path) -> dict[str, Any]:
    with _read_only(path) as connection:
        tables = _tables(connection)
        candidates = connection.execute("""SELECT COUNT(*) total,
          SUM(json_extract(labels_json,'$.outcome_status')='COMPLETE') complete,
          SUM(json_extract(execution_json,'$.state')='NOT_EXECUTED') not_executed,
          SUM(json_extract(features_json,'$.catalyst_status')='TRUE') positive_catalyst,
          SUM(CASE WHEN json_extract(features_json,'$.bid') IS NOT NULL AND json_extract(features_json,'$.ask') IS NOT NULL AND
            json_extract(features_json,'$.spread_percent') IS NOT NULL AND json_extract(features_json,'$.float_shares') IS NOT NULL AND
            json_type(features_json,'$.halted') IN ('true','false') AND json_type(features_json,'$.tradable') IN ('true','false') AND
            json_extract(features_json,'$.catalyst_status') IN ('TRUE','FALSE') AND
            (json_extract(features_json,'$.catalyst_status')='FALSE' OR (json_extract(features_json,'$.selected_source') IS NOT NULL AND json_extract(features_json,'$.published_at') IS NOT NULL))
          THEN 1 ELSE 0 END) evidence_complete FROM experiment_candidates""").fetchone()
        reasons = dict(connection.execute("""SELECT
          SUM(json_extract(features_json,'$.bid') IS NULL OR json_extract(features_json,'$.ask') IS NULL) quote_not_recorded,
          SUM(json_extract(features_json,'$.spread_percent') IS NULL) spread_not_recorded,
          SUM(json_extract(features_json,'$.float_shares') IS NULL) float_not_recorded,
          SUM(COALESCE(json_type(features_json,'$.halted'),'') NOT IN ('true','false') OR COALESCE(json_type(features_json,'$.tradable'),'') NOT IN ('true','false')) halt_state_not_recorded,
          SUM(json_extract(features_json,'$.catalyst_status') IN ('UNKNOWN','UNAVAILABLE')) catalyst_evidence_unavailable,
          SUM(json_extract(features_json,'$.catalyst_status')='TRUE' AND json_extract(features_json,'$.selected_source') IS NULL) catalyst_source_not_recorded,
          SUM(json_extract(features_json,'$.catalyst_status')='TRUE' AND json_extract(features_json,'$.published_at') IS NULL) catalyst_published_at_not_recorded
          FROM experiment_candidates""").fetchone())
        cumulative = _latest_row(connection, "research_worker_telemetry", "singleton=1") if "research_worker_telemetry" in tables else None
        latest = _latest_row(connection, "research_worker_sessions", "1=1", "started_at DESC,session_id DESC") if "research_worker_sessions" in tables else None
        gaps: dict[str, Any] = {"episodes": 0, "observations": 0, "first_gap_at": None, "last_gap_at": None, "by_reason": []}
        if "research_capture_gaps" in tables:
            columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(research_capture_gaps)")}
            episode = "COALESCE(SUM(episode_count),0)" if "episode_count" in columns else "COUNT(*)"
            gaps.update(dict(connection.execute(f"SELECT {episode} episodes,COALESCE(SUM(rejected_count),0) observations,MIN(started_at) first_gap_at,MAX(ended_at) last_gap_at FROM research_capture_gaps").fetchone()))
            reason_episodes = "SUM(episode_count)" if "episode_count" in columns else "COUNT(*)"
            gaps["by_reason"] = [dict(reason=row[0], episodes=int(row[1]), observations=int(row[2])) for row in connection.execute(f"SELECT reason,{reason_episodes},SUM(rejected_count) FROM research_capture_gaps GROUP BY reason ORDER BY reason")]
    total, evidence = int(candidates["total"] or 0), int(candidates["evidence_complete"] or 0)
    outcome = int(candidates["complete"] or 0)
    return {
        "candidates": total, "outcome_complete": outcome,
        "outcome_pending": total - outcome,
        "decision_evidence_complete": evidence,
        "decision_evidence_incomplete": total - evidence,
        "positive_catalyst_present": int(candidates["positive_catalyst"] or 0),
        "evidence_completeness_is_not_positive_catalyst_presence": True,
        "decision_evidence_incomplete_reasons": {key: int(value or 0) for key, value in reasons.items()},
        "not_executed_counterfactuals": int(candidates["not_executed"] or 0),
        "historical_cumulative_telemetry": cumulative,
        "latest_worker_session": latest, "explicit_capture_gaps": gaps,
        "legacy_rejections_without_gap_detail": 0 if cumulative is None else max(0, int(cumulative["items_rejected"]) - int(gaps["observations"])),
    }


def _latest_row(
    connection: sqlite3.Connection, table: str, where: str,
    order: str | None = None,
) -> dict[str, Any] | None:
    suffix = "" if order is None else f" ORDER BY {order}"
    row = connection.execute(
        f"SELECT * FROM {table} WHERE {where}{suffix} LIMIT 1"
    ).fetchone()
    return None if row is None else dict(row)


def build_paper_capture_report(
    *, experiment_path: Path, execution_path: Path, forward_path: Path,
) -> dict[str, Any]:
    campaigns, lifecycles, keys, identity_unmatched, audit = _execution_lifecycles(execution_path)
    forward, exact, attribution = _forward_correlations(forward_path, keys)
    path_metrics = _execution_path_metrics(forward_path)
    for lifecycle in lifecycles:
        key = lifecycle["paper_campaign_id"], lifecycle["lifecycle_id"]
        lifecycle["forward_capture_correlation"] = "EXACT" if key in exact else "UNMATCHED"
        lifecycle["attribution"] = attribution.get(key, {"session": None, "setup": None})
        path = path_metrics.get(key)
        lifecycle["execution_price_path"] = path
        if path is not None and path["reported_as_complete_mae_mfe"]:
            lifecycle["mae"] = path["observed_mae_per_share"]
            lifecycle["mfe"] = path["observed_mfe_per_share"]
            lifecycle["metric_availability"]["mae"] = "AVAILABLE_COMPLETE_PROSPECTIVE_AUTHORITATIVE_PATH_PER_SHARE"
            lifecycle["metric_availability"]["mfe"] = "AVAILABLE_COMPLETE_PROSPECTIVE_AUTHORITATIVE_PATH_PER_SHARE"
    completed = [item for item in lifecycles if item["state"] == "COMPLETED"]
    open_positions = [item for item in lifecycles if item["state"] == "OPEN"]
    active_inventory = {
        (item["paper_campaign_id"], item["symbol"]): _decimal(item["quantity"])
        for item in audit["active_campaign_symbol_inventory"]
    }
    for position in open_positions:
        key = position["paper_campaign_id"], position["lifecycle_id"]
        symbol = position["symbols"][0] if len(position["symbols"]) == 1 else None
        orders = [order for order in audit["working_sell_orders"] if (
            order["paper_campaign_id"], order["lifecycle_id"]
        ) == key]
        hard_stop = sum((
            _decimal(order["remaining_order_quantity"])
            for order in orders if order["order_type"] in {"STOP", "STOP_LIMIT"}
        ), ZERO)
        passive_target = sum((
            _decimal(order["remaining_order_quantity"])
            for order in orders if order["order_type"] == "LIMIT"
        ), ZERO)
        reserved = sum((
            _decimal(order["remaining_order_quantity"])
            for order in orders
            if order["reservation_mode"] != "CONTINGENT_OCO"
        ), ZERO)
        remaining = _decimal(position["remaining_quantity"])
        persisted = active_inventory.get((position["paper_campaign_id"], symbol or ""))
        position["persisted_position_and_protection"] = {
            "persisted_symbol_inventory": None if persisted is None else str(persisted),
            "lifecycle_inventory_matches_persisted_symbol_inventory": persisted == remaining,
            "working_hard_stop_quantity": str(hard_stop),
            "working_passive_target_quantity": str(passive_target),
            "working_sell_trigger_quantity": str(hard_stop + passive_target),
            "reserved_working_sell_quantity": str(reserved),
            "hard_stop_covers_full_remaining_position": hard_stop >= remaining,
            "reserved_working_sell_quantity_exceeds_remaining_position": reserved > remaining,
            "discrepancies": [
                reason for reason, present in (
                    ("PERSISTED_SYMBOL_INVENTORY_MISMATCH", persisted != remaining),
                    ("HARD_STOP_DOES_NOT_COVER_FULL_REMAINING_POSITION", hard_stop < remaining),
                    ("RESERVED_WORKING_SELL_QUANTITY_EXCEEDS_REMAINING_POSITION", reserved > remaining),
                ) if present
            ],
        }
    unmatched = [*identity_unmatched, *({
        "paper_campaign_id": item["paper_campaign_id"],
        "lifecycle_id": item["lifecycle_id"], "symbols": item["symbols"],
        "fill_count": item["fill_count"], "reason": "NO_EXACT_FORWARD_CAPTURE_KEY",
    } for item in lifecycles if item["fill_count"] and item["forward_capture_correlation"] == "UNMATCHED")]
    grouped: dict[str, Any] = {}
    for field in ("session", "setup"):
        values: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for trade in completed:
            value = trade["attribution"].get(field)
            if value:
                values[str(value)].append(trade)
        attributed = sum(map(len, values.values()))
        grouped[field] = {
            "attributed_sample_size": attributed,
            "unattributed_sample_size": len(completed) - attributed,
            "groups": {key: _performance(value) for key, value in sorted(values.items())},
        }
    return {
        "stores": {"experiment": str(experiment_path.resolve()), "execution": str(execution_path.resolve()), "forward": str(forward_path.resolve()), "access": "READ_ONLY"},
        "campaigns": campaigns,
        "summary": {
            "completed_trade_lifecycles": len(completed),
            "open_positions": len(open_positions),
            "unmatched_execution_fill_records_or_groups": len(unmatched),
            "completed_gross_fill_pnl": str(sum((_decimal(item["gross_fill_pnl"]) for item in completed), ZERO)),
            "completed_recorded_commissions": str(sum((_decimal(item["recorded_commissions"]) for item in completed), ZERO)),
            "completed_recorded_net_pnl": str(sum((_decimal(item["recorded_net_pnl"]) for item in completed), ZERO)),
            "completed_recorded_measured_slippage_cost": str(sum((_decimal(item["recorded_measured_slippage_cost"]) for item in completed), ZERO)),
        },
        "completed_trade_breakdown": _performance(completed),
        "completed_trade_breakdown_by_supported_attribution": grouped,
        "hypothetical_execution_cost_sensitivity": _sensitivity(completed),
        "completed_trade_lifecycles": completed, "open_positions": open_positions,
        "unmatched_execution_fills": unmatched, "execution_integrity": audit,
        "forward_capture": forward,
        "experiment_capture": _experiment_health(experiment_path),
    }


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", type=Path, required=True)
    parser.add_argument("--execution", type=Path, required=True)
    parser.add_argument("--forward", type=Path, required=True)
    args = parser.parse_args(argv)
    print(json.dumps(build_paper_capture_report(
        experiment_path=args.experiment, execution_path=args.execution,
        forward_path=args.forward,
    ), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
