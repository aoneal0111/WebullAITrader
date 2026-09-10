from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

from app.market_data.models import MarketEvent, MarketEventType, QuotePayload
from app.paper_gateway import PaperOrderGateway
from app.paper_trading.order_book import PaperOrderBook
from app.services import OrderCommandFactory, OrderEntryCommand
from app.strategies.warrior_momentum.forward_runtime import (
    WarriorForwardCaptureService,
)
from app.webull.websocket_client import OfficialSdkStreamBackend


NOW = datetime(2026, 9, 10, 15, 0, tzinfo=UTC)


def _placement(symbol: str) -> object:
    factory = OrderCommandFactory(
        session_id_provider=lambda: "session",
        account_id_provider=lambda: "paper-account",
        request_id_factory=lambda: f"request-{symbol}",
        client_order_id_factory=lambda: f"client-{symbol}",
    )
    return factory.create_placement_request(OrderEntryCommand(
        symbol=symbol, side="BUY", quantity=Decimal("10"),
        order_type="LIMIT", limit_price=Decimal("10"), stop_price=None,
        time_in_force="DAY",
    ))


def _quote(symbol: str, timestamp: datetime, sequence: int) -> MarketEvent:
    return MarketEvent(
        sequence, timestamp, symbol, "paper", MarketEventType.QUOTE,
        QuotePayload(Decimal("9"), Decimal("10"), Decimal("100"), Decimal("100")),
    )


def test_stale_paper_fill_is_order_local_and_next_symbols_continue(caplog):
    book = PaperOrderBook()
    gateway = PaperOrderGateway(book, clock=lambda: NOW)
    first = gateway.place_order(_placement("AAPL"))
    second = gateway.place_order(_placement("MSFT"))

    assert gateway.process_market_event(_quote("AAPL", NOW - timedelta(seconds=1), 1)) == ()
    assert book.get(first.broker_order_id).filled_quantity == 0
    reports = gateway.process_market_event(_quote("MSFT", NOW, 2))
    assert reports and reports[0].order.filled_quantity == Decimal("10")
    assert "STALE_ORDER_MARKET_EVENT" in caplog.text


def test_actual_position_rebases_warrior_milestones(tmp_path):
    position = {"XYZ": Decimal("2028")}
    writer_store = __import__(
        "app.strategies.warrior_momentum", fromlist=["ForwardCaptureStore"]
    )
    store = writer_store.ForwardCaptureStore(tmp_path / "capture.sqlite3")
    writer = writer_store.ForwardCaptureWriter(store, flush_interval_seconds=0.01)
    service = WarriorForwardCaptureService(
        store, writer, paper_entry_submitter=lambda *_: True,
        paper_position_quantity_source=lambda symbol: position[symbol],
        paper_exit_submitter=lambda *_: True,
    )
    try:
        from tests.warrior_momentum.test_forward_capture import account, point
        _, signal = service.observe(point(), account=account())
        state = service._paper[signal.symbol]
        state.initial_quantity = 2696
        state.remaining = 2696
        state.managed_quantity = 0
        service._advance_authoritative_paper(
            state,
            __import__(
                "app.strategies.warrior_momentum", fromlist=["MinuteBar"]
            ).MinuteBar(
                "XYZ", NOW, signal.entry_trigger, signal.entry_trigger,
                signal.entry_trigger, signal.entry_trigger, Decimal("100"),
            ),
            NOW,
        )
        assert (state.first_quantity, state.second_quantity) == (1014, 507)
        assert state.managed_quantity == 2028
        assert state.first_quantity + state.second_quantity + (
            state.managed_quantity - state.first_quantity - state.second_quantity
        ) == 2028
    finally:
        writer.close()


class _Sdk:
    def __init__(self):
        self._client_id = "stream-session"
        self._transport = "test"
        self.on_quotes_message = None
        self.on_connect_success = None
        self.on_disconnect = None

    def connect(self):
        self.on_connect_success(self, object(), self._client_id)

    def disconnect(self):
        return None

    def subscribe(self, *_):
        return None


def test_callback_ingestion_halts_and_reconnect_reopens_one_fifo():
    sdk = _Sdk()
    backend = OfficialSdkStreamBackend(
        sdk, registration_grace_seconds=0,
        sdk_client_factory=_Sdk,
    )
    backend.connect()
    backend._on_quotes_message(sdk, "quotes", SimpleNamespace(value=1))
    assert backend.queue_depth() == 1
    backend.halt_callback_ingestion()
    backend._on_quotes_message(sdk, "quotes", SimpleNamespace(value=2))
    assert backend.queue_depth() == 0
    backend.connect()
    backend._on_quotes_message(backend.client, "quotes", SimpleNamespace(value=3))
    assert backend.queue_depth() == 1
