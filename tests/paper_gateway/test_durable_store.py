from datetime import datetime, timezone
from decimal import Decimal
import json

import pytest

from app.market_data.models import MarketEvent, MarketEventType, QuotePayload
from app.operations_core import OperationsBus
from app.composition.runtime_projection_pipeline import create_runtime_projection_pipeline
from app.paper_gateway.durable_store import DurablePaperExecutionStore
from app.paper_trading.command_composition import create_paper_trading_command_composition
from app.paper_trading.command_composition import PAPER_ACCOUNT_ID
from app.strategies.warrior_momentum.autonomous_paper import AutonomousPaperExecutionBridge


class Signal:
    symbol = "PMI"
    entry_trigger = Decimal("10")
    lifecycle_id = "trade-a"


def quote(sequence: int, bid: str, ask: str) -> MarketEvent:
    return MarketEvent(
        sequence, datetime.now(timezone.utc), "PMI", "test", MarketEventType.QUOTE,
        QuotePayload(Decimal(bid), Decimal(ask), Decimal("100"), Decimal("100")),
    )


def test_working_order_rehydrates_without_resubmission(tmp_path) -> None:
    path = tmp_path / "paper.sqlite3"
    first = create_paper_trading_command_composition(persistence_path=str(path))
    bridge = AutonomousPaperExecutionBridge(
        first.trading_service, first.order_command_factory, order_book=first.order_book,
    )
    assert bridge.submit_entry(Signal(), 100, Decimal("50"))
    order_id = first.order_book.history()[0].order_id
    assert first.order_book.history()[0].request.strategy_lifecycle_id == "trade-a"
    first.close()

    events = []
    second = create_paper_trading_command_composition(
        persistence_path=str(path), event_sink=events.append,
    )
    assert len(second.order_book.history()) == 1
    assert second.order_book.history()[0].order_id == order_id
    assert second.order_book.history()[0].request.strategy_lifecycle_id == "trade-a"
    assert second.order_book.history()[0].remaining_quantity == Decimal("100")
    restored_bridge = AutonomousPaperExecutionBridge(
        second.trading_service, second.order_command_factory, order_book=second.order_book,
    )
    assert restored_bridge.reconcile().value == "READY"
    assert restored_bridge._active_by_symbol["PMI"] == "trade-a"
    assert restored_bridge.submit_entry(Signal(), 100, Decimal("50")) is False
    assert not any(event.event_type == "ORDER_SUBMITTED" for event in events)
    second.close()


def test_filled_trade_and_realized_pnl_rehydrate_once(tmp_path) -> None:
    path = tmp_path / "paper.sqlite3"
    first = create_paper_trading_command_composition(
        persistence_path=str(path),
        position_average_cost_source=lambda _symbol: Decimal("10"),
        position_quantity_source=lambda _symbol: Decimal("100"),
    )
    bridge = AutonomousPaperExecutionBridge(
        first.trading_service, first.order_command_factory, order_book=first.order_book,
    )
    assert bridge.submit_entry(Signal(), 100, Decimal("50"))
    first.gateway.process_market_event(quote(1, "9.99", "10"))
    assert bridge.submit_exit("PMI", 100, Decimal("10.50"), "STOP")
    first.gateway.process_market_event(quote(2, "10.50", "10.51"))
    first.close()

    events = []
    second = create_paper_trading_command_composition(
        persistence_path=str(path), event_sink=events.append,
        position_average_cost_source=lambda _symbol: Decimal("10"),
        position_quantity_source=lambda _symbol: Decimal("100"),
    )
    assert len(second.order_book.history()) == 2
    fills = [event.fill for event in events if event.fill is not None]
    assert len(fills) == 2
    assert fills[-1].realized_pnl == Decimal("50.00")
    assert len(second.order_book.open_orders()) == 0
    second.close()


def test_replay_rehydrates_authoritative_position_projection(tmp_path) -> None:
    path = tmp_path / "paper.sqlite3"
    bus_a = OperationsBus()
    pipeline_a = create_runtime_projection_pipeline(operations_bus=bus_a, account_id="paper-account")
    first = create_paper_trading_command_composition(
        persistence_path=str(path), event_sink=pipeline_a.sink,
        position_average_cost_source=lambda _symbol: Decimal("10"),
        position_quantity_source=lambda _symbol: Decimal("100"),
    )
    bridge = AutonomousPaperExecutionBridge(first.trading_service, first.order_command_factory, order_book=first.order_book)
    assert bridge.submit_entry(Signal(), 100, Decimal("50"))
    first.gateway.process_market_event(quote(1, "9.99", "10"))
    first.close()

    bus_b = OperationsBus()
    pipeline_b = create_runtime_projection_pipeline(operations_bus=bus_b, account_id="paper-account")
    second = create_paper_trading_command_composition(
        persistence_path=str(path), event_sink=pipeline_b.sink,
        position_average_cost_source=lambda _symbol: Decimal("10"),
        position_quantity_source=lambda _symbol: Decimal("100"),
    )
    positions = pipeline_b.position_projection.snapshot.positions
    assert len(positions) == 1
    assert positions[0].symbol == "PMI"
    assert Decimal(positions[0].quantity) == Decimal("100")
    second.close()


