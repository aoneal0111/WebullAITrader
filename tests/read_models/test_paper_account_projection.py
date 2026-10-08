from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

from app.operations.runtime import PaperRuntimeEvent
from app.operations_core import OperationsBus
from app.paper_gateway.durable_store import DEFAULT_ATLAS_PAPER_STARTING_CASH
from app.paper_trading.models import PaperFill
from app.read_models.order_projection import OrderProjection
from app.read_models.paper_account_projection import PaperAccountProjection
from app.read_models.position_projection import PositionProjection


def _event(sequence, side, quantity, price, realized="0", symbol="SUNE"):
    return PaperRuntimeEvent(
        sequence=sequence, timestamp=datetime(2026, 1, sequence, tzinfo=timezone.utc),
        event_type="ORDER_FILLED", message="fill", cycle=0, symbol=symbol,
        source="test", mark_price=Decimal(price),
        fill=PaperFill(
            request_id=f"fill-{sequence}", symbol=symbol, side=side,
            quantity=Decimal(quantity), fill_price=Decimal(price),
            notional=Decimal(quantity) * Decimal(price),
            realized_pnl=Decimal(realized),
            timestamp=datetime(2026, 1, sequence, tzinfo=timezone.utc),
        ),
    )


def _mark(sequence, symbol, price):
    return PaperRuntimeEvent(
        sequence=sequence, timestamp=datetime(2026, 1, sequence, tzinfo=timezone.utc),
        event_type="MARK", message="mark", cycle=0, symbol=symbol,
        source="test", mark_price=Decimal(price),
    )


def _projection(starting_cash=Decimal("100000")):
    bus = OperationsBus()
    positions = PositionProjection(bus, account_id="paper-account")
    orders = OrderProjection(bus)
    account = PaperAccountProjection(
        campaign_id="paper-test", starting_cash=starting_cash,
        position_projection=positions, order_projection=orders,
    )
    return positions, orders, account


def test_partial_exit_accounting_and_runner():
    positions, orders, account = _projection()
    positions(_event(1, "BUY", "100", "10"))
    account(_event(1, "BUY", "100", "10"))
    assert account.snapshot.current_cash == Decimal("99000")
    assert account.snapshot.current_equity == Decimal("100000")

    positions(_event(2, "SELL", "50", "11", "50"))
    account(_event(2, "SELL", "50", "11", "50"))
    assert account.snapshot.current_cash == Decimal("99550")
    assert account.snapshot.realized_pnl == Decimal("50")
    assert account.snapshot.active_position_count == 1
    assert account.snapshot.open_position_market_value == Decimal("550")
    assert account.snapshot.current_equity == Decimal("100100")

    positions(_event(3, "SELL", "25", "12", "50"))
    account(_event(3, "SELL", "25", "12", "50"))
    snapshot = account.snapshot
    assert snapshot.current_cash == Decimal("99850")
    assert snapshot.realized_pnl == Decimal("100")
    assert snapshot.open_position_market_value == Decimal("300")
    assert snapshot.current_equity == Decimal("100150")
    assert snapshot.total_pnl == Decimal("150")


def test_loss_accounting_ends_flat_with_lower_equity():
    positions, orders, account = _projection()
    positions(_event(1, "BUY", "100", "10"))
    account(_event(1, "BUY", "100", "10"))
    positions(_event(2, "SELL", "100", "9", "-100"))
    account(_event(2, "SELL", "100", "9", "-100"))
    snapshot = account.snapshot
    assert snapshot.active_position_count == 0
    assert snapshot.current_cash == Decimal("99900")
    assert snapshot.current_equity == Decimal("99900")
    assert snapshot.realized_pnl == Decimal("-100")
    assert snapshot.unrealized_pnl == Decimal("0")
    assert snapshot.buying_power == Decimal("99900")


def test_empty_projection_uses_explicit_local_paper_default():
    positions, orders, account = _projection(DEFAULT_ATLAS_PAPER_STARTING_CASH)
    assert account.snapshot.starting_equity == Decimal("10000")
    assert account.snapshot.starting_cash == Decimal("10000")
    assert account.snapshot.buying_power == Decimal("10000")


def test_campaign_capital_source_is_immutable_account_authority():
    bus = OperationsBus()
    positions = PositionProjection(bus, account_id="paper-account")
    orders = OrderProjection(bus)
    account = PaperAccountProjection(
        campaign_id="fallback", starting_cash=Decimal("100000"),
        position_projection=positions, order_projection=orders,
        capital_source=lambda: {
            "starting_equity": Decimal("10000"),
            "starting_cash": Decimal("10000"),
            "buying_power_multiplier": Decimal("1"),
        },
    )
    assert account.snapshot.starting_equity == Decimal("10000")


