from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any
from decimal import Decimal

from app.indicators.market_snapshot import MarketSnapshot
from app.strategy.scoring import MarketAnalysis, StrategyScore

STRATEGY_VERSION = "1.0"
PROMPT_VERSION = "1.0"

SYSTEM_PROMPT = """You are a cautious market-analysis assistant.
Analyze only the market information supplied in the user message. Never invent,
estimate, or retrieve missing data. If evidence is insufficient or conflicting,
use HOLD and lower confidence. Explain the reasoning and explicitly express
uncertainty. Use only BUY, SELL, or HOLD. Confidence must be an integer from 0
through 100. stop_loss and take_profit are optional and must be positive numbers
or null. Return JSON only, with exactly these keys:
{"action":"BUY|SELL|HOLD","confidence":0,"reason":"...","stop_loss":null,"take_profit":null}
Do not include Markdown, commentary, or trading-execution instructions."""


class PromptValidationError(ValueError):
    """Raised when prompt inputs or package contents are invalid."""


@dataclass(frozen=True, slots=True)
class PromptMetadata:
    timestamp: str
    symbol: str
    strategy_version: str
    prompt_version: str


@dataclass(frozen=True, slots=True)
class PromptPackage:
    system_prompt: str
    user_prompt: str
    metadata: PromptMetadata

    def __post_init__(self) -> None:
        if not self.system_prompt.strip() or not self.user_prompt.strip():
            raise PromptValidationError("prompts must not be empty")
        if not self.metadata.symbol.strip():
            raise PromptValidationError("metadata symbol must not be empty")
        if not self.metadata.strategy_version or not self.metadata.prompt_version:
            raise PromptValidationError("prompt and strategy versions are required")
        try:
            parsed_timestamp = datetime.fromisoformat(self.metadata.timestamp)
        except ValueError as exc:
            raise PromptValidationError("metadata timestamp must be ISO 8601") from exc
        if parsed_timestamp.tzinfo is None:
            raise PromptValidationError("metadata timestamp must include a timezone")
        try:
            user_data = json.loads(self.user_prompt)
        except json.JSONDecodeError as exc:
            raise PromptValidationError("user prompt must contain valid JSON") from exc
        if user_data.get("symbol") != self.metadata.symbol:
            raise PromptValidationError("user prompt and metadata symbols must match")

    def to_dict(self) -> dict[str, Any]:
        return {
            "system_prompt": self.system_prompt,
            "user_prompt": self.user_prompt,
            "metadata": asdict(self.metadata),
        }

    def to_json(self, *, indent: int | None = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, sort_keys=True, allow_nan=False)


def build_prompt_package(
    snapshot: MarketSnapshot,
    analysis: MarketAnalysis,
    strategy_score: StrategyScore,
    *,
    timestamp: datetime | None = None,
    strategy_version: str = STRATEGY_VERSION,
    prompt_version: str = PROMPT_VERSION,
) -> PromptPackage:
    """Build a local prompt package without model, broker, or network access."""
    _validate_inputs(snapshot, analysis, strategy_score)
    generated_at = timestamp or datetime.now(UTC)
    if generated_at.tzinfo is None:
        raise PromptValidationError("timestamp must include a timezone")
    symbol = snapshot.symbol.strip().upper()
    user_data = {
        "symbol": symbol,
        "current_price": snapshot.close,
        "indicators": {
            "ema_12": snapshot.ema_12,
            "ema_26": snapshot.ema_26,
            "rsi_14": snapshot.rsi_14,
            "macd": snapshot.macd,
            "macd_signal": snapshot.macd_signal,
            "macd_histogram": snapshot.macd_histogram,
            "atr_14": snapshot.atr_14,
            "bollinger_upper": snapshot.bollinger_upper,
            "bollinger_middle": snapshot.bollinger_middle,
            "bollinger_lower": snapshot.bollinger_lower,
            "vwap": snapshot.vwap,
        },
        "strategy": {
            "trend": analysis.trend,
            "momentum": analysis.momentum,
            "volatility": analysis.volatility,
            "overall_score": analysis.overall_score,
            "deterministic_action": strategy_score.action.value,
            "deterministic_confidence": strategy_score.confidence,
        },
    }
    return PromptPackage(
        system_prompt=SYSTEM_PROMPT,
        user_prompt=json.dumps(_json_safe(user_data), sort_keys=True, allow_nan=False),
        metadata=PromptMetadata(
            timestamp=generated_at.astimezone(UTC).isoformat(),
            symbol=symbol,
            strategy_version=strategy_version,
            prompt_version=prompt_version,
        ),
    )


def _validate_inputs(
    snapshot: MarketSnapshot, analysis: MarketAnalysis, strategy_score: StrategyScore
) -> None:
    if not snapshot.symbol.strip():
        raise PromptValidationError("snapshot symbol must not be empty")
    if snapshot.close <= 0:
        raise PromptValidationError("current price must be greater than zero")
    if not 0 <= analysis.overall_score <= 100:
        raise PromptValidationError("overall score must be between 0 and 100")
    if not 0 <= strategy_score.confidence <= 100:
        raise PromptValidationError("strategy confidence must be between 0 and 100")


def _json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    return value
