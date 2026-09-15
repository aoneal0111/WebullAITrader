from dataclasses import dataclass
from decimal import Decimal
import dataclasses
import math
from enum import StrEnum
from pathlib import Path


class TradingEnvironment(StrEnum):
    TEST = "TEST"
    PAPER = "PAPER"
    SANDBOX = "SANDBOX"
    LIVE = "LIVE"
    PRODUCTION = "PRODUCTION"


class PaperSymbolAuthorizationMode(StrEnum):
    """How the Warrior PAPER path answers its symbol-authorization gate."""

    STATIC_ALLOWLIST = "STATIC_ALLOWLIST"
    DYNAMIC_WARRIOR = "DYNAMIC_WARRIOR"


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
    user_agent: str = dataclasses.field(repr=False)
    freshness_days: int = 3
    timeout_seconds: float = 10.0


MAX_SYMBOL_INTELLIGENCE_SEC_TICKER_ENTRIES = 50_000
MAX_SYMBOL_INTELLIGENCE_SEC_SUBMISSIONS_CACHE_ENTRIES = 8_192


@dataclass(frozen=True, slots=True)
class SymbolIntelligenceSECEdgarConfiguration:
    """Non-composed SEC acquisition settings with no economic authority."""

    enabled: bool = False
    user_agent: str | None = dataclasses.field(default=None, repr=False)
    requests_per_second: float = 2.0
    connect_timeout_seconds: float = 3.0
    read_timeout_seconds: float = 10.0
    max_retries: int = 2
    backoff_initial_seconds: float = 0.5
    backoff_max_seconds: float = 8.0
    failure_cooldown_seconds: float = 60.0
    freshness_days: int = 3
    ticker_refresh_seconds: float = 86_400.0
    submissions_refresh_seconds: float = 900.0
    max_ticker_entries: int = 25_000
    max_submissions_cache_entries: int = 2_048

    def __post_init__(self) -> None:
        if not isinstance(self.enabled, bool):
            raise TypeError("SEC EDGAR enabled must be boolean")
        user_agent = str(self.user_agent or "").strip()
        if "\n" in user_agent or "\r" in user_agent or len(user_agent) > 512:
            raise ValueError("SEC EDGAR User-Agent is malformed")
        if self.enabled and not user_agent:
            raise ValueError("SEC EDGAR User-Agent is required when enabled")
        object.__setattr__(self, "user_agent", user_agent or None)
        _bounded_float(self.requests_per_second, "requests_per_second", minimum=0.0, maximum=5.0, strict_minimum=True)
        _bounded_float(self.connect_timeout_seconds, "connect_timeout_seconds", minimum=0.0, strict_minimum=True)
        _bounded_float(self.read_timeout_seconds, "read_timeout_seconds", minimum=0.0, strict_minimum=True)
        _bounded_int(self.max_retries, "max_retries", minimum=0, maximum=4)
        _bounded_float(self.backoff_initial_seconds, "backoff_initial_seconds", minimum=0.0)
        _bounded_float(self.backoff_max_seconds, "backoff_max_seconds", minimum=0.0, maximum=30.0)
        if self.backoff_max_seconds < self.backoff_initial_seconds:
            raise ValueError("SEC EDGAR backoff_max_seconds must not be below initial backoff")
        _bounded_float(self.failure_cooldown_seconds, "failure_cooldown_seconds", minimum=0.0, strict_minimum=True)
        _bounded_int(self.freshness_days, "freshness_days", minimum=1)
        _bounded_float(self.ticker_refresh_seconds, "ticker_refresh_seconds", minimum=0.0, strict_minimum=True)
        _bounded_float(self.submissions_refresh_seconds, "submissions_refresh_seconds", minimum=0.0, strict_minimum=True)
        _bounded_int(
            self.max_ticker_entries,
            "max_ticker_entries",
            minimum=1,
            maximum=MAX_SYMBOL_INTELLIGENCE_SEC_TICKER_ENTRIES,
        )
        _bounded_int(
            self.max_submissions_cache_entries,
            "max_submissions_cache_entries",
            minimum=1,
            maximum=MAX_SYMBOL_INTELLIGENCE_SEC_SUBMISSIONS_CACHE_ENTRIES,
        )

    def diagnostic_summary(self) -> dict[str, bool]:
        """Return the only safe configuration diagnostics for this source."""

        return {
            "enabled": self.enabled,
            "user_agent_configured": self.user_agent is not None,
        }


def _bounded_float(
    value: float,
    name: str,
    *,
    minimum: float,
    maximum: float | None = None,
    strict_minimum: bool = False,
) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"SEC EDGAR {name} must be numeric")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError(f"SEC EDGAR {name} must be finite")
    if numeric < minimum or (strict_minimum and numeric == minimum):
        raise ValueError(f"SEC EDGAR {name} is below its bound")
    if maximum is not None and numeric > maximum:
        raise ValueError(f"SEC EDGAR {name} exceeds its bound")


def _bounded_int(
    value: int,
    name: str,
    *,
    minimum: int,
    maximum: int | None = None,
) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"SEC EDGAR {name} must be an integer")
    if value < minimum:
        raise ValueError(f"SEC EDGAR {name} is below its bound")
    if maximum is not None and value > maximum:
        raise ValueError(f"SEC EDGAR {name} exceeds its bound")


