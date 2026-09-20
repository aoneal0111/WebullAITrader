from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
import json
from types import SimpleNamespace

from app.daily_review import DailyReviewExporter, _lifecycle_attribution


NOW = datetime(2026, 9, 19, 20, 0, tzinfo=UTC)


class OrderBook:
    def __init__(self, orders=()):
        self._orders = tuple(orders)

    def history(self):
        return self._orders


def test_daily_review_exports_authoritative_orders_account_and_seeds(tmp_path):
    fill = SimpleNamespace(
        quantity=Decimal("5"),
        price=Decimal("10.25"),
        commission=Decimal("0"),
        timestamp=NOW - timedelta(minutes=2),
    )
    request = SimpleNamespace(
        side=SimpleNamespace(value="BUY"),
        order_type=SimpleNamespace(value="LIMIT"),
        quantity=Decimal("5"),
        limit_price=Decimal("10.25"),
        stop_price=None,
        execution_reason="ENTRY",
        strategy_lifecycle_id="WARRIOR|XYZ|episode",
    )
    order = SimpleNamespace(
        order_id="PAPER-ONE",
        symbol="XYZ",
        request=request,
        status=SimpleNamespace(value="FILLED"),
        filled_quantity=Decimal("5"),
        remaining_quantity=Decimal("0"),
        average_fill_price=Decimal("10.25"),
        created_at=NOW - timedelta(minutes=3),
        updated_at=NOW - timedelta(minutes=2),
        fills=(fill,),
    )
    account = SimpleNamespace(
        current_cash=Decimal("9948.75"),
        realized_pnl=Decimal("0"),
        account_id="must-not-export",
    )
    position = SimpleNamespace(symbol="XYZ", quantity="5", market_value="51.25")
    projections = SimpleNamespace(
        paper_account_projection=SimpleNamespace(snapshot=account),
        position_projection=SimpleNamespace(
            snapshot=SimpleNamespace(positions=(position,))
        ),
    )
    watch_path = tmp_path / "catalyst_watch_seeds.json"
    watch_path.write_text(
        json.dumps(
            [
                {
                    "symbol": "XYZ",
                    "headline": "XYZ announces results",
                    "source": "CNBC",
                    "source_url": "https://example.test/story",
                    "provider_event_id": "story",
                    "published_at": NOW.isoformat(),
                    "discovered_at": NOW.isoformat(),
                    "unexpected": "drop",
                }
            ]
        ),
        encoding="utf-8",
    )
    exporter = DailyReviewExporter(
        tmp_path / "daily_reviews",
        retention_days=14,
        clock=lambda: NOW,
        catalyst_watch_path=watch_path,
    )
    old_fill = SimpleNamespace(
        quantity=Decimal("2"), price=Decimal("9.50"),
        commission=Decimal("0"), timestamp=NOW - timedelta(days=1),
    )
    old_order = SimpleNamespace(
        order_id="PAPER-OLD", symbol="OLD", request=request,
        status=SimpleNamespace(value="FILLED"),
        filled_quantity=Decimal("2"), remaining_quantity=Decimal("0"),
        average_fill_price=Decimal("9.50"),
        created_at=NOW - timedelta(days=1),
        updated_at=NOW - timedelta(days=1),
        fills=(old_fill,),
    )
    durable_store = SimpleNamespace(orders=lambda: (old_order, order))
    composition = SimpleNamespace(
        # Durable history must win over the process-local order book so a
        # same-day restart cannot erase earlier review evidence.
        paper_order_book=OrderBook(()),
        paper_trading_commands=SimpleNamespace(durable_store=durable_store),
        paper_entry_intelligence=SimpleNamespace(
            _journal=SimpleNamespace(status=lambda: {
                "framework_version": "ATLAS_PAPER_EXPERIMENTS_V1",
                "experiments": [],
            })
        ),
        runtime_projections=projections,
    )

    target = exporter.export(composition)

    assert target is not None and target.exists()
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["trading_date"] == "2026-09-19"
    assert payload["trading_timezone"] == "America/New_York"
    assert payload["summary"] == {
        "order_count": 1,
        "fill_count": 1,
        "filled_buy_count": 1,
        "filled_sell_count": 0,
        "active_position_count": 1,
        "catalyst_watch_seed_count": 1,
        "attributed_lifecycle_count": 1,
        "attributed_net_realized_pnl": "0",
    }
    assert [item["order_id"] for item in payload["orders"]] == ["PAPER-ONE"]
    assert payload["orders"][0]["strategy_lifecycle_id"] == "WARRIOR|XYZ|episode"
    assert payload["fills"][0]["price"] == "10.25"
    assert payload["account"]["current_cash"] == "9948.75"
    assert "account_id" not in payload["account"]
    assert "unexpected" not in payload["catalyst_watch_seeds"][0]
    assert payload["positions"][0]["symbol"] == "XYZ"
    assert payload["shadow_experiments"] == {
        "framework_version": "ATLAS_PAPER_EXPERIMENTS_V1",
        "experiments": [],
    }
    assert payload["performance_attribution"][0]["status"] == "OPEN"
    assert payload["performance_attribution"][0]["entry_quantity"] == "5"


def test_lifecycle_attribution_calculates_realized_outcome():
    attribution = _lifecycle_attribution([
        {
            "order_id": "BUY-1", "symbol": "XYZ", "side": "BUY",
            "quantity": "10", "price": "10", "commission": "0.25",
            "timestamp": (NOW - timedelta(minutes=10)).isoformat(),
            "execution_reason": "ENTRY",
            "strategy_lifecycle_id": "WARRIOR|XYZ|episode",
        },
        {
            "order_id": "SELL-1", "symbol": "XYZ", "side": "SELL",
            "quantity": "4", "price": "12", "commission": "0.25",
            "timestamp": NOW.isoformat(),
            "execution_reason": "FIRST_TARGET",
            "strategy_lifecycle_id": "WARRIOR|XYZ|episode",
        },
    ])

    assert attribution == [{
        "strategy_lifecycle_id": "WARRIOR|XYZ|episode",
        "symbol": "XYZ",
        "status": "OPEN",
        "entry_quantity": "10",
        "exit_quantity": "4",
        "remaining_quantity": "6",
        "matched_quantity": "4",
        "average_entry_price": "10",
        "average_exit_price": "12",
        "gross_realized_pnl": "8",
        "commissions": "0.50",
        "net_realized_pnl": "7.50",
        "holding_seconds": 600,
        "first_entry_at": (NOW - timedelta(minutes=10)).isoformat(),
        "last_exit_at": NOW.isoformat(),
        "exit_reasons": ["FIRST_TARGET"],
    }]


def test_daily_review_prunes_only_expired_review_files(tmp_path):
    root = tmp_path / "daily_reviews"
    root.mkdir()
    old = root / "atlas-daily-review-2026-09-05.json"
    retained = root / "atlas-daily-review-2026-09-06.json"
    unrelated = root / "notes.json"
    for path in (old, retained, unrelated):
        path.write_text("{}", encoding="utf-8")
    exporter = DailyReviewExporter(root, retention_days=14, clock=lambda: NOW)

    removed = exporter.prune(NOW.date())

    assert removed == (old,)
    assert not old.exists()
    assert retained.exists()
    assert unrelated.exists()


def test_review_export_failure_never_escapes(tmp_path):
    exporter = DailyReviewExporter(
        tmp_path / "blocked",
        clock=lambda: datetime(2026, 9, 19),
    )

    assert exporter.export(SimpleNamespace()) is None
