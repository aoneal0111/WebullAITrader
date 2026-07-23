from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Mapping

from app.committee.models import JSONValue, freeze_json_mapping, thaw_json_value
from app.account_information.exceptions import AccountInformationValidationError


def _text(value, name):
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise AccountInformationValidationError(f"{name} must be a non-empty stripped string")
    return value


def _money(value, name):
    if isinstance(value, bool) or not isinstance(value, (Decimal, str, int)):
        raise AccountInformationValidationError(f"{name} must be Decimal-compatible")
    try:
        result = Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise AccountInformationValidationError(f"{name} must be a finite Decimal") from exc
    if not result.is_finite():
        raise AccountInformationValidationError(f"{name} must be a finite Decimal")
    return result


class AccountInformationDecision(StrEnum):
    DISABLED = "DISABLED"
    SESSION_INVALID = "SESSION_INVALID"
    GATEWAY_FAILURE = "GATEWAY_FAILURE"
    SUCCESS = "SUCCESS"


@dataclass(frozen=True, slots=True)
class AccountInformationRequest:
    request_id: str
    session_id: str
    metadata: Mapping[str, JSONValue] = field(default_factory=dict)

    def __post_init__(self):
        object.__setattr__(self, "request_id", _text(self.request_id, "request_id"))
        object.__setattr__(self, "session_id", _text(self.session_id, "session_id"))
        object.__setattr__(self, "metadata", freeze_json_mapping("metadata", self.metadata))

    def to_dict(self):
        return {"request_id": self.request_id, "session_id": self.session_id,
                "metadata": thaw_json_value(self.metadata)}

    @classmethod
    def from_dict(cls, value):
        try:
            return cls(**dict(value))
        except AccountInformationValidationError:
            raise
        except (TypeError, ValueError, KeyError) as exc:
            raise AccountInformationValidationError("invalid account information request") from exc


@dataclass(frozen=True, slots=True)
class BrokerNeutralAccountInformation:
    account_id: str
    account_type: str
    account_status: str
    buying_power: Decimal
    cash_balance: Decimal
    equity: Decimal
    currency: str
    metadata: Mapping[str, JSONValue] = field(default_factory=dict)

    def __post_init__(self):
        for name in ("account_id", "account_type", "account_status", "currency"):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        object.__setattr__(self, "currency", self.currency.upper())
        for name in ("buying_power", "cash_balance", "equity"):
            value = _money(getattr(self, name), name)
            if value < 0:
                raise AccountInformationValidationError(f"{name} cannot be negative")
            object.__setattr__(self, name, value)
        object.__setattr__(self, "metadata", freeze_json_mapping("metadata", self.metadata))

    def to_dict(self):
        return {"account_id": self.account_id, "account_type": self.account_type,
                "account_status": self.account_status, "buying_power": str(self.buying_power),
                "cash_balance": str(self.cash_balance), "equity": str(self.equity),
                "currency": self.currency, "metadata": thaw_json_value(self.metadata)}

    @classmethod
    def from_dict(cls, value):
        try:
            return cls(**dict(value))
        except AccountInformationValidationError:
            raise
        except (TypeError, ValueError, KeyError) as exc:
            raise AccountInformationValidationError("invalid broker-neutral account information") from exc


@dataclass(frozen=True, slots=True)
class AccountInformationCriteriaResult:
    name: str
    passed: bool
    detail: str
    metadata: Mapping[str, JSONValue] = field(default_factory=dict)

    def __post_init__(self):
        object.__setattr__(self, "name", _text(self.name, "criteria name"))
        object.__setattr__(self, "detail", _text(self.detail, "criteria detail"))
        if not isinstance(self.passed, bool):
            raise AccountInformationValidationError("criteria passed must be boolean")
        object.__setattr__(self, "metadata", freeze_json_mapping("metadata", self.metadata))

    def to_dict(self):
        return {"name": self.name, "passed": self.passed, "detail": self.detail,
                "metadata": thaw_json_value(self.metadata)}

    @classmethod
    def from_dict(cls, value):
        return cls(**dict(value))


@dataclass(frozen=True, slots=True)
class AccountInformationResult:
    request_id: str
    session_id: str
    decision: AccountInformationDecision
    account_id: str
    account_type: str
    account_status: str
    buying_power: Decimal
    cash_balance: Decimal
    equity: Decimal
    currency: str
    criteria_results: tuple[AccountInformationCriteriaResult, ...]
    metadata: Mapping[str, JSONValue] = field(default_factory=dict)

    def __post_init__(self):
        object.__setattr__(self, "request_id", _text(self.request_id, "request_id"))
        object.__setattr__(self, "session_id", _text(self.session_id, "session_id"))
        if not isinstance(self.decision, AccountInformationDecision):
            raise AccountInformationValidationError("decision must be AccountInformationDecision")
        for name in ("account_id", "account_type", "account_status", "currency"):
            value = getattr(self, name)
            if self.decision is AccountInformationDecision.SUCCESS:
                object.__setattr__(self, name, _text(value, name))
            elif not isinstance(value, str) or value:
                raise AccountInformationValidationError(f"failed result {name} must be empty")
        for name in ("buying_power", "cash_balance", "equity"):
            value = _money(getattr(self, name), name)
            if value < 0 or (self.decision is not AccountInformationDecision.SUCCESS and value != 0):
                raise AccountInformationValidationError(f"invalid {name} for result decision")
            object.__setattr__(self, name, value)
        if not isinstance(self.criteria_results, tuple) or any(not isinstance(x, AccountInformationCriteriaResult) for x in self.criteria_results):
            raise AccountInformationValidationError("criteria_results must be an immutable criteria tuple")
        object.__setattr__(self, "metadata", freeze_json_mapping("metadata", self.metadata))

    @property
    def success(self):
        return self.decision is AccountInformationDecision.SUCCESS

    def to_dict(self):
        return {"request_id": self.request_id, "session_id": self.session_id,
                "decision": self.decision.value, "account_id": self.account_id,
                "account_type": self.account_type, "account_status": self.account_status,
                "buying_power": str(self.buying_power), "cash_balance": str(self.cash_balance),
                "equity": str(self.equity), "currency": self.currency,
                "criteria_results": [x.to_dict() for x in self.criteria_results],
                "metadata": thaw_json_value(self.metadata)}

    @classmethod
    def from_dict(cls, value):
        try:
            data = dict(value)
            data["decision"] = AccountInformationDecision(data["decision"])
            data["criteria_results"] = tuple(AccountInformationCriteriaResult.from_dict(x) for x in data["criteria_results"])
            return cls(**data)
        except AccountInformationValidationError:
            raise
        except (TypeError, ValueError, KeyError) as exc:
            raise AccountInformationValidationError("invalid account information result") from exc
