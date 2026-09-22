from __future__ import annotations

from decimal import Decimal, InvalidOperation
import math
import os
from pathlib import Path
from urllib.parse import urlparse

from app.broker_plugins import normalize_provider
from app.configuration.environment import (
    ResolvedSymbolIntelligenceSECEnvironment,
    resolve_runtime_environment,
    parse_symbol_intelligence_sec_manual_targets,
    resolve_symbol_intelligence_sec_environment,
)
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
    if env is None:
        process_environment = dict(os.environ)
        e = resolve_runtime_environment()
        symbol_intelligence_sec_environment = (
            resolve_symbol_intelligence_sec_environment()
        )
    else:
        process_environment = dict(env)
        e = dict(env)
        symbol_intelligence_sec_environment = (
            resolve_symbol_intelligence_sec_environment(e, dotenv_path=None)
        )
    symbol_intelligence_sec_configuration = (
        load_symbol_intelligence_sec_configuration(
            symbol_intelligence_sec_environment,
            manual_targets=parse_symbol_intelligence_sec_manual_targets(process_environment),
        )
    )

    mode = _scoped_environment(
        e,
        "WEBULL_TRADING_ENVIRONMENT",
        "TRADING_ENVIRONMENT",
        TradingEnvironment.TEST,
    )

    historical_enabled = _bool(
        e.get("ATLAS_HISTORICAL_ENTRY_EXPERIMENT_ENABLED", "false")
    )
    historical_mode_value = e.get("ATLAS_HISTORICAL_ENTRY_EXPERIMENT_MODE")
    if historical_enabled and not str(historical_mode_value or "").strip():
        raise ValueError(
            "ATLAS_HISTORICAL_ENTRY_EXPERIMENT_MODE must be explicitly set "
            "when historical entry experiment is enabled"
        )
    historical_mode = _historical_entry_mode(
        historical_mode_value or "OBSERVE_ONLY"
    )
    trading_environment = _scoped_environment(
        e, "WEBULL_TRADING_ENVIRONMENT", "TRADING_ENVIRONMENT", mode
    )
    if historical_enabled and historical_mode == "PAPER_TREATMENT":
        if trading_environment is not TradingEnvironment.PAPER:
            raise ValueError(
                "PAPER_TREATMENT requires WEBULL_TRADING_ENVIRONMENT=PAPER"
            )
        historical_path_value = e.get("ATLAS_HISTORICAL_ENTRY_EXPERIMENT_PATH")
        if not str(historical_path_value or "").strip():
            raise ValueError(
                "ATLAS_HISTORICAL_ENTRY_EXPERIMENT_PATH must be explicitly "
                "set for PAPER_TREATMENT"
            )
    historical_path = Path(
        e.get(
            "ATLAS_HISTORICAL_ENTRY_EXPERIMENT_PATH",
            "data/paper_trade_experiment.sqlite3",
        )
    ).resolve()

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
    paper_catalyst_news_enabled = (
        trading_environment is TradingEnvironment.PAPER
        and _bool(e.get("ATLAS_PAPER_CATALYST_NEWS_ENABLED", "true"))
    )
    paper_news_default = "true" if paper_catalyst_news_enabled else "false"
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
        if _bool(e.get("YAHOO_FINANCE_NEWS_ENABLED", paper_news_default))
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
        if _bool(e.get("CNBC_NEWS_ENABLED", paper_news_default))
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
        if _bool(e.get("MARKETWATCH_NEWS_ENABLED", paper_news_default))
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
        _positive_float(e, "MARKET_DATA_RECONNECT_AFTER_SECONDS", 10.0),
        _positive_float(e, "SUSPEND_GAP_DETECTION_SECONDS", 10.0),
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
        _bool(e.get("ATLAS_MEMORY_OBSERVABILITY_ENABLED", "false")),
        Path(
            e.get("ATLAS_MEMORY_OBSERVABILITY_PATH", "").strip()
            or "memory-observability.jsonl"
        ).resolve(),
        max(
            30.0,
            _positive_float(
                e, "ATLAS_MEMORY_OBSERVABILITY_INTERVAL_SECONDS", 60.0
            ),
        ),
        _bool(e.get("ATLAS_MEMORY_TRACEMALLOC_ENABLED", "false")),
        max(
            30.0,
            _positive_float(
                e,
                "ATLAS_MEMORY_TRACEMALLOC_SNAPSHOT_INTERVAL_SECONDS",
                600.0,
            ),
        ),
        _bool(e.get("ATLAS_MEMORY_GC_TRACKED_OBJECTS_ENABLED", "false")),
        _bool(e.get("CRYPTO_DISCOVERY_ENABLED", "false")),
        _symbols(e.get("CRYPTO_DISCOVERY_SYMBOLS", "")),
        _int(e, "CRYPTO_DISCOVERY_REFRESH_SECONDS", 60),
        _int(e, "CRYPTO_DISCOVERY_QUEUE_CAPACITY", 256),
        Path(
            e.get("CRYPTO_DISCOVERY_PATH", "").strip()
            or "crypto-research.jsonl"
        ).resolve(),
        _bool(e.get("CRYPTO_INTELLIGENCE_RESEARCH_ENABLED", "false")),
        _int(e, "CRYPTO_INTELLIGENCE_MAX_ACTIVE_DECISIONS", 4096),
        _int(e, "CRYPTO_INTELLIGENCE_HISTORY_MAX_SYMBOLS", 10),
        _int(e, "CRYPTO_INTELLIGENCE_HISTORY_BAR_COUNT", 64),
        _int(e, "CRYPTO_INTELLIGENCE_HISTORY_REQUEST_BUDGET", 20),
        _int(e, "CRYPTO_INTELLIGENCE_M1_REFRESH_SECONDS", 60),
        _int(e, "CRYPTO_INTELLIGENCE_M5_REFRESH_SECONDS", 60),
        _bool(e.get("CRYPTO_CATALYST_ACQUISITION_ENABLED", "false")),
        _bool(e.get("CRYPTO_CATALYST_SEC_ENABLED", "false")),
        _bool(e.get("CRYPTO_CATALYST_FEDERAL_REGISTER_ENABLED", "false")),
        _bool(e.get("CRYPTO_CATALYST_STATUSPAGE_ENABLED", "false")),
        _bool(e.get("CRYPTO_CATALYST_BYBIT_ENABLED", "false")),
        _int(e, "CRYPTO_CATALYST_SCHEDULER_TICK_SECONDS", 5),
        _int(e, "CRYPTO_CATALYST_SEC_CADENCE_SECONDS", 3600),
        _int(e, "CRYPTO_CATALYST_FEDERAL_REGISTER_CADENCE_SECONDS", 7200),
        _int(e, "CRYPTO_CATALYST_STATUSPAGE_CADENCE_SECONDS", 300),
        _int(e, "CRYPTO_CATALYST_BYBIT_CADENCE_SECONDS", 900),
        historical_entry_experiment_enabled=(
            historical_enabled
        ),
        historical_entry_experiment_mode=historical_mode,
        historical_entry_experiment_path=historical_path,
        symbol_intelligence_sec_edgar=symbol_intelligence_sec_configuration,
        nasdaq_trade_halts_enabled=_bool(
            e.get("NASDAQ_TRADE_HALTS_ENABLED", "false")
        ),
    )


