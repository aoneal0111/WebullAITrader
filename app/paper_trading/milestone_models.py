from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Mapping

from app.committee.models import JSONValue, freeze_json_mapping, thaw_json_value
from app.execution_planner import ExecutionPlanResult
from app.order_placement import OrderSide, OrderType
from app.paper_trading.exceptions import PaperTradingValidationError


ZERO = Decimal("0")


def _text(value, name):
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise PaperTradingValidationError(f"{name} must be a non-empty stripped string")
    return value


def _decimal(value, name, *, positive=False, nonnegative=False):
    if isinstance(value, bool) or not isinstance(value, (Decimal, str, int)):
        raise PaperTradingValidationError(f"{name} must be Decimal-compatible")
    try:
        result = Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise PaperTradingValidationError(f"{name} must be finite") from exc
    if not result.is_finite() or (positive and result <= 0) or (nonnegative and result < 0):
        qualifier = "positive and " if positive else "non-negative and " if nonnegative else ""
        raise PaperTradingValidationError(f"{name} must be {qualifier}finite")
    return result


def _timestamp(value, name):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise PaperTradingValidationError(f"{name} must be timezone-aware")
    return value


class PaperOrderStatus(StrEnum):
    PENDING = "PENDING"
    FILLED = "FILLED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


class PaperExecutionOutcome(StrEnum):
    EXECUTED = "EXECUTED"
    PARTIALLY_EXECUTED = "PARTIALLY_EXECUTED"
    REJECTED = "REJECTED"
    DISABLED = "DISABLED"
    NO_ACTION = "NO_ACTION"


@dataclass(frozen=True, slots=True)
class PaperPosition:
    account_id: str
    symbol: str
    quantity: Decimal
    average_cost: Decimal
    market_price: Decimal
    market_value: Decimal
    unrealized_profit_loss: Decimal
    realized_profit_loss: Decimal = ZERO
    metadata: Mapping[str, JSONValue] = field(default_factory=dict)

    def __post_init__(self):
        object.__setattr__(self, "account_id", _text(self.account_id, "account_id"))
        object.__setattr__(self, "symbol", _text(self.symbol, "symbol").upper())
        for name in ("quantity", "average_cost", "market_price", "market_value"):
            object.__setattr__(self, name, _decimal(getattr(self, name), name, positive=True))
        object.__setattr__(self, "unrealized_profit_loss", _decimal(self.unrealized_profit_loss, "unrealized_profit_loss"))
        object.__setattr__(self, "realized_profit_loss", _decimal(self.realized_profit_loss, "realized_profit_loss"))
        if self.market_value != self.quantity * self.market_price:
            raise PaperTradingValidationError("position market_value must equal quantity times market_price")
        if self.unrealized_profit_loss != (self.market_price - self.average_cost) * self.quantity:
            raise PaperTradingValidationError("position unrealized profit and loss is inconsistent")
        object.__setattr__(self, "metadata", freeze_json_mapping("metadata", self.metadata))

    def to_dict(self):
        return {"account_id": self.account_id, "symbol": self.symbol, "quantity": str(self.quantity),
                "average_cost": str(self.average_cost), "market_price": str(self.market_price),
                "market_value": str(self.market_value), "unrealized_profit_loss": str(self.unrealized_profit_loss),
                "realized_profit_loss": str(self.realized_profit_loss), "metadata": thaw_json_value(self.metadata)}

    @classmethod
    def from_dict(cls, value):
        try: return cls(**dict(value))
        except PaperTradingValidationError: raise
        except (TypeError, ValueError, KeyError) as exc: raise PaperTradingValidationError("invalid paper position") from exc


