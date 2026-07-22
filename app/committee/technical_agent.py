from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from app.evidence.enums import EvidenceCategory, SignalDirection
from app.evidence.models import Evidence
from app.evidence.scoring import score_evidence


class TechnicalAgentAction(StrEnum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"


@dataclass(frozen=True, slots=True)
class TechnicalAgentOpinion:
    symbol: str
    timestamp: datetime
    action: TechnicalAgentAction
    confidence: float
    score: float
    bullish_count: int
    bearish_count: int
    neutral_count: int
    evidence_ids: tuple[str, ...]
    reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        if (
            not isinstance(self.symbol, str)
            or not self.symbol
            or self.symbol != self.symbol.strip().upper()
        ):
            raise ValueError("symbol must be a nonempty uppercase string")
        if (
            not isinstance(self.timestamp, datetime)
            or self.timestamp.tzinfo is None
            or self.timestamp.utcoffset() is None
        ):
            raise ValueError("timestamp must be timezone-aware")
        if not isinstance(self.action, TechnicalAgentAction):
            raise ValueError("action must be a TechnicalAgentAction")
        for name, number in (
            ("confidence", self.confidence),
            ("score", self.score),
        ):
            if isinstance(number, bool) or not math.isfinite(float(number)):
                raise ValueError(f"{name} must be finite")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        if abs(self.score) > 1.0:
            raise ValueError("absolute score must be between 0 and 1")
        for name in ("bullish_count", "bearish_count", "neutral_count"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a nonnegative integer")
        total = self.bullish_count + self.bearish_count + self.neutral_count
        if total and not self.evidence_ids:
            raise ValueError("evidence_ids cannot be empty when evidence was supplied")
        if len(self.evidence_ids) != total:
            raise ValueError("evidence_ids count must match evidence counts")
        if not self.reasons or any(
            not isinstance(reason, str) or not reason.strip()
            for reason in self.reasons
        ):
            raise ValueError("reasons must contain nonempty strings")
        if not isinstance(self.evidence_ids, tuple) or not all(
            isinstance(item, str) and item
            for item in self.evidence_ids
        ):
            raise ValueError("evidence_ids must be an immutable tuple of strings")
        if not isinstance(self.reasons, tuple):
            raise ValueError("reasons must be an immutable tuple")


class TechnicalAgent:
    """Aggregate provider evidence into an analysis-only technical opinion."""

    name = "technical_agent_v1"

    def evaluate(
        self,
        evidence: Sequence[Evidence],
        *,
        timestamp: datetime,
    ) -> TechnicalAgentOpinion:
        if (
            not isinstance(timestamp, datetime)
            or timestamp.tzinfo is None
            or timestamp.utcoffset() is None
        ):
            raise ValueError("timestamp must be timezone-aware")
        items = tuple(evidence)
        if not items:
            return TechnicalAgentOpinion(
                symbol="UNKNOWN",
                timestamp=timestamp,
                action=TechnicalAgentAction.NEUTRAL,
                confidence=0.0,
                score=0.0,
                bullish_count=0,
                bearish_count=0,
                neutral_count=0,
                evidence_ids=(),
                reasons=("No usable technical evidence was supplied.",),
            )
        if any(not isinstance(item, Evidence) for item in items):
            raise ValueError("evidence must contain only Evidence items")
        symbols = {item.symbol for item in items}
        if len(symbols) != 1:
            raise ValueError("technical evidence cannot contain mixed symbols")
        if any(item.category is not EvidenceCategory.TECHNICAL for item in items):
            raise ValueError("all evidence must use the technical category")
        if any(item.source != "technical_snapshot_v1" for item in items):
            raise ValueError("all evidence must come from technical_snapshot_v1")
        for item in items:
            indicator = item.metadata.get("indicator")
            role = item.metadata.get("role")
            if role == "volatility_context" and (
                indicator != "atr_14"
                or item.direction is not SignalDirection.NEUTRAL
            ):
                raise ValueError(
                    "volatility_context must be neutral ATR evidence"
                )
            if indicator == "atr_14" and role != "volatility_context":
                raise ValueError(
                    "ATR evidence must use the volatility_context role"
                )
        identifiers = [str(item.evidence_id) for item in items]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("duplicate evidence IDs are not allowed")

        ordered = tuple(
            sorted(
                items,
                key=lambda item: (
                    item.source,
                    str(item.metadata.get("indicator", "")),
                    str(item.evidence_id),
                ),
            )
        )
        aggregate = score_evidence(ordered)
        directional_items = tuple(
            item
            for item in ordered
            if item.metadata.get("role") != "volatility_context"
        )
        directional = (
            score_evidence(directional_items)
            if directional_items
            else None
        )
        score = max(
            -1.0,
            min(1.0, directional.score if directional else 0.0),
        )
        action = (
            TechnicalAgentAction.BULLISH
            if score >= 0.20
            else TechnicalAgentAction.BEARISH
            if score <= -0.20
            else TechnicalAgentAction.NEUTRAL
        )
        return TechnicalAgentOpinion(
            symbol=aggregate.symbol,
            timestamp=timestamp,
            action=action,
            confidence=max(
                0.0,
                min(1.0, directional.confidence if directional else 0.0),
            ),
            score=score,
            bullish_count=aggregate.bullish_count,
            bearish_count=aggregate.bearish_count,
            neutral_count=aggregate.neutral_count,
            evidence_ids=tuple(str(item.evidence_id) for item in ordered),
            reasons=tuple(item.explanation for item in ordered),
        )