@dataclass(frozen=True, slots=True)
class YahooFinanceNewsConfiguration:
    freshness_minutes: int = 1_440
    timeout_seconds: float = 5.0
    cache_ttl_seconds: float = 300.0


@dataclass(frozen=True, slots=True)
class CNBCNewsConfiguration:
    freshness_minutes: int = 1_440
    timeout_seconds: float = 5.0
    refresh_ttl_seconds: float = 3_600.0
    failure_cooldown_seconds: float = 60.0
    maximum_snapshot_age_seconds: float = 7_200.0
    max_items: int = 512
    max_payload_bytes: int = 1_000_000


@dataclass(frozen=True, slots=True)
class MarketWatchNewsConfiguration:
    freshness_minutes: int = 1_440
    timeout_seconds: float = 5.0
    refresh_ttl_seconds: float = 3_600.0
    failure_cooldown_seconds: float = 300.0
    maximum_snapshot_age_seconds: float = 7_200.0
    max_items: int = 256
    max_payload_bytes: int = 250_000


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
    market_data_reconnect_after_seconds: float = 10.0
    suspend_gap_detection_seconds: float = 10.0

    trading: TradingConfiguration | None = None
    market_data: MarketDataConfiguration | None = None
    warrior_forward_paper_enabled: bool = False
    warrior_forward_capture_path: Path = Path(
        "data/warrior_momentum_v1_forward/forward_capture.sqlite3"
    )
    sec_edgar: SECEdgarConfiguration | None = None
    yahoo_finance_news: YahooFinanceNewsConfiguration | None = None
    cnbc_news: CNBCNewsConfiguration | None = None
    marketwatch_news: MarketWatchNewsConfiguration | None = None
    trade_intelligence_enabled: bool = True
    trade_intelligence_path: Path = Path("data/atlas_learning/experiences.sqlite3")
    trade_intelligence_queue_capacity: int = 4096
    paper_symbol_authorization_mode: PaperSymbolAuthorizationMode = (
        PaperSymbolAuthorizationMode.STATIC_ALLOWLIST
    )
    entry_opportunity_value_enabled: bool = False
    entry_opportunity_value_path: Path = Path(
        "data/entry_opportunity_value/observations.jsonl"
    )
    entry_opportunity_value_queue_capacity: int = 1024
    adaptive_entry_research_enabled: bool = False
    adaptive_entry_research_path: Path = Path(
        "data/adaptive_entry_research/recommendations.jsonl"
    )
    adaptive_entry_research_queue_capacity: int = 512
    scanner_universe_observability_enabled: bool = False
    scanner_universe_observability_path: Path = Path(
        "data/scanner_universe_observability/events.jsonl"
    )
    scanner_universe_observability_queue_capacity: int = 4096
    dynamic_momentum_discovery_enabled: bool = False
    dynamic_momentum_discovery_path: Path = Path(
        "data/dynamic_momentum_discovery/observations.jsonl"
    )
    dynamic_momentum_discovery_queue_capacity: int = 1024
    dynamic_momentum_discovery_breadth: int = 100
    dynamic_momentum_discovery_refresh_seconds: int = 60
    memory_observability_enabled: bool = False
    memory_observability_path: Path = Path("memory-observability.jsonl")
    memory_observability_interval_seconds: float = 60.0
    memory_tracemalloc_enabled: bool = False
    memory_tracemalloc_snapshot_interval_seconds: float = 600.0
    memory_gc_tracked_objects_enabled: bool = False
    crypto_discovery_enabled: bool = False
    crypto_discovery_symbols: tuple[str, ...] = ()
    crypto_discovery_refresh_seconds: int = 60
    crypto_discovery_queue_capacity: int = 256
    crypto_discovery_path: Path = Path("crypto-research.jsonl")
    crypto_intelligence_enabled: bool = False
    crypto_intelligence_max_active_decisions: int = 4096
    crypto_intelligence_history_max_symbols: int = 10
    crypto_intelligence_history_bar_count: int = 64
    crypto_intelligence_history_request_budget: int = 20
    crypto_intelligence_m1_refresh_seconds: int = 60
    crypto_intelligence_m5_refresh_seconds: int = 60
    crypto_catalyst_acquisition_enabled: bool = False
    crypto_catalyst_sec_enabled: bool = False
    crypto_catalyst_federal_register_enabled: bool = False
    crypto_catalyst_statuspage_enabled: bool = False
    crypto_catalyst_bybit_enabled: bool = False
    crypto_catalyst_scheduler_tick_seconds: int = 5
    crypto_catalyst_sec_cadence_seconds: int = 3600
    crypto_catalyst_federal_register_cadence_seconds: int = 7200
    crypto_catalyst_statuspage_cadence_seconds: int = 300
    crypto_catalyst_bybit_cadence_seconds: int = 900
    historical_entry_experiment_enabled: bool = False
    historical_entry_experiment_mode: str = "OBSERVE_ONLY"
    historical_entry_experiment_path: Path = Path(
        "data/paper_trade_experiment.sqlite3"
    )
    symbol_intelligence_sec_edgar: SymbolIntelligenceSECEdgarConfiguration = (
        dataclasses.field(default_factory=SymbolIntelligenceSECEdgarConfiguration)
    )
