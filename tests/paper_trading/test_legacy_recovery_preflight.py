"""Focused tests for legacy-only read-only inventory reporting."""
import json
import sqlite3

import pytest

from app.paper_trading.legacy_recovery_preflight import (
    preflight, summarize_legacy_orders,
)


def order(symbol, side, quantity, filled, *, status, lifecycle=None,
          campaign=None, kind="LIMIT"):
    return json.dumps({
        "order_id": symbol + "-" + side + "-" + str(quantity),
        "paper_campaign_id": campaign,
        "request": {
            "symbol": symbol, "side": side, "quantity": str(quantity),
            "strategy_lifecycle_id": lifecycle or "WARRIOR_MOMENTUM_V1|" + symbol,
            "order_type": kind,
        },
        "filled_quantity": str(filled), "status": status,
        "fills": ([{"quantity": str(filled)}] if filled else []),
    })


def test_legacy_positions_are_reported_but_not_promoted():
    active = "paper-active"
    rows = [
        order("ODD", "BUY", 783, 197, status="EXPIRED"),
        order("SUNE", "BUY", 759, 759, status="FILLED"),
        order("SUNE", "SELL", 759, 0, status="ACCEPTED", kind="STOP"),
        order("WYHG", "BUY", 30, 30, status="FILLED"),
        order("WYHG", "SELL", 30, 0, status="ACCEPTED", kind="LIMIT"),
        order("BIAF", "BUY", 50, 50, status="FILLED"),
        order("BIAF", "BUY", 46, 46, status="FILLED", campaign=active,
              lifecycle="QUICK_SCALPER|BIAF"),
        order("BIAF", "SELL", 46, 46, status="FILLED", campaign=active,
              lifecycle="QUICK_SCALPER|BIAF"),
    ]
    report = summarize_legacy_orders(rows, active_campaign=active)
    positions = {row["symbol"]: row for row in report["legacy_positions"]}
    assert report["orders_by_campaign"] == {"legacy-paper-campaign": 6, active: 2}
    assert {symbol: row["net_filled_shares"] for symbol, row in positions.items()} == {
        "BIAF": "50", "ODD": "197", "SUNE": "759", "WYHG": "30"
    }
    assert report["legacy_stop_gaps"] == 3
    assert positions["SUNE"]["working_stop_shares"] == "759"
    assert positions["WYHG"]["working_targets"][0]["remaining_quantity"] == "30"
    assert report["runtime_action"] == "NONE"
    assert report["recovery_authorized"] is False


def test_rejects_bad_fills_and_oversell():
    bad = json.loads(order("ODD", "BUY", 10, 5, status="EXPIRED"))
    bad["fills"] = []
    with pytest.raises(ValueError, match="FILL_MISMATCH"):
        summarize_legacy_orders([json.dumps(bad)], active_campaign="paper-a")
    negative = [
        order("ODD", "SELL", 2, 2, status="FILLED"),
    ]
    report = summarize_legacy_orders(negative, active_campaign="paper-a")
    assert report["legacy_ambiguities"] == 1


def test_rejects_limit_and_invalid_metadata(tmp_path):
    with pytest.raises(ValueError, match="ORDER_LIMIT"):
        summarize_legacy_orders(["{}"] * 5001, active_campaign="paper-a")
    path = tmp_path / "paper.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE metadata(key TEXT, value TEXT)")
        db.execute("CREATE TABLE orders(payload TEXT)")
        db.executemany("INSERT INTO metadata VALUES(?,?)", [
            ("environment", "PAPER"), ("account_id", "paper-account"),
            ("schema_version", "1"), ("active_campaign_id", "paper-a"),
        ])
        db.execute("INSERT INTO orders VALUES(?)", (
            order("ODD", "BUY", 783, 197, status="EXPIRED"),))
    before = path.read_bytes()
    report = preflight(path)
    assert report["legacy_nonzero_positions"] == 1
    assert path.read_bytes() == before
    with sqlite3.connect(path) as db:
        db.execute("UPDATE metadata SET value='LIVE' WHERE key='environment'")
    with pytest.raises(ValueError, match="NOT_DEDICATED_PAPER_LEDGER"):
        preflight(path)


def test_active_campaign_inventory_is_separate_from_legacy():
    rows = [
        order("ODD", "BUY", 200, 197, status="EXPIRED"),
        order("ODD", "BUY", 10, 10, status="FILLED", campaign="paper-a"),
        order("ODD", "SELL", 4, 4, status="FILLED", campaign="paper-a"),
        order("ODD", "SELL", 6, 0, status="ACCEPTED", campaign="paper-a", kind="STOP"),
        order("XYZ", "BUY", 3, 0, status="ACCEPTED", campaign="paper-a"),
    ]
    result = summarize_legacy_orders(rows, active_campaign="paper-a")
    assert result["legacy_nonzero_positions"] == 1
    assert result["legacy_positions"][0]["net_filled_shares"] == "197"
    active = {row["symbol"]: row for row in result["active_campaign_inventory"]}
    assert active["ODD"] == {
        "symbol": "ODD", "net_filled_shares": "6",
        "working_buy_shares": "0", "working_sell_shares": "6",
        "working_order_count": 1, "negative_inventory": False,
    }
    assert active["XYZ"]["net_filled_shares"] == "0"
    assert active["XYZ"]["working_buy_shares"] == "3"
    assert result["active_nonzero_positions"] == 1
    assert result["active_working_orders"] == 2
    assert result["active_negative_inventory"] == 0
    assert result["runtime_action"] == "NONE"


def test_active_fills_must_reconcile_even_if_legacy_is_clean():
    bad = json.loads(order("ODD", "BUY", 10, 5, status="EXPIRED", campaign="paper-a"))
    bad["fills"] = []
    with pytest.raises(ValueError, match="FILL_MISMATCH"):
        summarize_legacy_orders([json.dumps(bad)], active_campaign="paper-a")


def test_active_negative_inventory_is_reported_not_repaired():
    result = summarize_legacy_orders(
        [order("XYZ", "SELL", 3, 3, status="FILLED", campaign="paper-a")],
        active_campaign="paper-a",
    )
    assert result["active_negative_inventory"] == 1
    assert result["active_campaign_inventory"][0]["net_filled_shares"] == "-3"
    assert result["recovery_authorized"] is False


def test_other_campaign_not_conflated_with_active_or_legacy():
    result = summarize_legacy_orders(
        [order("XYZ", "BUY", 7, 7, status="FILLED", campaign="paper-old")],
        active_campaign="paper-a",
    )
    assert result["other_campaign_orders"] == 1
    assert result["active_campaign_inventory"] == []
    assert result["legacy_positions"] == []
