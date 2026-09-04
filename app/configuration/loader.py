from __future__ import annotations

from decimal import Decimal, InvalidOperation
import math
from pathlib import Path
from urllib.parse import urlparse

from app.broker_plugins import normalize_provider
from app.configuration.environment import resolve_runtime_environment
from app.configuration.models import *
from app.webull.stream_endpoint import parse_webull_stream_url


def _env(values: dict[str, str], primary: str, legacy: str) -> str:
    value = values.get(primary, "").strip()
    if value:
        return value
    return values.get(legacy, "").strip()


def _scoped_environment(
    values: dict[str, str],
    primary: str,
    compatibility: str,
    fallback: TradingEnvironment,
) -> TradingEnvironment:
    value = (
        values.get(primary, "").strip()
        or values.get(compatibility, "").strip()
    )
    return TradingEnvironment(value.upper()) if value else fallback


def _reject_partial_scope(
    values: dict[str, str], *, scope: str, required: tuple[str, ...]
) -> None:
    present = tuple(name for name in required if values.get(name, "").strip())
    if present and len(present) != len(required):
        missing = sorted(set(required) - set(present))
        raise ValueError(
            f"ambiguous mixed {scope} configuration; missing scoped settings: "
            + ",".join(missing)
        )



def load_configuration(env=None):
    e = resolve_runtime_environment() if env is None else dict(env)

    mode = _scoped_environment(
        e,
        "WEBULL_TRADING_ENVIRONMENT",
        "TRADING_ENVIRONMENT",
        TradingEnvironment.TEST,
    )

    provider = normalize_provider(
        e.get("BROKER_PROVIDER", "webull")
    )

    live = _bool(
        e.get("LIVE_TRADING_ENABLED", "false")
    )

    required = (
        "WEBULL_ACCOUNT_ID",
        "WEBULL_API_KEY",
        "WEBULL_API_SECRET",
        "WEBULL_API_BASE_URL",
        "WEBULL_STREAM_URL",
        "AUTHORIZATION_DATABASE_PATH",
        "EXECUTION_DATABASE_PATH",
        "MARKET_EVENT_DATABASE_PATH",
        "EMERGENCY_STOP_DATABASE_PATH",
        "MAX_ORDER_NOTIONAL",
        "MAX_DAILY_NOTIONAL",
        "MAX_OPEN_POSITIONS",
        "MAX_OPEN_ORDERS",
        "MAX_ORDER_RATE",
        "MAX_QUANTITY_PER_SYMBOL",
        "ALLOWED_SYMBOLS",
    )

    if mode is TradingEnvironment.LIVE:
        missing = [
            k for k in required
            if not e.get(k, " ").strip()
        ]
        if missing:
            raise ValueError(
                "missing required live settings: "
                + ",".join(sorted(missing))
            )

        if not live:
            raise ValueError(
                "LIVE_TRADING_ENABLED=true is required for live mode"
            )

    api = e.get(
        "WEBULL_API_BASE_URL",
        "https://api.sandbox.webull.com",
    )

    stream = e.get(
        "WEBULL_STREAM_URL",
        "wss://data-api.sandbox.webull.com:8883/mqtt",
    )

    if urlparse(api).scheme != "https":
        raise ValueError(
            "secure Webull API endpoint is required"
        )
    parse_webull_stream_url(stream)

    paths = tuple(
        Path(
            e.get(k, f"data/{k.lower()}.sqlite3")
        ).resolve()
        for k in (
            "AUTHORIZATION_DATABASE_PATH",
            "EXECUTION_DATABASE_PATH",
            "MARKET_EVENT_DATABASE_PATH",
            "EMERGENCY_STOP_DATABASE_PATH",
        )
    )

    if mode is TradingEnvironment.LIVE:
        import tempfile

        temp = Path(tempfile.gettempdir()).resolve()

        if any(
            p == temp or temp in p.parents
            for p in paths
        ):
            raise ValueError(
                "live database paths must not use temporary storage"
            )


    _reject_partial_scope(
        e,
        scope="trading",
        required=(
            "WEBULL_TRADING_ACCOUNT_ID",
            "WEBULL_TRADING_APP_KEY",
            "WEBULL_TRADING_APP_SECRET",
            "WEBULL_TRADING_API_BASE_URL",
            "WEBULL_TRADING_STREAM_URL",
        ),
    )
    _reject_partial_scope(
        e,
        scope="market-data",
        required=(
            "WEBULL_MARKET_DATA_APP_KEY",
            "WEBULL_MARKET_DATA_APP_SECRET",
            "WEBULL_MARKET_DATA_API_BASE_URL",
            "WEBULL_MARKET_DATA_STREAM_URL",
        ),
    )

    trading_configuration = TradingConfiguration(
        environment=_scoped_environment(
            e, "WEBULL_TRADING_ENVIRONMENT", "TRADING_ENVIRONMENT", mode
        ),
        account_id=_env(e,"WEBULL_TRADING_ACCOUNT_ID","WEBULL_ACCOUNT_ID"),
        api_key=_env(e,"WEBULL_TRADING_APP_KEY","WEBULL_API_KEY"),
        api_secret=_env(e,"WEBULL_TRADING_APP_SECRET","WEBULL_API_SECRET"),
        api_base_url=_env(e,"WEBULL_TRADING_API_BASE_URL","WEBULL_API_BASE_URL") or api,
        stream_url=_env(e,"WEBULL_TRADING_STREAM_URL","WEBULL_STREAM_URL") or stream,
    )

    market_data_configuration = MarketDataConfiguration(
        environment=_scoped_environment(
            e,
            "WEBULL_MARKET_DATA_ENVIRONMENT",
            "MARKET_DATA_ENVIRONMENT",
            mode,
        ),
        api_key=_env(e,"WEBULL_MARKET_DATA_APP_KEY","WEBULL_API_KEY"),
        api_secret=_env(e,"WEBULL_MARKET_DATA_APP_SECRET","WEBULL_API_SECRET"),
        api_base_url=_env(e,"WEBULL_MARKET_DATA_API_BASE_URL","WEBULL_API_BASE_URL") or api,
        stream_url=_env(e,"WEBULL_MARKET_DATA_STREAM_URL","WEBULL_STREAM_URL") or stream,
    )
    sec_user_agent = e.get("SEC_EDGAR_USER_AGENT", "").strip()
    sec_edgar_configuration = (
        SECEdgarConfiguration(
            user_agent=sec_user_agent,
            freshness_days=_non_negative_int(e, "SEC_EDGAR_FRESHNESS_DAYS", 3),
            timeout_seconds=_positive_float(e, "SEC_EDGAR_TIMEOUT_SECONDS", 10.0),
        )
        if sec_user_agent
        else None
    )
    yahoo_finance_news_configuration = (
        YahooFinanceNewsConfiguration(
            freshness_minutes=_non_negative_int(
                e, "YAHOO_FINANCE_NEWS_FRESHNESS_MINUTES", 1_440
            ),
            timeout_seconds=_positive_float(
                e, "YAHOO_FINANCE_TIMEOUT_SECONDS", 5.0
            ),
            cache_ttl_seconds=_positive_float(
                e, "YAHOO_FINANCE_NEWS_CACHE_TTL_SECONDS", 300.0
            ),
        )
        if _bool(e.get("YAHOO_FINANCE_NEWS_ENABLED", "false"))
        else None
    )
    cnbc_news_configuration = (
        CNBCNewsConfiguration(
            freshness_minutes=_non_negative_int(
                e, "CNBC_NEWS_FRESHNESS_MINUTES", 1_440
            ),
            timeout_seconds=_positive_float(
                e, "CNBC_NEWS_TIMEOUT_SECONDS", 5.0
            ),
            refresh_ttl_seconds=_positive_float(
                e, "CNBC_NEWS_REFRESH_TTL_SECONDS", 3_600.0
            ),
            failure_cooldown_seconds=_positive_float(
                e, "CNBC_NEWS_FAILURE_COOLDOWN_SECONDS", 60.0
            ),
            maximum_snapshot_age_seconds=_positive_float(
                e, "CNBC_NEWS_MAXIMUM_SNAPSHOT_AGE_SECONDS", 7_200.0
            ),
            max_items=_int(e, "CNBC_NEWS_MAX_ITEMS", 512),
            max_payload_bytes=_int(e, "CNBC_NEWS_MAX_PAYLOAD_BYTES", 1_000_000),
        )
        if _bool(e.get("CNBC_NEWS_ENABLED", "false"))
        else None
    )
    marketwatch_news_configuration = (
        MarketWatchNewsConfiguration(
            freshness_minutes=_non_negative_int(
                e, "MARKETWATCH_NEWS_FRESHNESS_MINUTES", 1_440
            ),
            timeout_seconds=_positive_float(
                e, "MARKETWATCH_NEWS_TIMEOUT_SECONDS", 5.0
            ),
            refresh_ttl_seconds=_positive_float(
                e, "MARKETWATCH_NEWS_REFRESH_TTL_SECONDS", 3_600.0
            ),
            failure_cooldown_seconds=_positive_float(
                e, "MARKETWATCH_NEWS_FAILURE_COOLDOWN_SECONDS", 300.0
            ),
            maximum_snapshot_age_seconds=_positive_float(
                e, "MARKETWATCH_NEWS_MAXIMUM_SNAPSHOT_AGE_SECONDS", 7_200.0
            ),
            max_items=_int(e, "MARKETWATCH_NEWS_MAX_ITEMS", 256),
            max_payload_bytes=_int(
                e, "MARKETWATCH_NEWS_MAX_PAYLOAD_BYTES", 250_000
            ),
        )
        if _bool(e.get("MARKETWATCH_NEWS_ENABLED", "false"))
        else None
    )
    for section_name, section in (
        ("trading", trading_configuration),
        ("market-data", market_data_configuration),
    ):
        if urlparse(section.api_base_url).scheme != "https":
            raise ValueError(f"secure Webull {section_name} API endpoint is required")
        parse_webull_stream_url(section.stream_url)
    paper_symbol_authorization_mode = PaperSymbolAuthorizationMode(
        e.get(
            "PAPER_SYMBOL_AUTHORIZATION_MODE",
            PaperSymbolAuthorizationMode.STATIC_ALLOWLIST.value,
        ).strip().upper()
    )
    if (
        paper_symbol_authorization_mode
        is PaperSymbolAuthorizationMode.DYNAMIC_WARRIOR
        and (
            live
            or trading_configuration.environment
            not in {
                TradingEnvironment.TEST,
                TradingEnvironment.PAPER,
                TradingEnvironment.SANDBOX,
            }
        )
    ):
        raise ValueError(
            "dynamic Warrior PAPER symbol authorization requires a non-live "
            "TEST/PAPER/SANDBOX trading environment"
        )
    return OperationalConfiguration(
        mode,
        provider,
        e.get("WEBULL_ACCOUNT_ID", ""),
        e.get("WEBULL_API_KEY", ""),
        e.get("WEBULL_API_SECRET", ""),
        api,
        stream,
        *paths,
        e.get("LOG_LEVEL", "INFO").upper(),
        _int(e, "HEALTH_PORT", 8080),
        live,
        _decimal(e, "MAX_ORDER_NOTIONAL", "10"),
        _decimal(e, "MAX_DAILY_NOTIONAL", "50"),
        _int(e, "MAX_OPEN_POSITIONS", 1),
        _int(e, "MAX_OPEN_ORDERS", 1),
        _int(e, "MAX_ORDER_RATE", 5),
        _decimal(e, "MAX_QUANTITY_PER_SYMBOL", "1"),
        _symbols(e.get("ALLOWED_SYMBOLS", "")),
        _symbols(e.get("BLOCKED_SYMBOLS", "")),
        _int(e, "MAXIMUM_MARKET_DATA_AGE_SECONDS", 5),
        _int(e, "RECONCILIATION_INTERVAL_SECONDS", 30),
        _int(e, "MAXIMUM_RECONCILIATION_AGE_SECONDS", 60),
        _int(e, "MAXIMUM_UNRESOLVED_MUTATIONS", 0),
        _bool(e.get("MARKET_DATA_STREAMING_ENABLED", "true")),
        _symbols(e.get("MARKET_DATA_SYMBOLS", "")),
        _int(e, "STREAM_RECONNECT_ATTEMPTS", 3),
        _decimal(e, "STREAM_RECONNECT_BACKOFF_SECONDS", "1"),
        trading_configuration,
        market_data_configuration,
        _bool(e.get("WARRIOR_FORWARD_PAPER_ENABLED", "false")),
        Path(e.get(
            "WARRIOR_FORWARD_CAPTURE_PATH",
            "data/warrior_momentum_v1_forward/forward_capture.sqlite3",
        )).resolve(),
        sec_edgar_configuration,
        yahoo_finance_news_configuration,
        cnbc_news_configuration,
        marketwatch_news_configuration,
        _bool(e.get("TRADE_INTELLIGENCE_ENABLED", "true")),
        Path(e.get(
            "TRADE_INTELLIGENCE_PATH",
            "data/atlas_learning/experiences.sqlite3",
        )).resolve(),
        _int(e, "TRADE_INTELLIGENCE_QUEUE_CAPACITY", 4096),
        paper_symbol_authorization_mode,
        _bool(e.get("ENTRY_OPPORTUNITY_VALUE_ENABLED", "false")),
        Path(
            e.get("ENTRY_OPPORTUNITY_VALUE_PATH", "").strip()
            or "data/entry_opportunity_value/observations.jsonl"
        ).resolve(),
        _int(e, "ENTRY_OPPORTUNITY_VALUE_QUEUE_CAPACITY", 1024),
        _bool(e.get("ADAPTIVE_ENTRY_RESEARCH_ENABLED", "false")),
        Path(
            e.get("ADAPTIVE_ENTRY_RESEARCH_PATH", "").strip()
            or "data/adaptive_entry_research/recommendations.jsonl"
        ).resolve(),
        _int(e, "ADAPTIVE_ENTRY_RESEARCH_QUEUE_CAPACITY", 512),
        _bool(e.get("SCANNER_UNIVERSE_OBSERVABILITY_ENABLED", "false")),
        Path(
            e.get("SCANNER_UNIVERSE_OBSERVABILITY_PATH", "").strip()
            or "data/scanner_universe_observability/events.jsonl"
        ).resolve(),
        _int(e, "SCANNER_UNIVERSE_OBSERVABILITY_QUEUE_CAPACITY", 4096),
        _bool(e.get("DYNAMIC_MOMENTUM_DISCOVERY_ENABLED", "false")),
        Path(
            e.get("DYNAMIC_MOMENTUM_DISCOVERY_PATH", "").strip()
            or "data/dynamic_momentum_discovery/observations.jsonl"
        ).resolve(),
        _int(e, "DYNAMIC_MOMENTUM_DISCOVERY_QUEUE_CAPACITY", 1024),
        _int(e, "DYNAMIC_MOMENTUM_DISCOVERY_BREADTH", 100),
        _int(e, "DYNAMIC_MOMENTUM_DISCOVERY_REFRESH_SECONDS", 60),
    )


def _bool(v):
    if str(v).lower() not in ("true", "false"):
        raise ValueError("boolean setting is malformed")
    return str(v).lower() == "true"


def _int(e, k, d):
    v = int(e.get(k, d))
    if v <= 0 and k != "MAXIMUM_UNRESOLVED_MUTATIONS":
        raise ValueError(k + " must be positive")
    return v


def _non_negative_int(e, k, d):
    v = int(e.get(k, d))
    if v < 0:
        raise ValueError(k + " must not be negative")
    return v


def _positive_float(e, k, d):
    v = float(e.get(k, d))
    if not math.isfinite(v) or v <= 0:
        raise ValueError(k + " must be positive")
    return v


def _decimal(e, k, d):
    try:
        v = Decimal(e.get(k, d))
    except InvalidOperation as x:
        raise ValueError(k + " is malformed") from x
    if not v.is_finite() or v <= 0:
        raise ValueError(k + " must be positive")
    return v


def _symbols(v):
    return tuple(
        sorted(
            {
                x.strip().upper()
                for x in v.split(",")
                if x.strip()
            }
        )
    )