def test_pending_exit_rehydrates_as_working_order(tmp_path) -> None:
    path = tmp_path / "paper.sqlite3"
    first = create_paper_trading_command_composition(
        persistence_path=str(path),
        position_average_cost_source=lambda _symbol: Decimal("10"),
        position_quantity_source=lambda _symbol: Decimal("100"),
    )
    bridge = AutonomousPaperExecutionBridge(first.trading_service, first.order_command_factory, order_book=first.order_book)
    assert bridge.submit_entry(Signal(), 100, Decimal("50"))
    first.gateway.process_market_event(quote(1, "9.99", "10"))
    assert bridge.submit_exit("PMI", 100, Decimal("9.50"), "STOP")
    assert len(first.order_book.open_orders()) == 1
    first.close()

    second = create_paper_trading_command_composition(
        persistence_path=str(path),
        position_average_cost_source=lambda _symbol: Decimal("10"),
        position_quantity_source=lambda _symbol: Decimal("100"),
    )
    assert len(second.order_book.open_orders()) == 1
    restored_bridge = AutonomousPaperExecutionBridge(
        second.trading_service, second.order_command_factory, order_book=second.order_book,
    )
    assert restored_bridge.submit_exit("PMI", 100, Decimal("9.50"), "STOP") is False
    second.close()


def test_store_rejects_account_environment_and_schema_mismatch(tmp_path) -> None:
    path = tmp_path / "paper.sqlite3"
    store = DurablePaperExecutionStore(path, account_id="paper-account")
    store.close()
    with pytest.raises(ValueError, match="identity mismatch"):
        DurablePaperExecutionStore(path, account_id="other-account")
    with __import__("sqlite3").connect(path) as connection:
        connection.execute("UPDATE metadata SET value='LIVE' WHERE key='environment'")
    with pytest.raises(ValueError, match="identity mismatch"):
        DurablePaperExecutionStore(path, account_id="paper-account")
    with __import__("sqlite3").connect(path) as connection:
        connection.execute("UPDATE metadata SET value='PAPER' WHERE key='environment'")
        connection.execute("UPDATE metadata SET value='999' WHERE key='schema_version'")
    with pytest.raises(ValueError, match="unsupported PAPER execution store schema"):
        DurablePaperExecutionStore(path, account_id="paper-account")


def test_corrupt_event_payload_fails_restore(tmp_path) -> None:
    path = tmp_path / "paper.sqlite3"
    store = DurablePaperExecutionStore(path, account_id="paper-account")
    store.close()
    with __import__("sqlite3").connect(path) as connection:
        connection.execute("INSERT INTO events VALUES(1,'ORDER_ACCEPTED',?)", (json.dumps({"bad": True}),))
    reopened = DurablePaperExecutionStore(path, account_id="paper-account")
    with pytest.raises((KeyError, TypeError, ValueError)):
        reopened.events()
    reopened.close()


def _complete_trade(composition, bridge, *, lifecycle_id: str, entry: str, exit: str, quantity_source) -> None:
    signal = Signal()
    signal.lifecycle_id = lifecycle_id
    signal.entry_trigger = Decimal(entry)
    assert bridge.submit_entry(signal, 100, Decimal("50"))
    composition.gateway.process_market_event(quote(1, str(Decimal(entry) - Decimal("0.01")), entry))
    quantity_source["PMI"] = Decimal("100")
    assert bridge.submit_exit("PMI", 100, Decimal(exit), "STOP")
    composition.gateway.process_market_event(quote(2, exit, str(Decimal(exit) + Decimal("0.01"))))


def test_two_same_symbol_trades_restore_once_with_aggregate_pnl(tmp_path) -> None:
    path = tmp_path / "paper.sqlite3"
    quantity = {"PMI": Decimal("0")}
    first = create_paper_trading_command_composition(
        persistence_path=str(path),
        position_average_cost_source=lambda _symbol: Decimal("10"),
        position_quantity_source=lambda symbol: quantity[symbol],
    )
    bridge = AutonomousPaperExecutionBridge(first.trading_service, first.order_command_factory, order_book=first.order_book, position_quantity_source=lambda symbol: quantity[symbol])
    _complete_trade(first, bridge, lifecycle_id="trade-a", entry="10", exit="10.50", quantity_source=quantity)
    quantity["PMI"] = Decimal("0")
    _complete_trade(first, bridge, lifecycle_id="trade-b", entry="10", exit="9.75", quantity_source=quantity)
    first.close()

    second = create_paper_trading_command_composition(
        persistence_path=str(path),
        position_average_cost_source=lambda _symbol: Decimal("10"),
        position_quantity_source=lambda _symbol: Decimal("100"),
    )
    assert len(second.order_book.history()) == 4
    assert sorted(order.request.strategy_lifecycle_id for order in second.order_book.history()) == ["trade-a", "trade-a", "trade-b", "trade-b"]
    assert sum(len(order.fills) for order in second.order_book.history()) == 4
    assert second.order_book.open_orders() == ()
    bus = OperationsBus()
    pipeline = create_runtime_projection_pipeline(operations_bus=bus, account_id=PAPER_ACCOUNT_ID)
    for event in second.durable_store.events():
        pipeline.sink(event)
    position = pipeline.position_projection.snapshot.positions[0]
    assert Decimal(position.quantity) == Decimal("0")
    assert Decimal(position.realized_gain_loss) == Decimal("25")
    second.close()


