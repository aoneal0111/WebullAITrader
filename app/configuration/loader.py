from __future__ import annotations

from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlparse

from app.broker_plugins import normalize_provider
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
    import os

    e = dict(os.environ if env is None else env)

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
    for section_name, section in (
        ("trading", trading_configuration),
        ("market-data", market_data_configuration),
    ):
        if urlparse(section.api_base_url).scheme != "https":
            raise ValueError(f"secure Webull {section_name} API endpoint is required")
        parse_webull_stream_url(section.stream_url)
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
        _symbols(
            e.get(
                "MARKET_DATA_SYMBOLS",
                e.get("ALLOWED_SYMBOLS", ""),
            )
        ),
        _int(e, "STREAM_RECONNECT_ATTEMPTS", 3),
        _decimal(e, "STREAM_RECONNECT_BACKOFF_SECONDS", "1"),
        trading_configuration,
        market_data_configuration,
    )


def _bool(v):
    if str(v).lower() not in ("true", "false"):
        raise ValueError("boolean setting is malformed")
    return str(v).lower() == "true"


def _int(e, k, d):
    v = int(e.get(k, d))
    if v <= 0 and k != "MAXIMUM_UNRESOLVED_MUTATIONS":
        raise ValueError(k + " must be positive")
    if v < 0:
        raise ValueError(k + " must be nonnegative")
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



