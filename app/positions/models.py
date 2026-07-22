from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Mapping
from app.committee.models import JSONValue, freeze_json_mapping, thaw_json_value
from app.positions.exceptions import PositionsValidationError


def _text(value, name):
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise PositionsValidationError(f"{name} must be a non-empty stripped string")
    return value


def _decimal(value, name):
    if isinstance(value, bool) or not isinstance(value, (Decimal, str, int)):
        raise PositionsValidationError(f"{name} must be Decimal-compatible")
    try: result = Decimal(value)
    except (InvalidOperation, ValueError) as exc: raise PositionsValidationError(f"{name} must be a finite Decimal") from exc
    if not result.is_finite(): raise PositionsValidationError(f"{name} must be a finite Decimal")
    return result


class PositionsDecision(StrEnum):
    DISABLED="DISABLED"
    SESSION_INVALID="SESSION_INVALID"
    GATEWAY_FAILURE="GATEWAY_FAILURE"
    SUCCESS="SUCCESS"


@dataclass(frozen=True, slots=True)
class PositionModel:
    account_id: str
    symbol: str
    asset_type: str
    quantity: Decimal
    average_cost: Decimal
    market_value: Decimal
    unrealized_gain_loss: Decimal
    realized_gain_loss: Decimal | None
    currency: str
    metadata: Mapping[str, JSONValue] = field(default_factory=dict)

    def __post_init__(self):
        for name in ("account_id", "asset_type", "currency"):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        object.__setattr__(self, "symbol", _text(self.symbol, "symbol").upper())
        object.__setattr__(self, "currency", self.currency.upper())
        quantity = _decimal(self.quantity, "quantity")
        if quantity == 0: raise PositionsValidationError("quantity must be non-zero")
        average_cost = _decimal(self.average_cost, "average_cost")
        if average_cost < 0: raise PositionsValidationError("average_cost cannot be negative")
        object.__setattr__(self, "quantity", quantity)
        object.__setattr__(self, "average_cost", average_cost)
        object.__setattr__(self, "market_value", _decimal(self.market_value, "market_value"))
        object.__setattr__(self, "unrealized_gain_loss", _decimal(self.unrealized_gain_loss, "unrealized_gain_loss"))
        if self.realized_gain_loss is not None:
            object.__setattr__(self, "realized_gain_loss", _decimal(self.realized_gain_loss, "realized_gain_loss"))
        object.__setattr__(self, "metadata", freeze_json_mapping("metadata", self.metadata))

    def to_dict(self):
        return {"account_id":self.account_id,"symbol":self.symbol,"asset_type":self.asset_type,
                "quantity":str(self.quantity),"average_cost":str(self.average_cost),"market_value":str(self.market_value),
                "unrealized_gain_loss":str(self.unrealized_gain_loss),
                "realized_gain_loss":str(self.realized_gain_loss) if self.realized_gain_loss is not None else None,
                "currency":self.currency,"metadata":thaw_json_value(self.metadata)}

    @classmethod
    def from_dict(cls, value):
        try: return cls(**dict(value))
        except PositionsValidationError: raise
        except (TypeError, ValueError, KeyError) as exc: raise PositionsValidationError("invalid position") from exc


@dataclass(frozen=True, slots=True)
class PositionsRequest:
    request_id: str
    session_id: str
    metadata: Mapping[str, JSONValue] = field(default_factory=dict)
    def __post_init__(self):
        object.__setattr__(self,"request_id",_text(self.request_id,"request_id")); object.__setattr__(self,"session_id",_text(self.session_id,"session_id")); object.__setattr__(self,"metadata",freeze_json_mapping("metadata",self.metadata))
    def to_dict(self): return {"request_id":self.request_id,"session_id":self.session_id,"metadata":thaw_json_value(self.metadata)}
    @classmethod
    def from_dict(cls,value):
        try:return cls(**dict(value))
        except PositionsValidationError:raise
        except (TypeError,ValueError,KeyError) as exc:raise PositionsValidationError("invalid positions request") from exc


@dataclass(frozen=True, slots=True)
class PositionsCriteriaResult:
    name:str
    passed:bool
    detail:str
    metadata:Mapping[str,JSONValue]=field(default_factory=dict)
    def __post_init__(self):
        object.__setattr__(self,"name",_text(self.name,"criteria name"));object.__setattr__(self,"detail",_text(self.detail,"criteria detail"))
        if not isinstance(self.passed,bool):raise PositionsValidationError("criteria passed must be boolean")
        object.__setattr__(self,"metadata",freeze_json_mapping("metadata",self.metadata))
    def to_dict(self):return {"name":self.name,"passed":self.passed,"detail":self.detail,"metadata":thaw_json_value(self.metadata)}
    @classmethod
    def from_dict(cls,value):return cls(**dict(value))


@dataclass(frozen=True, slots=True)
class PositionsResult:
    request_id:str
    session_id:str
    decision:PositionsDecision
    positions:tuple[PositionModel,...]
    criteria_results:tuple[PositionsCriteriaResult,...]
    metadata:Mapping[str,JSONValue]=field(default_factory=dict)
    def __post_init__(self):
        object.__setattr__(self,"request_id",_text(self.request_id,"request_id"));object.__setattr__(self,"session_id",_text(self.session_id,"session_id"))
        if not isinstance(self.decision,PositionsDecision):raise PositionsValidationError("decision must be PositionsDecision")
        if not isinstance(self.positions,tuple) or any(not isinstance(x,PositionModel) for x in self.positions):raise PositionsValidationError("positions must be an immutable position tuple")
        if self.decision is not PositionsDecision.SUCCESS and self.positions:raise PositionsValidationError("failure result cannot expose positions")
        if not isinstance(self.criteria_results,tuple) or any(not isinstance(x,PositionsCriteriaResult) for x in self.criteria_results):raise PositionsValidationError("criteria_results must be immutable criteria tuple")
        object.__setattr__(self,"metadata",freeze_json_mapping("metadata",self.metadata))
    @property
    def success(self):return self.decision is PositionsDecision.SUCCESS
    def to_dict(self):return {"request_id":self.request_id,"session_id":self.session_id,"decision":self.decision.value,"positions":[x.to_dict() for x in self.positions],"criteria_results":[x.to_dict() for x in self.criteria_results],"metadata":thaw_json_value(self.metadata)}
    @classmethod
    def from_dict(cls,value):
        try:
            data=dict(value);data["decision"]=PositionsDecision(data["decision"]);data["positions"]=tuple(PositionModel.from_dict(x) for x in data["positions"]);data["criteria_results"]=tuple(PositionsCriteriaResult.from_dict(x) for x in data["criteria_results"]);return cls(**data)
        except PositionsValidationError:raise
        except (TypeError,ValueError,KeyError) as exc:raise PositionsValidationError("invalid positions result") from exc
