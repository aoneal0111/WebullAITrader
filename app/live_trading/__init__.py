"""Synchronous orchestration of one research request and only caller-supplied orders.

Research output is informational and never authorizes, constructs, or alters an
order. This layer performs no inference, risk calculation, provider selection,
persistence, retry, polling, cancellation, concurrency, scheduling, or background work.
"""
from app.live_trading.exceptions import *
from app.live_trading.interfaces import BrokerOrderExecutor,ResearchPortfolioExecutor
from app.live_trading.models import *
from app.live_trading.runtime import LiveTradingRuntime
from app.live_trading.serializers import *
from app.live_trading.validation import validate_request
__all__=("LiveTradingRuntime","ResearchPortfolioExecutor","BrokerOrderExecutor","LiveTradingStatus","LiveTradingResearchStatus","LiveTradingOrderStatus","LiveTradingPolicy","LiveTradingIdentity","LiveTradingOrderIdentity","LiveTradingOrderRequest","LiveTradingRequest","LiveTradingCriteriaResult","LiveTradingResearchRecord","LiveTradingOrderRecord","LiveTradingSummary","LiveTradingResult","LiveTradingError","LiveTradingValidationError","LiveTradingDependencyError","LiveTradingSerializationError","serialize_identity","serialize_order_identity","serialize_policy","serialize_order_request","serialize_request","serialize_criteria","serialize_research_record","serialize_order_record","serialize_summary","serialize_result","validate_request")
