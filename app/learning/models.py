from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Mapping, Sequence

from app.committee.models import JSONValue, freeze_json_mapping, thaw_json_value
from app.learning.policies import LearningPolicy
from app.outcomes import TradeOutcome
from app.trade_proposals.policies import decimal_value


@dataclass(frozen=True, slots=True)
class LearningCheck:
    name: str
    passed: bool

    def __post_init__(self) -> None:
        if self.name not in {"sample not empty", "all outcomes closed", "valid pnl values"}:
            raise ValueError("name must be a recognized learning check")
        if not isinstance(self.passed, bool):
            raise ValueError("passed must be a boolean")

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "passed": self.passed}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> LearningCheck:
        if not isinstance(value, Mapping):
            raise ValueError("serialized check must be a mapping")
        try:
            return cls(value["name"], value["passed"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Unable to deserialize learning check") from exc


@dataclass(frozen=True, slots=True)
class LearningRequest:
    outcomes: Sequence[TradeOutcome]
    policy: LearningPolicy
    metadata: Mapping[str, JSONValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if isinstance(self.outcomes, (str, bytes)) or not isinstance(self.outcomes, Sequence):
            raise ValueError("outcomes must be a sequence of TradeOutcome")
        outcomes = tuple(self.outcomes)
        if not outcomes:
            raise ValueError("outcomes cannot be empty")
        if not all(isinstance(item, TradeOutcome) for item in outcomes):
            raise ValueError("every outcome must be a TradeOutcome")
        object.__setattr__(self, "outcomes", outcomes)
        if not isinstance(self.policy, LearningPolicy):
            raise ValueError("policy must be a LearningPolicy")
        object.__setattr__(self, "metadata", freeze_json_mapping("metadata", self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {"outcomes": [item.to_dict() for item in self.outcomes], "policy": self.policy.to_dict(),
                "metadata": thaw_json_value(self.metadata)}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> LearningRequest:
        if not isinstance(value, Mapping):
            raise ValueError("serialized request must be a mapping")
        try:
            return cls(tuple(TradeOutcome.from_dict(item) for item in value["outcomes"]),
                       LearningPolicy.from_dict(value["policy"]), value.get("metadata", {}))
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Unable to deserialize learning request") from exc


@dataclass(frozen=True, slots=True)
class LearningReport:
    report_id: str
    sample_size: int
    wins: int
    losses: int
    win_rate: Decimal
    average_win: Decimal
    average_loss: Decimal
    total_profit: Decimal
    total_loss: Decimal
    net_profit: Decimal
    expectancy: Decimal
    profit_factor: Decimal
    largest_win: Decimal
    largest_loss: Decimal
    average_return: Decimal
    policy_version: str
    learning_engine_version: str
    checks: tuple[LearningCheck, ...]
    metadata: Mapping[str, JSONValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("report_id", "policy_version", "learning_engine_version"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be nonempty")
            object.__setattr__(self, name, value.strip())
        for name in ("sample_size", "wins", "losses"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a nonnegative integer")
        if self.sample_size < 1 or self.wins + self.losses > self.sample_size:
            raise ValueError("sample_size must be positive and cover wins and losses")
        for name in ("win_rate", "average_win", "average_loss", "total_profit", "total_loss", "net_profit",
                     "expectancy", "largest_win", "largest_loss", "average_return"):
            object.__setattr__(self, name, decimal_value(name, getattr(self, name)))
        profit_factor = self.profit_factor
        if not isinstance(profit_factor, Decimal):
            try:
                profit_factor = Decimal(profit_factor)
            except (ValueError, TypeError) as exc:
                raise ValueError("profit_factor must be a Decimal") from exc
        if profit_factor.is_nan() or profit_factor == Decimal("-Infinity") or profit_factor < 0:
            raise ValueError("profit_factor must be nonnegative or Infinity")
        object.__setattr__(self, "profit_factor", profit_factor)
        if not Decimal("0") <= self.win_rate <= Decimal("1"):
            raise ValueError("win_rate must be between zero and one")
        zero = Decimal("0")
        if self.win_rate != Decimal(self.wins) / self.sample_size:
            raise ValueError("win_rate must equal wins divided by sample_size")
        if self.average_win < zero or self.total_profit < zero or self.largest_win < zero:
            raise ValueError("winning statistics must be nonnegative")
        if self.average_loss > zero or self.total_loss > zero or self.largest_loss > zero:
            raise ValueError("losing statistics must be nonpositive")
        if self.average_win != (self.total_profit / self.wins if self.wins else zero):
            raise ValueError("average_win is inconsistent with winning statistics")
        if self.average_loss != (self.total_loss / self.losses if self.losses else zero):
            raise ValueError("average_loss is inconsistent with losing statistics")
        if self.net_profit != self.total_profit + self.total_loss:
            raise ValueError("net_profit must equal total_profit plus total_loss")
        if self.expectancy != self.net_profit / self.sample_size:
            raise ValueError("expectancy must equal net_profit divided by sample_size")
        expected_factor = (self.total_profit / abs(self.total_loss)
                           if self.losses else Decimal("Infinity"))
        if self.profit_factor != expected_factor:
            raise ValueError("profit_factor is inconsistent with profit and loss totals")
        if (self.wins == 0 and self.largest_win != zero) or (self.losses == 0 and self.largest_loss != zero):
            raise ValueError("largest win and loss require corresponding samples")
        expected = ("sample not empty", "all outcomes closed", "valid pnl values")
        if (not isinstance(self.checks, tuple) or not all(isinstance(item, LearningCheck) for item in self.checks)
                or tuple(item.name for item in self.checks) != expected):
            raise ValueError("checks must contain the stable ordered learning checks")
        object.__setattr__(self, "metadata", freeze_json_mapping("metadata", self.metadata))

    def to_dict(self) -> dict[str, Any]:
        data = {name: str(getattr(self, name)) for name in ("win_rate", "average_win", "average_loss",
            "total_profit", "total_loss", "net_profit", "expectancy", "profit_factor", "largest_win",
            "largest_loss", "average_return")}
        data.update({"report_id": self.report_id, "sample_size": self.sample_size, "wins": self.wins,
            "losses": self.losses, "policy_version": self.policy_version,
            "learning_engine_version": self.learning_engine_version,
            "checks": [item.to_dict() for item in self.checks], "metadata": thaw_json_value(self.metadata)})
        return data

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> LearningReport:
        if not isinstance(value, Mapping):
            raise ValueError("serialized report must be a mapping")
        try:
            data = dict(value)
            data["checks"] = tuple(LearningCheck.from_dict(item) for item in data["checks"])
            return cls(**data)
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Unable to deserialize learning report") from exc
