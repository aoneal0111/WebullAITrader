from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class RiskLimits:
    minimum_confidence: int
    minimum_reward_risk_ratio: Decimal
    maximum_position_percent: Decimal
    missing_atr_position_percent: Decimal


DEFAULT_RISK_LIMITS = RiskLimits(70, Decimal("2"), Decimal("5"), Decimal("2.5"))

MINIMUM_CONFIDENCE = DEFAULT_RISK_LIMITS.minimum_confidence
MINIMUM_REWARD_RISK_RATIO = DEFAULT_RISK_LIMITS.minimum_reward_risk_ratio
MAX_POSITION_PERCENT = DEFAULT_RISK_LIMITS.maximum_position_percent
MISSING_ATR_POSITION_PERCENT = DEFAULT_RISK_LIMITS.missing_atr_position_percent
