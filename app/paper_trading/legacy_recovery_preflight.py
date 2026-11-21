"""Offline read-only legacy PAPER inventory preflight; never imports or repairs orders.

Campaign isolation is deliberate. This audit describes historical inventory and
working SELL records without implying they are active in the desktop runtime.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from contextlib import closing
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import sqlite3

LEGACY = "legacy-paper-campaign"
TERMINAL = frozenset({"FILLED", "CANCELLED", "EXPIRED", "REJECTED"})
MAX_ORDERS = 5000
MAX_PAYLOAD_BYTES = 131072


def _quantity(raw, *, positive=False):
    if isinstance(raw, bool):
        raise ValueError("INVALID_QUANTITY")
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("INVALID_QUANTITY") from exc
    if not value.is_finite() or value < 0 or (positive and value <= 0):
        raise ValueError("INVALID_QUANTITY")
    return value


def summarize_legacy_orders(rows, *, active_campaign):
    """Pure bounded reducer; no SQLite access, trading imports or side effects."""
    if not isinstance(active_campaign, str) or not active_campaign or active_campaign == LEGACY:
        raise ValueError("INVALID_ACTIVE_CAMPAIGN")
    if len(rows) > MAX_ORDERS:
        raise ValueError("ORDER_LIMIT")
    groups = defaultdict(lambda: {"buy": Decimal(0), "sell": Decimal(0), "stops": [],
                                  "targets": [], "other_working": 0})
    campaigns = defaultdict(int)
    active_positions = defaultdict(lambda: {"bought": Decimal(0), "sold": Decimal(0), "working_buys": Decimal(0), "working_sells": Decimal(0), "working_orders": 0})
    for raw in rows:
        if not isinstance(raw, str) or len(raw.encode("utf-8")) > MAX_PAYLOAD_BYTES:
            raise ValueError("PAYLOAD_LIMIT")
        order = json.loads(raw)
        if not isinstance(order, dict) or not isinstance(order.get("request"), dict) or not isinstance(order.get("fills"), list):
            raise ValueError("INVALID_ORDER")
        request = order["request"]
        campaign = order.get("paper_campaign_id") or LEGACY
        if not isinstance(campaign, str):
            raise ValueError("INVALID_CAMPAIGN")
        campaigns[campaign] += 1
        if campaign == active_campaign:
            # Observe the active-campaign state separately. Never mix its
            # quantities with legacy ownership or infer broker positions.
            symbol = request.get("symbol")
            if not isinstance(symbol, str) or not symbol or len(symbol) > 16:
                raise ValueError("INVALID_SYMBOL")
            side, status = request.get("side"), order.get("status")
            if side not in {"BUY", "SELL"} or status not in TERMINAL | {"NEW", "ACCEPTED", "PARTIALLY_FILLED"}:
                raise ValueError("INVALID_ORDER_STATE")
            qty = _quantity(request.get("quantity"), positive=True)
            filled = _quantity(order.get("filled_quantity"))
            if filled > qty:
                raise ValueError("OVERFILLED_ORDER")
            fills = order["fills"]
            if any(not isinstance(f, dict) for f in fills):
                raise ValueError("FILL_MISMATCH")
            total = sum((_quantity(f.get("quantity"), positive=True) for f in fills), Decimal(0))
            if total != filled or (status == "FILLED" and filled != qty):
                raise ValueError("FILL_MISMATCH")
            entry = active_positions[symbol]
            entry["bought" if side == "BUY" else "sold"] += filled
            if status not in TERMINAL:
                entry["working_orders"] += 1
                entry["working_buys" if side == "BUY" else "working_sells"] += qty - filled
            continue
        if campaign != LEGACY:
            # Unknown campaign is not silently imported into either inventory.
            continue
        symbol, lifecycle = request.get("symbol"), request.get("strategy_lifecycle_id")
        if not isinstance(symbol, str) or not symbol or len(symbol) > 16:
            raise ValueError("INVALID_SYMBOL")
        if not isinstance(lifecycle, str) or not lifecycle or len(lifecycle) > 512:
            raise ValueError("INVALID_LIFECYCLE")
        side, status = request.get("side"), order.get("status")
        if side not in {"BUY", "SELL"} or status not in TERMINAL | {"NEW", "ACCEPTED", "PARTIALLY_FILLED"}:
            raise ValueError("INVALID_ORDER_STATE")
        qty = _quantity(request.get("quantity"), positive=True)
        filled = _quantity(order.get("filled_quantity"))
        if filled > qty:
            raise ValueError("OVERFILLED_ORDER")
        # Require actual fills to corroborate the stored order total.
        total = sum((_quantity(f.get("quantity"), positive=True) for f in order["fills"]
                     if isinstance(f, dict)), Decimal(0))
        if len([f for f in order["fills"] if not isinstance(f, dict)]) or total != filled:
            raise ValueError("FILL_MISMATCH")
        if status == "FILLED" and filled != qty:
            raise ValueError("INVALID_FILLED_STATUS")
        key = (symbol, lifecycle)
        group = groups[key]
        group["buy" if side == "BUY" else "sell"] += filled
        if status not in TERMINAL:
            if side == "SELL":
                kind = request.get("order_type")
                info = {"remaining_quantity": str(qty - filled),
                        "order_id": order.get("order_id")}
                if kind == "STOP":
                    group["stops"].append(info)
                elif kind in {"LIMIT", "STOP_LIMIT"}:
                    group["targets"].append(info)
                else:
                    group["other_working"] += 1
            else:
                group["other_working"] += 1
    positions = []
    for (symbol, lifecycle), group in sorted(groups.items()):
        net = group["buy"] - group["sell"]
        if not net and not group["stops"] and not group["targets"] and not group["other_working"]:
            continue
        stop_qty = sum((_quantity(s["remaining_quantity"]) for s in group["stops"]), Decimal(0))
        positions.append({
            "symbol": symbol, "lifecycle": lifecycle, "net_filled_shares": str(net),
            "working_stop_shares": str(stop_qty),
            "working_stops": group["stops"], "working_targets": group["targets"],
            "other_working_orders": group["other_working"],
            "protection_gap": bool(net > 0 and stop_qty < net),
            "ambiguous_inventory": bool(net < 0 or len(group["stops"]) > 1 or stop_qty > net),
        })
    active_rows = []
    for symbol, entry in sorted(active_positions.items()):
        net = entry["bought"] - entry["sold"]
        if net != 0 or entry["working_orders"]:
            active_rows.append({
                "symbol": symbol,
                "net_filled_shares": str(net),
                "working_buy_shares": str(entry["working_buys"]),
                "working_sell_shares": str(entry["working_sells"]),
                "working_order_count": entry["working_orders"],
                "negative_inventory": net < 0,
            })
    return {
        "version": "LEGACY_PAPER_RECOVERY_PREFLIGHT_V2",
        "active_campaign": active_campaign,
        "orders_by_campaign": dict(sorted(campaigns.items())),
        "active_campaign_inventory": active_rows,
        "active_nonzero_positions": sum(row["net_filled_shares"] != "0" for row in active_rows),
        "active_working_orders": sum(row["working_order_count"] for row in active_rows),
        "active_negative_inventory": sum(row["negative_inventory"] for row in active_rows),
        "other_campaign_orders": sum(count for campaign, count in campaigns.items() if campaign not in {LEGACY, active_campaign}),
        "legacy_positions": positions,
        "legacy_nonzero_positions": sum(p["net_filled_shares"] != "0" for p in positions),
        "legacy_stop_gaps": sum(p["protection_gap"] for p in positions),
        "legacy_ambiguities": sum(p["ambiguous_inventory"] for p in positions),
        "runtime_action": "NONE", "recovery_authorized": False,
        "interpretation": "SAVED_ORDER_EVIDENCE_SEPARATED_BY_CAMPAIGN_NOT_BROKER_INVENTORY",
    }


def preflight(path: Path):
    path = Path(path).resolve()
    if not path.is_file():
        raise ValueError("EXISTING_LEDGER_REQUIRED")
    # SQLite read-only URI prevents accidental creation of a missing database.
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=2)) as db:
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        metadata = dict(db.execute("SELECT key,value FROM metadata LIMIT 101"))
        if metadata.get("environment") != "PAPER" or metadata.get("account_id") != "paper-account" or metadata.get("schema_version") != "1":
            raise ValueError("NOT_DEDICATED_PAPER_LEDGER")
        active = metadata.get("active_campaign_id")
        cursor = db.execute("SELECT payload FROM orders LIMIT ?", (MAX_ORDERS + 1,))
        rows = [row[0] for row in cursor]
        report = summarize_legacy_orders(rows, active_campaign=active)
        db.rollback()
        return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ledger", type=Path, help="Existing PAPER execution SQLite file")
    args = parser.parse_args(argv)
    print(json.dumps(preflight(args.ledger), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
