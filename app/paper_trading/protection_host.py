"""Headless, existing-inventory PAPER matching core; no feed or entry engine."""
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
from threading import RLock

from app.asset_modules.engine_catalog import EngineId, lifecycle_owner
from app.asset_modules.learning_outcomes import amount
from app.composition.runtime_projection_pipeline import create_runtime_projection_pipeline
from app.gui.runtime_ownership import acquire_runtime_ownership
from app.market_data.models import MarketEventType
from app.market_data.validation import validate_event
from app.operations_core import OperationsBus
from app.paper_trading.command_composition import PAPER_ACCOUNT_ID, create_paper_trading_command_composition
from app.paper_trading.order_models import OrderSide, OrderType
from app.strategies.warrior_momentum.autonomous_paper import (
    AutonomousPaperExecutionBridge, AutonomousPaperReadiness,
)
from app.trade_intelligence.knowledge.capture_profit_export import read_database, bounded_rows


def _preflight(path):
    """Bounded read-only ownership check before any recovery mutation."""
    with read_database(path) as db:
        db.execute("BEGIN")
        metadata = dict(bounded_rows(db, "SELECT key,value FROM metadata LIMIT 101", (), 100, 128_000))
        if metadata.get("environment") != "PAPER" or metadata.get("account_id") != PAPER_ACCOUNT_ID:
            raise ValueError("Dedicated Atlas PAPER account required")
        campaign = metadata.get("active_campaign_id")
        if not campaign or campaign == "no-active-paper-campaign":
            raise ValueError("Active PAPER campaign required")
        rows = bounded_rows(db, """SELECT payload FROM orders
            WHERE COALESCE(json_extract(payload,'$.paper_campaign_id'),'legacy-paper-campaign')=?
            LIMIT 5001""", (campaign,), 5000, 32_000_000)
        bounded_rows(db, "SELECT payload FROM events LIMIT 100001", (), 100000, 64_000_000)
    groups = defaultdict(list)
    fill_ids = set()
    for (raw,) in rows:
        order = json.loads(raw)
        if order['request']['side'] not in {'BUY', 'SELL'}:
            raise ValueError("Unsupported side")
        filled = Decimal(0)
        for fill in order['fills']:
            if fill['fill_id'] in fill_ids:
                raise ValueError("Duplicate durable fill")
            fill_ids.add(fill['fill_id'])
            q, price = amount(fill['quantity']), amount(fill['price'])
            if q <= 0 or price <= 0 or amount(fill['commission']) < 0:
                raise ValueError("Invalid durable fill")
            filled += q
        if filled != amount(order['filled_quantity']):
            raise ValueError("Durable fill quantity disagreement")
        groups[(order['request'].get('strategy_lifecycle_id'), order['request']['symbol'])].append(order)
    for (life, _), orders in groups.items():
        net = sum((Decimal(o['filled_quantity']) * (1 if o['request']['side'] == 'BUY' else -1)
                   for o in orders), Decimal(0))
        active = net != 0 or any(o['status'] not in {'FILLED','CANCELLED','REJECTED','EXPIRED'} for o in orders)
        if active and lifecycle_owner(life) not in {EngineId.WARRIOR, EngineId.SCALPER}:
            raise ValueError("Unclassified active inventory or orders require reconciliation")
        if active:
            if any(o['request'].get('asset_class') != 'STOCK' for o in orders):
                raise ValueError("Equity asset class required")
            permitted = ({"WARRIOR_MOMENTUM", "WARRIOR_MOMENTUM_V1"}
                         if lifecycle_owner(life) is EngineId.WARRIOR else {"QUICK_SCALPER"})
            if any(o['request'].get('metadata', {}).get('strategy_owner') not in permitted | {None, ""}
                   for o in orders):
                raise ValueError("Conflicting equity owner metadata")
        if net < 0 or net != net.to_integral_value():
            raise ValueError("Long whole-share inventory required")


