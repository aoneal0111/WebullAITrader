from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Mapping

if TYPE_CHECKING:
    from app.committee.technical_agent import TechnicalAgentOpinion


JSONScalar = str | int | float | bool | None
JSONValue = JSONScalar | tuple["JSONValue", ...] | Mapping[str, "JSONValue"]


class AgentOpinionAction(StrEnum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"


class CommitteeAction(StrEnum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"


@dataclass(frozen=True, slots=True)
class AgentOpinion:
    agent_name: str
    symbol: str
    timestamp: datetime
    action: AgentOpinionAction
    confidence: float
    score: float
    reasons: tuple[str, ...]
    evidence_ids: tuple[str, ...] = ()
    metadata: Mapping[str, JSONValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "agent_name",
            _required_stripped("agent_name", self.agent_name),
        )
        object.__setattr__(self, "symbol", _uppercase_symbol(self.symbol))
        object.__setattr__(
            self,
            "timestamp",
            _aware_timestamp(self.timestamp),
        )
        if not isinstance(self.action, AgentOpinionAction):
            raise ValueError("action must be an AgentOpinionAction")
        confidence = _bounded_number(
            "confidence", self.confidence, lower=0.0, upper=1.0
        )
        score = _bounded_number(
            "score", self.score, lower=-1.0, upper=1.0
        )
        if self.action is AgentOpinionAction.BULLISH and score < 0:
            raise ValueError("BULLISH opinions cannot have a negative score")
        if self.action is AgentOpinionAction.BEARISH and score > 0:
            raise ValueError("BEARISH opinions cannot have a positive score")
        object.__setattr__(self, "confidence", confidence)
        object.__setattr__(self, "score", score)
        object.__setattr__(self, "reasons", _nonempty_strings("reasons", self.reasons))
        evidence_ids = _string_tuple("evidence_ids", self.evidence_ids)
        if len(set(evidence_ids)) != len(evidence_ids):
            raise ValueError("evidence_ids must be unique")
        object.__setattr__(self, "evidence_ids", evidence_ids)
        object.__setattr__(self, "metadata", freeze_json_mapping("metadata", self.metadata))

    @classmethod
    def from_technical_opinion(
        cls,
        opinion: TechnicalAgentOpinion,
    ) -> AgentOpinion:
        from app.committee.technical_agent import (
            TechnicalAgentAction,
            TechnicalAgentOpinion,
        )

        if not isinstance(opinion, TechnicalAgentOpinion):
            raise ValueError("opinion must be a TechnicalAgentOpinion")
        action_mapping = {
            TechnicalAgentAction.BULLISH: AgentOpinionAction.BULLISH,
            TechnicalAgentAction.BEARISH: AgentOpinionAction.BEARISH,
            TechnicalAgentAction.NEUTRAL: AgentOpinionAction.NEUTRAL,
        }
        return cls(
            agent_name="technical_agent_v1",
            symbol=opinion.symbol,
            timestamp=opinion.timestamp,
            action=action_mapping[opinion.action],
            confidence=opinion.confidence,
            score=opinion.score,
            reasons=opinion.reasons,
            evidence_ids=opinion.evidence_ids,
            metadata={
                "specialist_type": "technical",
                "adapter_version": "1",
                "deterministic": True,
            },
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_name": self.agent_name,
            "symbol": self.symbol,
            "timestamp": self.timestamp.isoformat(),
            "action": self.action.value,
            "confidence": self.confidence,
            "score": self.score,
            "reasons": list(self.reasons),
            "evidence_ids": list(self.evidence_ids),
            "metadata": thaw_json_value(self.metadata),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> AgentOpinion:
        if not isinstance(value, Mapping):
            raise ValueError("serialized opinion must be a mapping")
        try:
            return cls(
                agent_name=value["agent_name"],
                symbol=value["symbol"],
                timestamp=datetime.fromisoformat(value["timestamp"]),
                action=AgentOpinionAction(value["action"]),
                confidence=value["confidence"],
                score=value["score"],
                reasons=tuple(value["reasons"]),
                evidence_ids=tuple(value.get("evidence_ids", ())),
                metadata=value.get("metadata", {}),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Unable to deserialize agent opinion") from exc


@dataclass(frozen=True, slots=True)
class CommitteeVote:
    agent_name: str
    action: AgentOpinionAction
    raw_score: float
    confidence: float
    configured_weight: float
    effective_weight: float
    weighted_score: float
    included: bool
    exclusion_reason: str | None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "agent_name",
            _required_stripped("agent_name", self.agent_name),
        )
        if not isinstance(self.action, AgentOpinionAction):
            raise ValueError("action must be an AgentOpinionAction")
        for name, lower, upper in (
            ("raw_score", -1.0, 1.0),
            ("confidence", 0.0, 1.0),
            ("configured_weight", 0.0, 1.0),
            ("effective_weight", 0.0, 1.0),
            ("weighted_score", -1.0, 1.0),
        ):
            object.__setattr__(
                self,
                name,
                _bounded_number(name, getattr(self, name), lower, upper),
            )
        if not isinstance(self.included, bool):
            raise ValueError("included must be a boolean")
        if self.included and self.exclusion_reason is not None:
            raise ValueError("included votes cannot have an exclusion reason")
        if not self.included:
            if self.effective_weight != 0 or self.weighted_score != 0:
                raise ValueError("excluded votes must have zero effective weight")
            if not isinstance(self.exclusion_reason, str) or not self.exclusion_reason.strip():
                raise ValueError("excluded votes require an exclusion reason")

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_name": self.agent_name,
            "action": self.action.value,
            "raw_score": self.raw_score,
            "confidence": self.confidence,
            "configured_weight": self.configured_weight,
            "effective_weight": self.effective_weight,
            "weighted_score": self.weighted_score,
            "included": self.included,
            "exclusion_reason": self.exclusion_reason,
        }


@dataclass(frozen=True, slots=True)
class CommitteeOpinion:
    symbol: str
    timestamp: datetime
    action: CommitteeAction
    confidence: float
    score: float
    consensus: float
    participating_agents: int
    bullish_agents: int
    bearish_agents: int
    neutral_agents: int
    agent_names: tuple[str, ...]
    reasons: tuple[str, ...]
    votes: tuple[CommitteeVote, ...]
    weighting_version: str
    chair_version: str
    metadata: Mapping[str, JSONValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", _uppercase_symbol(self.symbol))
        object.__setattr__(self, "timestamp", _aware_timestamp(self.timestamp))
        if not isinstance(self.action, CommitteeAction):
            raise ValueError("action must be a CommitteeAction")
        for name, lower, upper in (
            ("confidence", 0.0, 1.0),
            ("score", -1.0, 1.0),
            ("consensus", 0.0, 1.0),
        ):
            object.__setattr__(
                self,
                name,
                _bounded_number(name, getattr(self, name), lower, upper),
            )
        counts = (
            self.participating_agents,
            self.bullish_agents,
            self.bearish_agents,
            self.neutral_agents,
        )
        if any(isinstance(count, bool) or not isinstance(count, int) or count < 0 for count in counts):
            raise ValueError("committee counts must be nonnegative integers")
        if self.bullish_agents + self.bearish_agents + self.neutral_agents != self.participating_agents:
            raise ValueError("direction counts must sum to participating_agents")
        if not isinstance(self.votes, tuple) or not all(isinstance(vote, CommitteeVote) for vote in self.votes):
            raise ValueError("votes must be an immutable tuple of CommitteeVote")
        included_names = tuple(vote.agent_name for vote in self.votes if vote.included)
        if self.participating_agents != len(included_names):
            raise ValueError("participating_agents must equal included votes")
        agent_names = _string_tuple("agent_names", self.agent_names)
        if agent_names != included_names:
            raise ValueError("agent_names must match included votes in order")
        object.__setattr__(self, "agent_names", agent_names)
        object.__setattr__(self, "reasons", _nonempty_strings("reasons", self.reasons))
        object.__setattr__(self, "weighting_version", _required_stripped("weighting_version", self.weighting_version))
        object.__setattr__(self, "chair_version", _required_stripped("chair_version", self.chair_version))
        object.__setattr__(self, "metadata", freeze_json_mapping("metadata", self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timestamp": self.timestamp.isoformat(),
            "action": self.action.value,
            "confidence": self.confidence,
            "score": self.score,
            "consensus": self.consensus,
            "participating_agents": self.participating_agents,
            "bullish_agents": self.bullish_agents,
            "bearish_agents": self.bearish_agents,
            "neutral_agents": self.neutral_agents,
            "agent_names": list(self.agent_names),
            "reasons": list(self.reasons),
            "votes": [vote.to_dict() for vote in self.votes],
            "weighting_version": self.weighting_version,
            "chair_version": self.chair_version,
            "metadata": thaw_json_value(self.metadata),
        }


def freeze_json_mapping(
    name: str,
    value: Mapping[str, Any],
) -> Mapping[str, JSONValue]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be a mapping")
    frozen: dict[str, JSONValue] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not key.strip():
            raise ValueError(f"{name} keys must be nonempty strings")
        frozen[key] = _freeze_json_value(item, f"{name}.{key}")
    return MappingProxyType(frozen)


def _freeze_json_value(value: Any, path: str) -> JSONValue:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path} must contain only finite numbers")
        return value
    if isinstance(value, Mapping):
        return freeze_json_mapping(path, value)
    if isinstance(value, (list, tuple)):
        return tuple(
            _freeze_json_value(item, f"{path}[{index}]")
            for index, item in enumerate(value)
        )
    raise ValueError(
        f"{path} contains unsupported value type: {type(value).__name__}"
    )


def thaw_json_value(value: JSONValue) -> Any:
    if isinstance(value, Mapping):
        return {key: thaw_json_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [thaw_json_value(item) for item in value]
    return value


def _required_stripped(name: str, value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a nonempty string")
    return value.strip()


def _uppercase_symbol(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("symbol must be a nonempty uppercase string")
    normalized = value.strip()
    if normalized != normalized.upper():
        raise ValueError("symbol must be uppercase")
    return normalized


def _aware_timestamp(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(timezone.utc)


def _bounded_number(
    name: str,
    value: float,
    lower: float,
    upper: float,
) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be numeric, not boolean")
    try:
        normalized = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be numeric") from exc
    if not math.isfinite(normalized):
        raise ValueError(f"{name} must be finite")
    if not lower <= normalized <= upper:
        raise ValueError(f"{name} must be between {lower:g} and {upper:g}")
    return normalized


def _string_tuple(name: str, value: tuple[str, ...]) -> tuple[str, ...]:
    if not isinstance(value, tuple):
        raise ValueError(f"{name} must be an immutable tuple")
    if any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValueError(f"{name} must contain nonempty strings")
    return tuple(item.strip() for item in value)


def _nonempty_strings(name: str, value: tuple[str, ...]) -> tuple[str, ...]:
    normalized = _string_tuple(name, value)
    if not normalized:
        raise ValueError(f"{name} must not be empty")
    return normalized