def load_symbol_intelligence_sec_configuration(
    resolved: ResolvedSymbolIntelligenceSECEnvironment,
    *,
    manual_targets: tuple[str, ...] = (),
) -> SymbolIntelligenceSECEdgarConfiguration:
    """Build non-composed SEC settings without exposing the User-Agent."""

    if not isinstance(resolved, ResolvedSymbolIntelligenceSECEnvironment):
        raise TypeError("resolved SEC environment is required")
    values = dict(resolved.values)
    enabled_value = resolved.get("ATLAS_SEC_EDGAR_ENABLED")
    user_agent = str(
        resolved.get("ATLAS_SEC_EDGAR_USER_AGENT", "") or ""
    ).strip()
    if enabled_value is None:
        enabled = bool(user_agent) and resolved.origin(
            "ATLAS_SEC_EDGAR_USER_AGENT"
        ) in {"legacy_process", "legacy_dotenv"}
    else:
        enabled = _bool(enabled_value)
    diagnostics_root = str(
        resolved.get("ATLAS_SYMBOL_INTELLIGENCE_SEC_D3_DIAGNOSTICS_ROOT", "") or ""
    ).strip()
    diagnostics_session_id = str(
        resolved.get("ATLAS_SYMBOL_INTELLIGENCE_SEC_D3_DIAGNOSTICS_SESSION_ID", "") or ""
    ).strip()
    return SymbolIntelligenceSECEdgarConfiguration(
        enabled=enabled,
        user_agent=user_agent or None,
        requests_per_second=_float_setting(
            values, "ATLAS_SEC_EDGAR_REQUESTS_PER_SECOND", 2.0
        ),
        connect_timeout_seconds=_float_setting(
            values, "ATLAS_SEC_EDGAR_CONNECT_TIMEOUT_SECONDS", 3.0
        ),
        read_timeout_seconds=_float_setting(
            values, "ATLAS_SEC_EDGAR_READ_TIMEOUT_SECONDS", 10.0
        ),
        max_retries=_int(values, "ATLAS_SEC_EDGAR_MAX_RETRIES", 2),
        backoff_initial_seconds=_float_setting(
            values, "ATLAS_SEC_EDGAR_BACKOFF_INITIAL_SECONDS", 0.5
        ),
        backoff_max_seconds=_float_setting(
            values, "ATLAS_SEC_EDGAR_BACKOFF_MAX_SECONDS", 8.0
        ),
        failure_cooldown_seconds=_float_setting(
            values, "ATLAS_SEC_EDGAR_FAILURE_COOLDOWN_SECONDS", 60.0
        ),
        freshness_days=_int(values, "ATLAS_SEC_EDGAR_FRESHNESS_DAYS", 3),
        ticker_refresh_seconds=_float_setting(
            values, "ATLAS_SEC_EDGAR_TICKER_REFRESH_SECONDS", 86_400.0
        ),
        submissions_refresh_seconds=_float_setting(
            values, "ATLAS_SEC_EDGAR_SUBMISSIONS_REFRESH_SECONDS", 900.0
        ),
        max_ticker_entries=_int(
            values, "ATLAS_SEC_EDGAR_MAX_TICKER_ENTRIES", 25_000
        ),
        max_submissions_cache_entries=_int(
            values,
            "ATLAS_SEC_EDGAR_MAX_SUBMISSIONS_CACHE_ENTRIES",
            2_048,
        ),
        shadow_parity_enabled=_bool(
            resolved.get("ATLAS_SYMBOL_INTELLIGENCE_SEC_SHADOW_PARITY_ENABLED", "false")
        ),
        acquisition_enabled=_bool(
            resolved.get("ATLAS_SYMBOL_INTELLIGENCE_SEC_ACQUISITION_ENABLED", "false")
        ),
        dual_network_migration_enabled=_bool(
            resolved.get(
                "ATLAS_SYMBOL_INTELLIGENCE_SEC_DUAL_NETWORK_MIGRATION_ENABLED",
                "false",
            )
        ),
        manual_targets=manual_targets,
        d3_diagnostics_enabled=_bool(
            resolved.get(
                "ATLAS_SYMBOL_INTELLIGENCE_SEC_D3_DIAGNOSTICS_ENABLED",
                "false",
            )
        ),
        d3_diagnostics_root=(
            Path(diagnostics_root) if diagnostics_root else None
        ),
        d3_diagnostics_session_id=(
            diagnostics_session_id or None
        ),
    )


def _bool(v):
    if str(v).lower() not in ("true", "false"):
        raise ValueError("boolean setting is malformed")
    return str(v).lower() == "true"


def _float_setting(e, k, d):
    try:
        return float(e.get(k, d))
    except (TypeError, ValueError) as x:
        raise ValueError(k + " is malformed") from x


def _historical_entry_mode(value: str) -> str:
    mode = str(value).strip().upper()
    if mode not in {"DISABLED", "OBSERVE_ONLY", "PAPER_TREATMENT"}:
        raise ValueError("ATLAS_HISTORICAL_ENTRY_EXPERIMENT_MODE is malformed")
    return mode


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