class PaperProtectionHost:
    """Own a paper ledger while supplied canonical quotes reduce inventory.

    This core starts no thread, GUI, SDK, strategy or model. A future feed owner
    must supply validated events. The shared GUI lock forbids simultaneous use.
    """
    def __init__(self, path, *, environment, clock=lambda: datetime.now(timezone.utc)):
        if environment != "PAPER":
            raise ValueError("Explicit PAPER environment required")
        self.path = Path(path).resolve()
        if not self.path.is_file():
            raise ValueError("Existing PAPER ledger required")
        self._lock, self._closed = RLock(), False
        self._clock, self._started_at = clock, clock()
        if self._started_at.tzinfo is None or self._started_at.utcoffset() is None:
            raise ValueError("Aware clock required")
        self._ownership = acquire_runtime_ownership(self.path.with_name("atlas-paper-runtime.lock"))
        if self._ownership is None:
            raise RuntimeError("Atlas PAPER runtime already owned")
        self._commands = None
        self._last_quote, self._last_status, self._halted = {}, {}, set()
        try:
            _preflight(self.path)
            self._projection = create_runtime_projection_pipeline(
                operations_bus=OperationsBus(), account_id=PAPER_ACCOUNT_ID,
            )
            self._commands = create_paper_trading_command_composition(
                persistence_path=str(self.path), event_sink=self._projection.sink,
                position_quantity_source=self._quantity,
                position_average_cost_source=self._average_cost, clock=clock,
            )
            # Replay events are observational; the desktop uses this same
            # explicit order-authority seed instead of replaying old fills.
            self._projection.position_projection.reconcile_from_paper_orders(
                self._commands.order_book.history(),
            )
            inventory = defaultdict(lambda: Decimal(0))
            for order in self._commands.order_book.history():
                inventory[order.symbol] += order.filled_quantity * (1 if order.request.side is OrderSide.BUY else -1)
            if any(q != self._quantity(s) for s, q in inventory.items()):
                raise ValueError("Durable fills and position replay disagree")
            self._symbols = {o.symbol for o in self._commands.order_book.open_orders()} | {
                p.symbol for p in self._projection.position_projection.snapshot.positions if Decimal(p.quantity) > 0
            }
            if len(self._symbols) > 100:
                raise ValueError("Protection symbol limit exceeded")
            self._halted = set(self._symbols)  # Fresh status is required after takeover.
            for o in self._commands.order_book.history():
                if o.symbol not in self._symbols:
                    continue
                for fill in o.fills:
                    self._last_quote[o.symbol] = max(self._last_quote.get(o.symbol, fill.timestamp), fill.timestamp)
            for o in self._commands.order_book.open_orders():
                if o.request.side is OrderSide.BUY:
                    result = self._commands.trading_service.cancel_order(
                        self._commands.order_command_factory.create_cancellation_request(
                            o.order_id, o.request.client_order_id, source="paper-protection-host",
                        ))
                    if result is None or not result.success:
                        raise RuntimeError("Residual entry cancellation failed")
            self._bridge = AutonomousPaperExecutionBridge(
                self._commands.trading_service, self._commands.order_command_factory,
                order_book=self._commands.order_book, position_quantity_source=self._quantity,
                protection_amender=self._commands.gateway.amend_protective_stop,
            )
            if self._bridge.reconcile() is not AutonomousPaperReadiness.READY:
                raise ValueError("Ambiguous PAPER ownership")
            self._repair()
        except Exception:
            if self._commands is not None:
                self._commands.close()
            self._ownership.release()
            raise

    def _quantity(self, symbol):
        return sum((Decimal(p.quantity) for p in self._projection.position_projection.snapshot.positions
                    if p.symbol == symbol), Decimal(0))

    @property
    def protection_symbols(self):
        """Immutable current subscription inventory; never introduces symbols."""
        with self._lock:
            if self._closed:
                raise RuntimeError("Protection host closed")
            working = {o.symbol for o in self._commands.order_book.open_orders()}
            return tuple(sorted(s for s in self._symbols
                                if self._quantity(s) > 0 or s in working))

    def _average_cost(self, symbol):
        return next((Decimal(p.average_cost) for p in self._projection.position_projection.snapshot.positions
                     if p.symbol == symbol), None)

    def _repair(self):
        for symbol in self._symbols:
            quantity = self._quantity(symbol)
            if quantity <= 0:
                for order in self._commands.order_book.open_orders_for_symbol(symbol):
                    result = self._commands.trading_service.cancel_order(
                        self._commands.order_command_factory.create_cancellation_request(
                            order.order_id, order.request.client_order_id, source="paper-protection-flat-cleanup",
                        ))
                    if result is None or not result.success:
                        raise RuntimeError("Flat inventory order cleanup failed")
                continue
            identity = self._bridge._active_by_symbol.get(symbol)
            working = self._commands.order_book.open_orders_for_symbol(symbol)
            if any(o.request.side is OrderSide.SELL and o.request.order_type is OrderType.MARKET
                   and o.request.strategy_lifecycle_id == identity and o.remaining_quantity == quantity
                   for o in working):
                continue  # Preserve liquidation; never rebuild a target over it.
            stop = self._bridge._structural_stop(symbol, identity)
            if stop is None or not stop.is_finite() or stop <= 0:
                raise ValueError("Recoverable structural stop required")
            if not self._bridge.ensure_exit(symbol, int(quantity), stop, "STOP", identity).protection_active:
                raise RuntimeError("Protection could not be reconciled")

    def pause_matching(self):
        """Revoke supplied status after transport loss; preserve all orders.

        This is a local interruption, not a fabricated exchange HALT. A new
        provider status must be newer than this barrier before matching resumes.
        """
        with self._lock:
            if self._closed:
                raise RuntimeError("Protection host closed")
            at = self._clock()
            self._halted.update(self._symbols)
            for symbol in self._symbols:
                self._last_status[symbol] = max(self._last_status.get(symbol, at), at)

    def process(self, event):
        with self._lock:
            if self._closed:
                raise RuntimeError("Protection host closed")
            validate_event(event)
            now = self._clock()
            stamps = (event.timestamp, event.received_timestamp, event.dequeued_timestamp)
            if any(at is not None and not 0 <= (now-at).total_seconds() <= 5 for at in stamps):
                return "STALE_OR_FUTURE_EVENT"
            if event.timestamp < self._started_at:
                return "BEFORE_HOST_START"
            if event.symbol not in self._symbols:
                return "UNOWNED_SYMBOL"
            if event.event_type is MarketEventType.TRADING_HALT:
                if event.timestamp <= self._last_status.get(event.symbol, self._started_at):
                    return "DUPLICATE_OR_OLDER_STATUS"
                self._last_status[event.symbol] = event.timestamp
                self._halted.add(event.symbol)
                return "HALTED"
            if event.event_type is MarketEventType.RESUME:
                if event.timestamp <= self._last_status.get(event.symbol, self._started_at):
                    return "DUPLICATE_OR_OLDER_STATUS"
                self._last_status[event.symbol] = event.timestamp
                self._halted.discard(event.symbol)
                return "RESUMED"
            if event.event_type is not MarketEventType.QUOTE:
                return "NOT_A_QUOTE"
            if event.symbol in self._halted:
                return "HALTED"
            if event.timestamp <= self._last_quote.get(event.symbol, self._started_at):
                return "DUPLICATE_OR_OLDER_QUOTE"
            if event.timestamp <= self._last_status.get(event.symbol, self._started_at):
                return "QUOTE_PRECEDES_STATUS"
            self._last_quote[event.symbol] = event.timestamp
            self._repair()
            self._commands.gateway.process_market_event(event)
            self._repair()
            return "PROCESSED"

    def snapshot(self):
        with self._lock:
            return dict(scope="PAPER_PROTECTION_CORE_NO_FEED_OR_ENTRY_ENGINE",
                        state="CLOSED" if self._closed else "PROTECTION_ONLY",
                        positions=sum(self._quantity(s) > 0 for s in self._symbols),
                        matching_paused_symbols=len(self._halted),
                        entries_enabled=False, live_feed_validated=False, network_requests=0)

    def close(self):
        with self._lock:
            if not self._closed:
                self._commands.close()
                self._closed = True
                self._ownership.release()