@dataclass(frozen=True, slots=True)
class PaperOrder:
    order_id: str
    request_id: str
    account_id: str
    symbol: str
    side: OrderSide
    order_type: OrderType
    requested_quantity: Decimal
    filled_quantity: Decimal
    status: PaperOrderStatus
    execution_price: Decimal | None
    fees: Decimal
    created_at: datetime
    rejection_reason: str | None = None
    metadata: Mapping[str, JSONValue] = field(default_factory=dict)

    def __post_init__(self):
        for name in ("order_id", "request_id", "account_id"): object.__setattr__(self, name, _text(getattr(self, name), name))
        object.__setattr__(self, "symbol", _text(self.symbol, "symbol").upper())
        if not isinstance(self.side, OrderSide) or not isinstance(self.order_type, OrderType) or not isinstance(self.status, PaperOrderStatus):
            raise PaperTradingValidationError("order enums are invalid")
        object.__setattr__(self, "requested_quantity", _decimal(self.requested_quantity, "requested_quantity", positive=True))
        object.__setattr__(self, "filled_quantity", _decimal(self.filled_quantity, "filled_quantity", nonnegative=True))
        object.__setattr__(self, "fees", _decimal(self.fees, "fees", nonnegative=True))
        if self.filled_quantity > self.requested_quantity: raise PaperTradingValidationError("filled quantity exceeds requested quantity")
        if self.execution_price is not None: object.__setattr__(self, "execution_price", _decimal(self.execution_price, "execution_price", positive=True))
        object.__setattr__(self, "created_at", _timestamp(self.created_at, "created_at"))
        if self.rejection_reason is not None: object.__setattr__(self, "rejection_reason", _text(self.rejection_reason, "rejection_reason"))
        object.__setattr__(self, "metadata", freeze_json_mapping("metadata", self.metadata))

    def to_dict(self):
        return {"order_id": self.order_id, "request_id": self.request_id, "account_id": self.account_id, "symbol": self.symbol,
                "side": self.side.value, "order_type": self.order_type.value, "requested_quantity": str(self.requested_quantity),
                "filled_quantity": str(self.filled_quantity), "status": self.status.value,
                "execution_price": str(self.execution_price) if self.execution_price is not None else None, "fees": str(self.fees),
                "created_at": self.created_at.isoformat(), "rejection_reason": self.rejection_reason,
                "metadata": thaw_json_value(self.metadata)}

    @classmethod
    def from_dict(cls, value):
        try:
            data = dict(value); data["side"] = OrderSide(data["side"]); data["order_type"] = OrderType(data["order_type"])
            data["status"] = PaperOrderStatus(data["status"]); data["created_at"] = datetime.fromisoformat(data["created_at"]); return cls(**data)
        except PaperTradingValidationError: raise
        except (TypeError, ValueError, KeyError) as exc: raise PaperTradingValidationError("invalid paper order") from exc


@dataclass(frozen=True, slots=True)
class PaperFill:
    fill_id: str
    order_id: str
    request_id: str
    account_id: str
    symbol: str
    side: OrderSide
    quantity: Decimal
    price: Decimal
    gross_amount: Decimal
    fees: Decimal
    executed_at: datetime
    metadata: Mapping[str, JSONValue] = field(default_factory=dict)

    def __post_init__(self):
        for name in ("fill_id", "order_id", "request_id", "account_id"): object.__setattr__(self, name, _text(getattr(self, name), name))
        object.__setattr__(self, "symbol", _text(self.symbol, "symbol").upper())
        if not isinstance(self.side, OrderSide): raise PaperTradingValidationError("side must be OrderSide")
        for name in ("quantity", "price", "gross_amount"): object.__setattr__(self, name, _decimal(getattr(self, name), name, positive=True))
        object.__setattr__(self, "fees", _decimal(self.fees, "fees", nonnegative=True))
        if self.gross_amount != self.quantity * self.price: raise PaperTradingValidationError("gross_amount must equal quantity times price")
        object.__setattr__(self, "executed_at", _timestamp(self.executed_at, "executed_at"))
        object.__setattr__(self, "metadata", freeze_json_mapping("metadata", self.metadata))

    def to_dict(self):
        return {"fill_id": self.fill_id, "order_id": self.order_id, "request_id": self.request_id, "account_id": self.account_id,
                "symbol": self.symbol, "side": self.side.value, "quantity": str(self.quantity), "price": str(self.price),
                "gross_amount": str(self.gross_amount), "fees": str(self.fees), "executed_at": self.executed_at.isoformat(),
                "metadata": thaw_json_value(self.metadata)}

    @classmethod
    def from_dict(cls, value):
        try:
            data = dict(value); data["side"] = OrderSide(data["side"]); data["executed_at"] = datetime.fromisoformat(data["executed_at"]); return cls(**data)
        except PaperTradingValidationError: raise
        except (TypeError, ValueError, KeyError) as exc: raise PaperTradingValidationError("invalid paper fill") from exc


