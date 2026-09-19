from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
import json
from types import SimpleNamespace

from app.daily_review import DailyReviewExporter


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
    composition = SimpleNamespace(
        paper_order_book=OrderBook((order,)),
        runtime_projections=projections,
    )

    target = exporter.export(composition)

    assert target is not None and target.exists()
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["summary"] == {
        "order_count": 1,
        "fill_count": 1,
        "filled_buy_count": 1,
        "filled_sell_count": 0,
        "active_position_count": 1,
        "catalyst_watch_seed_count": 1,
    }
    assert payload["orders"][0]["strategy_lifecycle_id"] == "WARRIOR|XYZ|episode"
    assert payload["fills"][0]["price"] == "10.25"
    assert payload["account"]["current_cash"] == "9948.75"
    assert "account_id" not in payload["account"]
    assert "unexpected" not in payload["catalyst_watch_seeds"][0]


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
