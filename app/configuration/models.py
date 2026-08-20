from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from pathlib import Path


class TradingEnvironment(StrEnum):
    TEST = "TEST"
    PAPER = "PAPER"
    SANDBOX = "SANDBOX"
    LIVE = "LIVE"
    PRODUCTION = "PRODUCTION"


@dataclass(frozen=True, slots=True)
class TradingConfiguration:
    environment: TradingEnvironment
    account_id: str
    api_key: str
    api_secret: str
    api_base_url: str
    stream_url: str


@dataclass(frozen=True, slots=True)
class MarketDataConfiguration:
    environment: TradingEnvironment
    api_key: str
    api_secret: str
    api_base_url: str
    stream_url: str


@dataclass(frozen=True, slots=True)
class SECEdgarConfiguration:
    user_agent: str
    freshness_days: int = 3
    timeout_seconds: float = 10.0


@dataclass(frozen=True, slots=True)
class YahooFinanceNewsConfiguration:
    freshness_minutes: int = 1_440
    timeout_seconds: float = 5.0
    cache_ttl_seconds: float = 300.0


@dataclass(frozen=True, slots=True)
class OperationalConfiguration:
    environment: TradingEnvironment

    broker_provider: str

    account_id: str
    api_key: str
    api_secret: str

    api_base_url: str
    stream_url: str

    authorization_database_path: Path
    execution_database_path: Path
    market_event_database_path: Path
    emergency_stop_database_path: Path

    log_level: str
    health_port: int
    live_trading_enabled: bool

    max_order_notional: Decimal
    max_daily_notional: Decimal
    max_open_positions: int
    max_open_orders: int
    max_order_rate: int
    max_quantity_per_symbol: Decimal

    allowed_symbols: tuple[str, ...]
    blocked_symbols: tuple[str, ...]

    maximum_market_data_age_seconds: int
    reconciliation_interval_seconds: int
    maximum_reconciliation_age_seconds: int
    maximum_unresolved_mutations: int

    market_data_streaming_enabled: bool = False
    market_data_symbols: tuple[str, ...] = ()
    stream_reconnect_attempts: int = 3
    stream_reconnect_backoff_seconds: Decimal = Decimal("1")

    trading: TradingConfiguration | None = None
    market_data: MarketDataConfiguration | None = None
    warrior_forward_paper_enabled: bool = False
    warrior_forward_capture_path: Path = Path(
        "data/warrior_momentum_v1_forward/forward_capture.sqlite3"
    )
    sec_edgar: SECEdgarConfiguration | None = None
    yahoo_finance_news: YahooFinanceNewsConfiguration | None = None