@dataclass(frozen=True, slots=True)
class PaperTradingAccount:
    account_id: str
    cash: Decimal
    buying_power: Decimal
    positions: tuple[PaperPosition, ...] = ()
    orders: tuple[PaperOrder, ...] = ()
    fills: tuple[PaperFill, ...] = ()
    realized_profit_loss: Decimal = ZERO
    unrealized_profit_loss: Decimal = ZERO
    total_market_value: Decimal = ZERO
    total_equity: Decimal = ZERO
    metadata: Mapping[str, JSONValue] = field(default_factory=dict)

    def __post_init__(self):
        object.__setattr__(self, "account_id", _text(self.account_id, "account_id"))
        for name in ("cash", "buying_power", "total_market_value", "total_equity"):
            object.__setattr__(self, name, _decimal(getattr(self, name), name, nonnegative=True))
        for name in ("realized_profit_loss", "unrealized_profit_loss"):
            object.__setattr__(self, name, _decimal(getattr(self, name), name))
        if not isinstance(self.positions, tuple) or any(not isinstance(x, PaperPosition) for x in self.positions): raise PaperTradingValidationError("positions must be an immutable tuple")
        if not isinstance(self.orders, tuple) or any(not isinstance(x, PaperOrder) for x in self.orders): raise PaperTradingValidationError("orders must be an immutable tuple")
        if not isinstance(self.fills, tuple) or any(not isinstance(x, PaperFill) for x in self.fills): raise PaperTradingValidationError("fills must be an immutable tuple")
        if any(x.account_id != self.account_id for x in self.positions + self.orders + self.fills): raise PaperTradingValidationError("account member identity mismatch")
        if len({x.symbol for x in self.positions}) != len(self.positions): raise PaperTradingValidationError("positions must have unique symbols")
        if self.buying_power != self.cash: raise PaperTradingValidationError("cash account buying_power must equal cash")
        if self.total_market_value != sum((x.market_value for x in self.positions), ZERO): raise PaperTradingValidationError("total_market_value is inconsistent")
        if self.unrealized_profit_loss != sum((x.unrealized_profit_loss for x in self.positions), ZERO): raise PaperTradingValidationError("unrealized profit and loss is inconsistent")
        if self.total_equity != self.cash + self.total_market_value: raise PaperTradingValidationError("total_equity must equal cash plus market value")
        object.__setattr__(self, "metadata", freeze_json_mapping("metadata", self.metadata))

    def to_dict(self):
        return {"account_id": self.account_id, "cash": str(self.cash), "buying_power": str(self.buying_power),
                "positions": [x.to_dict() for x in self.positions], "orders": [x.to_dict() for x in self.orders],
                "fills": [x.to_dict() for x in self.fills], "realized_profit_loss": str(self.realized_profit_loss),
                "unrealized_profit_loss": str(self.unrealized_profit_loss), "total_market_value": str(self.total_market_value),
                "total_equity": str(self.total_equity), "metadata": thaw_json_value(self.metadata)}

    @classmethod
    def from_dict(cls, value):
        try:
            data = dict(value); data["positions"] = tuple(PaperPosition.from_dict(x) for x in data.get("positions", ()))
            data["orders"] = tuple(PaperOrder.from_dict(x) for x in data.get("orders", ())); data["fills"] = tuple(PaperFill.from_dict(x) for x in data.get("fills", ())); return cls(**data)
        except PaperTradingValidationError: raise
        except (TypeError, ValueError, KeyError) as exc: raise PaperTradingValidationError("invalid paper trading account") from exc


