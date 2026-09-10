from datetime import datetime, timezone
from decimal import Decimal

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