def test_two_symbol_aggregation_and_partial_exit_accounting():
    positions, orders, account = _projection(Decimal("100000"))
    for event in (
        _event(1, "BUY", "100", "10", symbol="AAA"),
        _event(2, "BUY", "50", "20", symbol="BBB"),
        _mark(3, "AAA", "11"),
        _mark(4, "BBB", "18"),
    ):
        positions(event)
        account(event)
    snapshot = account.snapshot
    assert snapshot.current_cash == Decimal("98000")
    assert snapshot.open_position_market_value == Decimal("2000")
    assert snapshot.gross_exposure == Decimal("2000")
    assert snapshot.net_exposure == Decimal("2000")
    assert snapshot.unrealized_pnl == Decimal("0")
    assert snapshot.realized_pnl == Decimal("0")
    assert snapshot.current_equity == Decimal("100000")
    assert snapshot.total_pnl == Decimal("0")
    assert snapshot.buying_power == Decimal("98000")
    assert snapshot.active_position_count == 2

    for event in (
        _event(5, "SELL", "50", "11", "50", symbol="AAA"),
        _event(6, "SELL", "50", "18", "-100", symbol="BBB"),
    ):
        positions(event)
        account(event)
    snapshot = account.snapshot
    assert snapshot.current_cash == Decimal("99450")
    assert snapshot.open_position_market_value == Decimal("550")
    assert snapshot.realized_pnl == Decimal("-50")
    assert snapshot.unrealized_pnl == Decimal("50")
    assert snapshot.current_equity == Decimal("100000")
    assert snapshot.total_pnl == Decimal("0")
    assert snapshot.buying_power == Decimal("99450")
    assert snapshot.active_position_count == 1

    final = _event(7, "SELL", "50", "11", "50", symbol="AAA")
    positions(final)
    account(final)
    snapshot = account.snapshot
    assert snapshot.current_cash == Decimal("100000")
    assert snapshot.current_equity == Decimal("100000")
    assert snapshot.realized_pnl == Decimal("0")
    assert snapshot.unrealized_pnl == Decimal("0")
    assert snapshot.active_position_count == 0


def test_durable_fill_reconciliation_restores_cash_and_realized_pnl():
    positions, _orders, account = _projection(Decimal("10000"))
    buy_fill = SimpleNamespace(
        order_id="buy", quantity=Decimal("98"), price=Decimal("15.65"),
        commission=Decimal("0"), timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    sell_fill = SimpleNamespace(
        order_id="sell", quantity=Decimal("49"), price=Decimal("17.10"),
        commission=Decimal("0"), timestamp=datetime(2026, 1, 2, tzinfo=timezone.utc),
    )
    durable_orders = (
        SimpleNamespace(
            symbol="TJGC", request=SimpleNamespace(side="BUY"),
            fills=(buy_fill,),
        ),
        SimpleNamespace(
            symbol="TJGC", request=SimpleNamespace(side="SELL"),
            fills=(sell_fill,),
        ),
    )

    positions.reconcile_from_paper_orders(durable_orders)
    account.reconcile_from_paper_orders(durable_orders)

    snapshot = account.snapshot
    assert snapshot.current_cash == Decimal("9304.20")
    assert snapshot.realized_pnl == Decimal("71.05")
    assert snapshot.fees == Decimal("0")
    assert snapshot.active_position_count == 1
    assert snapshot.buying_power == Decimal("9304.20")
    assert snapshot.current_equity is None


def test_reconciled_position_mark_populates_valuation_without_changing_cash():
    positions, orders, account = _projection(Decimal("10000"))
    buy_fill = SimpleNamespace(
        order_id="buy", quantity=Decimal("100"), price=Decimal("10"),
        commission=Decimal("0"), timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    durable_orders = (
        SimpleNamespace(
            symbol="BNKK", request=SimpleNamespace(side="BUY"),
            fills=(buy_fill,),
        ),
    )
    positions.reconcile_from_paper_orders(durable_orders)
    account.reconcile_from_paper_orders(durable_orders)
    before = account.snapshot
    assert before.current_equity is None

    mark = _mark(1, "BNKK", "12")
    positions(mark)
    account(mark)
    after = account.snapshot
    assert after.current_cash == before.current_cash
    assert after.realized_pnl == before.realized_pnl
    assert after.current_equity == Decimal("10200")
    assert after.open_position_market_value == Decimal("1200")
    assert after.unrealized_pnl == Decimal("200")
    assert after.gross_exposure == Decimal("1200")
    assert after.net_exposure == Decimal("1200")


def test_duplicate_fill_cannot_change_cash_or_realized_pnl():
    _positions, _orders, account = _projection()
    buy = _event(1, "BUY", "100", "10")
    account(buy)
    account(buy)
    account(replace(buy, sequence=2))
    assert account.snapshot.current_cash == Decimal("99000")
    sell = _event(3, "SELL", "100", "11", "100")
    account(sell)
    account(replace(sell, source="replayed-fill", sequence=1))
    assert account.snapshot.current_cash == Decimal("100100")
    assert account.snapshot.realized_pnl == Decimal("100")


def test_restored_fill_is_not_debited_again_on_later_delivery():
    _positions, _orders, account = _projection()
    buy = _event(1, "BUY", "100", "10")
    durable_order = SimpleNamespace(
        symbol="SUNE", request=SimpleNamespace(side="BUY"),
        fills=(SimpleNamespace(fill_id=buy.fill.request_id, timestamp=buy.timestamp,
                               quantity=buy.fill.quantity, price=buy.fill.fill_price,
                               commission=Decimal("0")),),
    )
    account.reconcile_from_paper_orders((durable_order,))
    account(buy)
    assert account.snapshot.current_cash == Decimal("99000")