@dataclass(frozen=True, slots=True)
class PaperPortfolioSnapshot:
    account_id: str
    cash: Decimal
    buying_power: Decimal
    positions: tuple[PaperPosition, ...]
    realized_profit_loss: Decimal
    unrealized_profit_loss: Decimal
    total_market_value: Decimal
    total_equity: Decimal
    as_of: datetime

    def __post_init__(self):
        account = PaperTradingAccount(self.account_id, self.cash, self.buying_power, self.positions, (), (), self.realized_profit_loss,
                                      self.unrealized_profit_loss, self.total_market_value, self.total_equity)
        object.__setattr__(self, "account_id", account.account_id); object.__setattr__(self, "cash", account.cash)
        object.__setattr__(self, "buying_power", account.buying_power)
        object.__setattr__(self, "realized_profit_loss", account.realized_profit_loss)
        object.__setattr__(self, "unrealized_profit_loss", account.unrealized_profit_loss)
        object.__setattr__(self, "total_market_value", account.total_market_value)
        object.__setattr__(self, "total_equity", account.total_equity)
        object.__setattr__(self, "as_of", _timestamp(self.as_of, "as_of"))

    def to_dict(self):
        return {"account_id": self.account_id, "cash": str(self.cash), "buying_power": str(self.buying_power),
                "positions": [x.to_dict() for x in self.positions], "realized_profit_loss": str(self.realized_profit_loss),
                "unrealized_profit_loss": str(self.unrealized_profit_loss), "total_market_value": str(self.total_market_value),
                "total_equity": str(self.total_equity), "as_of": self.as_of.isoformat()}

    @classmethod
    def from_dict(cls, value):
        try:
            data = dict(value); data["positions"] = tuple(PaperPosition.from_dict(x) for x in data["positions"]); data["as_of"] = datetime.fromisoformat(data["as_of"]); return cls(**data)
        except PaperTradingValidationError: raise
        except (TypeError, ValueError, KeyError) as exc: raise PaperTradingValidationError("invalid paper portfolio snapshot") from exc


@dataclass(frozen=True, slots=True)
class PaperExecutionRequest:
    request_id: str
    account_id: str
    execution_plan_result: ExecutionPlanResult
    account: PaperTradingAccount
    market_price: Decimal
    execution_timestamp: datetime
    metadata: Mapping[str, JSONValue] = field(default_factory=dict)

    def __post_init__(self):
        object.__setattr__(self, "request_id", _text(self.request_id, "request_id")); object.__setattr__(self, "account_id", _text(self.account_id, "account_id"))
        if not isinstance(self.execution_plan_result, ExecutionPlanResult): raise PaperTradingValidationError("execution_plan_result must be ExecutionPlanResult")
        if not isinstance(self.account, PaperTradingAccount): raise PaperTradingValidationError("account must be PaperTradingAccount")
        object.__setattr__(self, "market_price", _decimal(self.market_price, "market_price", positive=True))
        object.__setattr__(self, "execution_timestamp", _timestamp(self.execution_timestamp, "execution_timestamp"))
        object.__setattr__(self, "metadata", freeze_json_mapping("metadata", self.metadata))

    def to_dict(self):
        return {"request_id": self.request_id, "account_id": self.account_id, "execution_plan_result": self.execution_plan_result.to_dict(),
                "account": self.account.to_dict(), "market_price": str(self.market_price), "execution_timestamp": self.execution_timestamp.isoformat(),
                "metadata": thaw_json_value(self.metadata)}

    @classmethod
    def from_dict(cls, value):
        try:
            data = dict(value); data["execution_plan_result"] = ExecutionPlanResult.from_dict(data["execution_plan_result"])
            data["account"] = PaperTradingAccount.from_dict(data["account"]); data["execution_timestamp"] = datetime.fromisoformat(data["execution_timestamp"]); return cls(**data)
        except PaperTradingValidationError: raise
        except (TypeError, ValueError, KeyError) as exc: raise PaperTradingValidationError("invalid paper execution request") from exc


