from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone
from decimal import Decimal

from app.committee.models import (
    AgentOpinion,
    AgentOpinionAction,
    CommitteeAction,
    CommitteeOpinion,
    CommitteeVote,
)
from app.committee.weighting import AgentWeightConfiguration


BULLISH_THRESHOLD = Decimal("0.20")
BEARISH_THRESHOLD = Decimal("-0.20")
DIRECTIONAL_STRENGTH_WEIGHT = Decimal("0.60")
AVERAGE_CONFIDENCE_WEIGHT = Decimal("0.25")
CONSENSUS_WEIGHT = Decimal("0.15")
NEUTRAL_CONFIDENCE_CAP = Decimal("0.70")
EXACT_CANCELLATION_CONFIDENCE_CAP = Decimal("0.50")
ZERO = Decimal("0")
ONE = Decimal("1")


class CommitteeChair:
    """Aggregate normalized specialist opinions without execution coupling."""

    name = "committee_chair_v1"

    def __init__(
        self,
        weight_configuration: AgentWeightConfiguration | None = None,
    ) -> None:
        if weight_configuration is not None and not isinstance(
            weight_configuration, AgentWeightConfiguration
        ):
            raise ValueError(
                "weight_configuration must be an AgentWeightConfiguration"
            )
        self._weights = weight_configuration or AgentWeightConfiguration()

    @property
    def weight_configuration(self) -> AgentWeightConfiguration:
        return self._weights

    def evaluate(
        self,
        opinions: Sequence[AgentOpinion],
        *,
        timestamp: datetime,
    ) -> CommitteeOpinion:
        evaluation_time = _aware_timestamp(timestamp)
        submitted = tuple(opinions)
        if any(not isinstance(item, AgentOpinion) for item in submitted):
            raise ValueError("opinions must contain only AgentOpinion items")
        symbols = {item.symbol for item in submitted}
        if len(symbols) > 1:
            raise ValueError("committee opinions cannot contain mixed symbols")
        names = [item.agent_name for item in submitted]
        if len(set(names)) != len(names):
            raise ValueError("duplicate agent names are not allowed")
        if any(item.timestamp > evaluation_time for item in submitted):
            raise ValueError(
                "opinion timestamps cannot be later than committee timestamp"
            )

        ordered = tuple(sorted(submitted, key=lambda item: item.agent_name))
        votes = tuple(self._vote(item) for item in ordered)
        included_pairs = tuple(
            (opinion, vote)
            for opinion, vote in zip(ordered, votes, strict=True)
            if vote.included
        )
        return self._opinion(
            symbol=next(iter(symbols), "UNKNOWN"),
            timestamp=evaluation_time,
            submitted_count=len(submitted),
            votes=votes,
            included_pairs=included_pairs,
        )

    def _vote(self, opinion: AgentOpinion) -> CommitteeVote:
        configured = _decimal(
            self._weights.weight_for(opinion.agent_name)
        )
        confidence = _decimal(opinion.confidence)
        if configured == ZERO:
            included = False
            exclusion_reason = "Configured weight is zero."
        elif confidence < _decimal(self._weights.minimum_confidence):
            included = False
            exclusion_reason = (
                "Opinion confidence is below the configured minimum."
            )
        else:
            included = True
            exclusion_reason = None
        effective = configured * confidence if included else ZERO
        weighted = _decimal(opinion.score) * effective if included else ZERO
        return CommitteeVote(
            agent_name=opinion.agent_name,
            action=opinion.action,
            raw_score=float(_decimal(opinion.score)),
            confidence=float(confidence),
            configured_weight=float(configured),
            effective_weight=float(effective),
            weighted_score=float(weighted),
            included=included,
            exclusion_reason=exclusion_reason,
        )

    def _opinion(
        self,
        *,
        symbol: str,
        timestamp: datetime,
        submitted_count: int,
        votes: tuple[CommitteeVote, ...],
        included_pairs: tuple[tuple[AgentOpinion, CommitteeVote], ...],
    ) -> CommitteeOpinion:
        effective_total = sum(
            (
                self._effective_weight(opinion)
                for opinion, _ in included_pairs
            ),
            ZERO,
        )
        weighted_total = sum(
            (
                _decimal(opinion.score) * self._effective_weight(opinion)
                for opinion, _ in included_pairs
            ),
            ZERO,
        )
        if effective_total == ZERO:
            score = confidence = consensus = ZERO
            action = CommitteeAction.NEUTRAL
        else:
            score = _clamp(weighted_total / effective_total, -ONE, ONE)
            action = _action_for(score)
            consensus = self._consensus(included_pairs, effective_total)
            confidence = self._confidence(
                included_pairs,
                score=score,
                consensus=consensus,
                action=action,
            )

        bullish = sum(
            opinion.action is AgentOpinionAction.BULLISH
            for opinion, _ in included_pairs
        )
        bearish = sum(
            opinion.action is AgentOpinionAction.BEARISH
            for opinion, _ in included_pairs
        )
        neutral = sum(
            opinion.action is AgentOpinionAction.NEUTRAL
            for opinion, _ in included_pairs
        )
        reasons = self._reasons(
            included_pairs,
            score=score,
            action=action,
            consensus=consensus,
            bullish=bullish,
            bearish=bearish,
        )
        return CommitteeOpinion(
            symbol=symbol,
            timestamp=timestamp,
            action=action,
            confidence=float(confidence),
            score=float(score),
            consensus=float(consensus),
            participating_agents=len(included_pairs),
            bullish_agents=bullish,
            bearish_agents=bearish,
            neutral_agents=neutral,
            agent_names=tuple(
                opinion.agent_name for opinion, _ in included_pairs
            ),
            reasons=reasons,
            votes=votes,
            weighting_version=self._weights.version,
            chair_version=self.name,
            metadata={
                "threshold_bullish": str(BULLISH_THRESHOLD),
                "threshold_bearish": str(BEARISH_THRESHOLD),
                "weighting_version": self._weights.version,
                "chair_version": self.name,
                "total_submitted_opinions": submitted_count,
                "excluded_opinions": submitted_count - len(included_pairs),
                "deterministic": True,
            },
        )

    def _consensus(
        self,
        included_pairs: tuple[tuple[AgentOpinion, CommitteeVote], ...],
        effective_total: Decimal,
    ) -> Decimal:
        weights = {
            action: sum(
                (
                    self._effective_weight(opinion)
                    for opinion, _ in included_pairs
                    if opinion.action is action
                ),
                ZERO,
            )
            for action in AgentOpinionAction
        }
        return _clamp(max(weights.values()) / effective_total, ZERO, ONE)

    def _confidence(
        self,
        included_pairs: tuple[tuple[AgentOpinion, CommitteeVote], ...],
        *,
        score: Decimal,
        consensus: Decimal,
        action: CommitteeAction,
    ) -> Decimal:
        configured_total = sum(
            (
                self._configured_weight(opinion)
                for opinion, _ in included_pairs
            ),
            ZERO,
        )
        confidence_total = sum(
            (
                _decimal(opinion.confidence)
                * self._configured_weight(opinion)
                for opinion, _ in included_pairs
            ),
            ZERO,
        )
        average_confidence = confidence_total / configured_total
        result = (
            abs(score) * DIRECTIONAL_STRENGTH_WEIGHT
            + average_confidence * AVERAGE_CONFIDENCE_WEIGHT
            + consensus * CONSENSUS_WEIGHT
        )
        result = _clamp(result, ZERO, ONE)
        if action is CommitteeAction.NEUTRAL:
            result = min(result, NEUTRAL_CONFIDENCE_CAP)
        if score == ZERO:
            result = min(result, EXACT_CANCELLATION_CONFIDENCE_CAP)
        return result

    def _configured_weight(self, opinion: AgentOpinion) -> Decimal:
        return _decimal(self._weights.weight_for(opinion.agent_name))

    def _effective_weight(self, opinion: AgentOpinion) -> Decimal:
        return self._configured_weight(opinion) * _decimal(opinion.confidence)

    @staticmethod
    def _reasons(
        included_pairs: tuple[tuple[AgentOpinion, CommitteeVote], ...],
        *,
        score: Decimal,
        action: CommitteeAction,
        consensus: Decimal,
        bullish: int,
        bearish: int,
    ) -> tuple[str, ...]:
        if not included_pairs:
            summary = (
                "No specialist opinions met committee inclusion requirements."
            )
        elif action is CommitteeAction.NEUTRAL and bullish and bearish:
            summary = "Opposing specialist opinions produced a NEUTRAL result."
        else:
            summary = (
                f"Committee score {score:.2f} produced an {action.value} opinion."
            )
        agent_reasons = tuple(
            f"{opinion.agent_name}: {opinion.reasons[0]}"
            for opinion, _ in included_pairs
        )
        consensus_reason = (
            f"Directional consensus was {consensus:.2f} across "
            f"{len(included_pairs)} participating agents."
        )
        return (summary, *agent_reasons, consensus_reason)


def _action_for(score: Decimal) -> CommitteeAction:
    if score >= BULLISH_THRESHOLD:
        return CommitteeAction.BULLISH
    if score <= BEARISH_THRESHOLD:
        return CommitteeAction.BEARISH
    return CommitteeAction.NEUTRAL


def _aware_timestamp(value: datetime) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(timezone.utc)


def _decimal(value: float) -> Decimal:
    return Decimal(str(value))


def _clamp(value: Decimal, lower: Decimal, upper: Decimal) -> Decimal:
    return max(lower, min(upper, value))