def test_manual_order_and_legacy_payload_without_lifecycle_remain_compatible(tmp_path) -> None:
    path = tmp_path / "paper.sqlite3"
    composition = create_paper_trading_command_composition(persistence_path=str(path))
    from app.services.order_command_factory import OrderEntryCommand
    request = composition.order_command_factory.create_placement_request(OrderEntryCommand(
        symbol="ABC", side="BUY", quantity=Decimal("10"), order_type="LIMIT",
        limit_price=Decimal("5"), stop_price=None, time_in_force="DAY",
    ))
    assert request.order.strategy_lifecycle_id is None
    assert composition.trading_service.place_order(request).success
    composition.close()
    reopened = create_paper_trading_command_composition(persistence_path=str(path))
    assert reopened.order_book.history()[0].request.strategy_lifecycle_id is None
    reopened.close()


def test_replaying_same_store_twice_is_idempotent(tmp_path) -> None:
    path = tmp_path / "paper.sqlite3"
    quantity = {"PMI": Decimal("0")}
    first = create_paper_trading_command_composition(persistence_path=str(path), position_average_cost_source=lambda _symbol: Decimal("10"), position_quantity_source=lambda symbol: quantity[symbol])
    bridge = AutonomousPaperExecutionBridge(first.trading_service, first.order_command_factory, order_book=first.order_book, position_quantity_source=lambda symbol: quantity[symbol])
    _complete_trade(first, bridge, lifecycle_id="trade-a", entry="10", exit="10.50", quantity_source=quantity)
    first.close()
    snapshots = []
    for _ in range(2):
        composition = create_paper_trading_command_composition(persistence_path=str(path))
        snapshots.append((len(composition.order_book.history()), len(composition.durable_store.events())))
        composition.close()
    assert snapshots == [(2, 6), (2, 6)]


def test_cancelled_order_replays_terminal_and_not_working(tmp_path) -> None:
    path = tmp_path / "paper.sqlite3"
    first = create_paper_trading_command_composition(persistence_path=str(path))
    bridge = AutonomousPaperExecutionBridge(first.trading_service, first.order_command_factory, order_book=first.order_book)
    assert bridge.submit_entry(Signal(), 100, Decimal("50"))
    order = first.order_book.history()[0]
    from app.order_cancellation import OrderCancellationRequest
    first.trading_service.cancel_order(OrderCancellationRequest(
        request_id="cancel-1", session_id=first.session_id, account_id=first.account_id, broker_order_id=order.order_id,
        client_order_id=order.request.client_order_id,
    ))
    first.close()
    second = create_paper_trading_command_composition(persistence_path=str(path))
    assert second.order_book.history()[0].status.value == "CANCELLED"
    assert second.order_book.open_orders() == ()
    second.close()


def test_rejected_order_replays_as_terminal_event(tmp_path) -> None:
    path = tmp_path / "paper.sqlite3"
    events = []
    first = create_paper_trading_command_composition(
        persistence_path=str(path), event_sink=events.append,
        position_quantity_source=lambda _symbol: Decimal("0"),
    )
    from app.services.order_command_factory import OrderEntryCommand
    request = first.order_command_factory.create_placement_request(OrderEntryCommand(
        symbol="PMI", side="SELL", quantity=Decimal("100"), order_type="STOP",
        limit_price=None, stop_price=Decimal("9.50"), time_in_force="DAY",
    ))
    assert first.trading_service.place_order(request).success is False
    first.close()
    replay = []
    second = create_paper_trading_command_composition(persistence_path=str(path), event_sink=replay.append)
    assert any(event.event_type == "ORDER_REJECTED" for event in replay)
    assert second.order_book.open_orders() == ()
    assert second.order_book.history() == ()
    second.close()


def test_production_gui_uses_deterministic_paper_store_path() -> None:
    from app.configuration import load_configuration
    from app.gui.app import configured_paper_persistence_path

    configured = load_configuration()
    paper_path = configured_paper_persistence_path()
    assert paper_path.name == "paper-execution.sqlite3"
    assert paper_path.parent == __import__("pathlib").Path(configured.execution_database_path).parent
    assert paper_path != __import__("pathlib").Path(configured.execution_database_path)