@dataclass(frozen=True, slots=True)
class PaperTradingCriteriaResult:
    name: str
    passed: bool
    detail: str

    def __post_init__(self):
        object.__setattr__(self, "name", _text(self.name, "criteria name")); object.__setattr__(self, "detail", _text(self.detail, "criteria detail"))
        if not isinstance(self.passed, bool): raise PaperTradingValidationError("criteria passed must be boolean")

    def to_dict(self): return {"name": self.name, "passed": self.passed, "detail": self.detail}
    @classmethod
    def from_dict(cls, value): return cls(**dict(value))


@dataclass(frozen=True, slots=True)
class PaperExecutionResult:
    request_id: str
    account_id: str
    outcome: PaperExecutionOutcome
    account: PaperTradingAccount
    order: PaperOrder | None
    fill: PaperFill | None
    portfolio: PaperPortfolioSnapshot
    criteria_results: tuple[PaperTradingCriteriaResult, ...]
    policy_version: str
    metadata: Mapping[str, JSONValue] = field(default_factory=dict)

    def __post_init__(self):
        object.__setattr__(self, "request_id", _text(self.request_id, "request_id")); object.__setattr__(self, "account_id", _text(self.account_id, "account_id"))
        object.__setattr__(self, "policy_version", _text(self.policy_version, "policy_version"))
        if not isinstance(self.outcome, PaperExecutionOutcome) or not isinstance(self.account, PaperTradingAccount) or not isinstance(self.portfolio, PaperPortfolioSnapshot): raise PaperTradingValidationError("result members are invalid")
        if self.account_id != self.account.account_id or self.account_id != self.portfolio.account_id: raise PaperTradingValidationError("result account identity mismatch")
        if self.order is not None and not isinstance(self.order, PaperOrder): raise PaperTradingValidationError("order must be PaperOrder")
        if self.fill is not None and not isinstance(self.fill, PaperFill): raise PaperTradingValidationError("fill must be PaperFill")
        if not isinstance(self.criteria_results, tuple) or any(not isinstance(x, PaperTradingCriteriaResult) for x in self.criteria_results): raise PaperTradingValidationError("criteria_results must be immutable")
        object.__setattr__(self, "metadata", freeze_json_mapping("metadata", self.metadata))

    def to_dict(self):
        return {"request_id": self.request_id, "account_id": self.account_id, "outcome": self.outcome.value, "account": self.account.to_dict(),
                "order": self.order.to_dict() if self.order else None, "fill": self.fill.to_dict() if self.fill else None,
                "portfolio": self.portfolio.to_dict(), "criteria_results": [x.to_dict() for x in self.criteria_results],
                "policy_version": self.policy_version, "metadata": thaw_json_value(self.metadata)}

    @classmethod
    def from_dict(cls, value):
        try:
            data = dict(value); data["outcome"] = PaperExecutionOutcome(data["outcome"]); data["account"] = PaperTradingAccount.from_dict(data["account"])
            data["order"] = PaperOrder.from_dict(data["order"]) if data.get("order") else None; data["fill"] = PaperFill.from_dict(data["fill"]) if data.get("fill") else None
            data["portfolio"] = PaperPortfolioSnapshot.from_dict(data["portfolio"]); data["criteria_results"] = tuple(PaperTradingCriteriaResult.from_dict(x) for x in data["criteria_results"]); return cls(**data)
        except PaperTradingValidationError: raise
        except (TypeError, ValueError, KeyError) as exc: raise PaperTradingValidationError("invalid paper execution result") from exc
