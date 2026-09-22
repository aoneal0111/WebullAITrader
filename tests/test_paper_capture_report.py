from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from app.paper_capture_report import build_paper_capture_report


def _execution(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript("""
            CREATE TABLE paper_campaigns(
                campaign_id TEXT PRIMARY KEY,status TEXT,started_at TEXT,
                ended_at TEXT,reason TEXT);
            CREATE TABLE orders(order_id TEXT PRIMARY KEY,payload TEXT);
        """)
        connection.execute(
            "INSERT INTO paper_campaigns VALUES(?,?,?,?,?)",
            ("campaign-1", "ACTIVE", "2026-09-22T14:00:00+00:00", None, "test"),
        )
        for order_id, side, lifecycle, quantity, price, commission, slippage, status, order_type in (
            ("buy-1", "BUY", "life-closed", "6", "5", "0.6", "0.01", "FILLED", "LIMIT"),
            ("buy-1b", "BUY", "life-closed", "4", "5", "0.4", "0.01", "FILLED", "LIMIT"),
            ("sell-1", "SELL", "life-closed", "10", "6", "1", "0.02", "FILLED", "LIMIT"),
            ("buy-2", "BUY", "life-open", "4", "8", "0.5", "0.01", "FILLED", "LIMIT"),
            ("stop-2", "SELL", "life-open", "4", "0", "0", "0", "ACCEPTED", "STOP"),
            ("target-2", "SELL", "life-open", "2", "0", "0", "0", "ACCEPTED", "LIMIT"),
            ("buy-3", "BUY", None, "2", "3", "0", "0", "FILLED", "LIMIT"),
        ):
            payload = {
                "paper_campaign_id": "campaign-1",
                "request": {
                    "symbol": "TEST", "side": side,
                    "strategy_lifecycle_id": lifecycle,
                    "quantity": quantity, "order_type": order_type,
                    "stop_price": "7" if order_type == "STOP" else None,
                    "metadata": {"reservation_mode": (
                        "CONTINGENT_OCO" if order_id == "stop-2" else None
                    )},
                },
                "status": status,
                "filled_quantity": quantity if status == "FILLED" else "0",
                "fills": [] if status != "FILLED" else [{
                    "fill_id": f"fill-{order_id}", "quantity": quantity,
                    "price": price, "commission": commission,
                    "slippage": slippage,
                    "timestamp": "2026-09-22T15:00:00+00:00",
                }],
            }
            connection.execute(
                "INSERT INTO orders VALUES(?,?)", (order_id, json.dumps(payload)),
            )


def _experiment(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript("""
            CREATE TABLE experiment_candidates(
                features_json TEXT NOT NULL,labels_json TEXT NOT NULL,
                execution_json TEXT NOT NULL);
            CREATE TABLE research_worker_telemetry(
                singleton INTEGER PRIMARY KEY,items_rejected INTEGER,
                queue_high_water INTEGER,lag_max_ms REAL,resumed INTEGER,
                updated_at TEXT);
            CREATE TABLE research_capture_gaps(
                reason TEXT,episode_count INTEGER,rejected_count INTEGER,
                started_at TEXT,ended_at TEXT);
            CREATE TABLE research_worker_sessions(
                session_id TEXT,started_at TEXT,updated_at TEXT,ended_at TEXT,
                enqueued INTEGER,completed INTEGER,rejected INTEGER,
                queue_high_water INTEGER,lag_max_ms REAL,pressure_episodes INTEGER);
        """)
        connection.execute(
            "INSERT INTO experiment_candidates VALUES(?,?,?)",
            (json.dumps({
                "bid": "1", "ask": "1.01", "spread_percent": "1",
                "float_shares": "100", "halted": False, "tradable": True,
                "catalyst_status": "FALSE",
            }), json.dumps({"outcome_status": "COMPLETE"}),
             json.dumps({"state": "NOT_EXECUTED"})),
        )
        connection.execute(
            "INSERT INTO experiment_candidates VALUES(?,?,?)",
            (json.dumps({"catalyst_status": "UNAVAILABLE"}),
             json.dumps({"outcome_status": "PENDING"}),
             json.dumps({"state": "NOT_EXECUTED"})),
        )
        connection.execute(
            "INSERT INTO research_worker_telemetry VALUES(1,7,8,9,0,?)",
            ("2026-09-22T15:00:00+00:00",),
        )
        connection.execute(
            "INSERT INTO research_capture_gaps VALUES(?,?,?,?,?)",
            ("BOUNDED_RESEARCH_QUEUE_FULL", 1, 3,
             "2026-09-22T14:00:00+00:00", "2026-09-22T14:01:00+00:00"),
        )


def _forward(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE capture_records(record_id TEXT,record_type TEXT,timestamp TEXT,payload_json TEXT)"
        )
        connection.execute(
            "INSERT INTO capture_records VALUES(?,?,?,?)",
            ("entry", "PAPER_FILL", "2026-09-22T15:00:00+00:00", json.dumps({
                "paper_campaign_id": "campaign-1",
                "lifecycle_id": "life-closed",
                "action": "ENTRY", "session": "REGULAR",
                "setup": "BULL_FLAG",
            }),),
        )
        for record_id, timestamp, payload in (
            ("path-buy", "2026-09-22T15:00:00+00:00", {
                "action": "FILL", "paper_campaign_id": "campaign-1",
                "lifecycle_id": "life-closed", "side": "BUY",
                "quantity": "10", "price": "5",
            }),
            ("path-quote", "2026-09-22T15:00:01+00:00", {
                "action": "QUOTE", "paper_campaign_id": "campaign-1",
                "lifecycle_id": "life-closed", "midpoint": "5.5",
                "stale_quote": False, "missing_interval": False,
            }),
            ("path-sell", "2026-09-22T15:00:02+00:00", {
                "action": "FILL", "paper_campaign_id": "campaign-1",
                "lifecycle_id": "life-closed", "side": "SELL",
                "quantity": "10", "price": "6",
            }),
        ):
            connection.execute(
                "INSERT INTO capture_records VALUES(?,?,?,?)",
                (record_id, "EXECUTION_PRICE_PATH", timestamp, json.dumps(payload)),
            )


def test_report_uses_exact_authoritative_ids_and_marks_unavailable_metrics(
    tmp_path: Path,
) -> None:
    execution = tmp_path / "execution.sqlite3"
    experiment = tmp_path / "experiment.sqlite3"
    forward = tmp_path / "forward.sqlite3"
    _execution(execution)
    _experiment(experiment)
    _forward(forward)

    report = build_paper_capture_report(
        experiment_path=experiment,
        execution_path=execution,
        forward_path=forward,
    )

    closed = report["completed_trade_lifecycles"][0]
    assert closed["lifecycle_id"] == "life-closed"
    assert closed["gross_fill_pnl"] == "10"
    assert closed["recorded_commissions"] == "2.0"
    assert closed["recorded_net_pnl"] == "8.0"
    assert closed["recorded_measured_slippage_cost"] == "0.30"
    assert closed["zero_remaining_inventory_verified"] is True
    assert closed["scale_in"] is True
    assert closed["mae"] == "0" and closed["mfe"] == "0.5"
    assert closed["execution_price_path"]["coverage"] == "COMPLETE"
    assert closed["forward_capture_correlation"] == "EXACT"
    assert report["open_positions"][0]["lifecycle_id"] == "life-open"
    protection = report["open_positions"][0]["persisted_position_and_protection"]
    assert protection["persisted_symbol_inventory"] == "4"
    assert protection["hard_stop_covers_full_remaining_position"] is True
    assert protection["reserved_working_sell_quantity_exceeds_remaining_position"] is False
    assert protection["working_sell_trigger_quantity"] == "6"
    assert protection["reserved_working_sell_quantity"] == "2"
    assert protection["read_only_reconciliation_preview"] is None
    assert report["execution_integrity"]["active_campaign_symbol_inventory"] == [{
        "paper_campaign_id": "campaign-1", "symbol": "TEST", "quantity": "4",
    }]
    assert report["execution_integrity"]["working_sell_orders"][0]["remaining_order_quantity"] == "4"
    reasons = {item["reason"] for item in report["unmatched_execution_fills"]}
    assert reasons == {"MISSING_LIFECYCLE_ID", "NO_EXACT_FORWARD_CAPTURE_KEY"}
    capture = report["experiment_capture"]
    assert capture["outcome_complete"] == 1
    assert capture["outcome_pending"] == 1
    assert capture["decision_evidence_complete"] == 1
    assert capture["decision_evidence_incomplete"] == 1
    assert capture["positive_catalyst_present"] == 0
    assert capture["explicit_capture_gaps"]["observations"] == 3
    assert capture["legacy_rejections_without_gap_detail"] == 4
    performance = report["completed_trade_breakdown"]
    assert performance["sample_size"] == 1
    assert performance["win_rate"] == "1"
    assert performance["expectancy"] == "8.0"
    assert performance["maximum_closed_trade_cumulative_pnl_drawdown"] == "0"
    assert report["completed_trade_breakdown_by_supported_attribution"]["setup"]["groups"]["BULL_FLAG"]["sample_size"] == 1
    assert report["hypothetical_execution_cost_sensitivity"][0]["adjusted_cumulative_pnl"] == "7.900"


def test_report_deduplicates_identical_fill_ids(tmp_path: Path) -> None:
    execution = tmp_path / "execution.sqlite3"
    experiment = tmp_path / "experiment.sqlite3"
    forward = tmp_path / "forward.sqlite3"
    _execution(execution)
    _experiment(experiment)
    _forward(forward)
    with sqlite3.connect(execution) as connection:
        payload = json.loads(connection.execute(
            "SELECT payload FROM orders WHERE order_id='buy-1'"
        ).fetchone()[0])
        duplicate = dict(payload["fills"][0])
        payload["fills"].append(duplicate)
        connection.execute(
            "UPDATE orders SET payload=? WHERE order_id='buy-1'",
            (json.dumps(payload),),
        )

    report = build_paper_capture_report(
        experiment_path=experiment, execution_path=execution,
        forward_path=forward,
    )

    assert report["execution_integrity"]["exact_duplicate_fill_records_suppressed"] == 1
    assert report["completed_trade_lifecycles"][0]["buy_quantity"] == "10"


def test_report_previews_legacy_partial_target_upgrade_without_mutation(
    tmp_path: Path,
) -> None:
    execution = tmp_path / "execution.sqlite3"
    experiment = tmp_path / "experiment.sqlite3"
    forward = tmp_path / "forward.sqlite3"
    _execution(execution)
    _experiment(experiment)
    _forward(forward)
    with sqlite3.connect(execution) as connection:
        payload = json.loads(connection.execute(
            "SELECT payload FROM orders WHERE order_id='stop-2'"
        ).fetchone()[0])
        payload["request"]["quantity"] = "2"
        payload["request"]["metadata"] = {}
        connection.execute(
            "UPDATE orders SET payload=? WHERE order_id='stop-2'",
            (json.dumps(payload),),
        )

    report = build_paper_capture_report(
        experiment_path=experiment, execution_path=execution,
        forward_path=forward,
    )

    preview = report["open_positions"][0][
        "persisted_position_and_protection"
    ]["read_only_reconciliation_preview"]
    assert preview == {
        "action": "ATOMIC_UPGRADE_EXISTING_STOP_TO_CONTINGENT_OCO",
        "existing_stop_order_id": "stop-2",
        "correlated_target_order_id": "target-2",
        "current_stop_quantity": "2",
        "intended_stop_quantity": "4",
        "intended_hard_stop_coverage": "4",
        "intended_reserved_sell_quantity": "2",
        "requires_new_order": False,
        "requires_cancellation": False,
        "mutation_performed": False,
    }
