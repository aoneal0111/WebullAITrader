"""Deterministic startup validation across isolated Webull runtimes."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.configuration.models import TradingConfiguration
from app.webull.credential_identity import credential_fingerprint
from app.webull.market_data_probe import MarketDataProbeResult


class TradingProbeState(StrEnum):
    CONNECTED = "CONNECTED"
    AUTH_FAILED = "AUTH_FAILED"
    OK = "OK"
    FAILED = "FAILED"
    ENABLED = "ENABLED"
    DISABLED = "DISABLED"
    NOT_TESTED = "NOT_TESTED"


@dataclass(frozen=True, slots=True)
class TradingProbeResult:
    environment: str
    fingerprint: str
    authentication: TradingProbeState
    account: TradingProbeState
    buying_power: TradingProbeState
    positions: TradingProbeState
    paper_trading: TradingProbeState

    @property
    def ready(self) -> bool:
        return (
            self.authentication is TradingProbeState.CONNECTED
            and self.account is TradingProbeState.CONNECTED
            and self.buying_power is TradingProbeState.OK
            and self.positions is TradingProbeState.OK
            and self.paper_trading is TradingProbeState.ENABLED
        )


@dataclass(frozen=True, slots=True)
class StartupValidationResult:
    trading: TradingProbeResult
    market_data: MarketDataProbeResult

    @property
    def scanner_ready(self) -> bool:
        return self.trading.ready and self.market_data.scanner_ready

    @property
    def reason(self) -> str | None:
        if not self.trading.ready:
            return "Trading startup validation failed; scanner remains disabled."
        return self.market_data.reason


class RuntimeStartupValidator:
    """Run trading first, then the independent market-data probe."""

    def __init__(
        self,
        broker: object,
        trading: TradingConfiguration,
        market_data_probe: object,
    ) -> None:
        self._broker = broker
        self._trading = trading
        self._market_data_probe = market_data_probe

    def run(self) -> StartupValidationResult:
        trading = self._validate_trading()
        market_data = self._market_data_probe.run()
        return StartupValidationResult(trading, market_data)

    def _validate_trading(self) -> TradingProbeResult:
        cfg = self._trading
        fingerprint = credential_fingerprint(cfg.api_key, cfg.api_secret)
        not_tested = TradingProbeState.NOT_TESTED
        try:
            account = self._broker.get_account()
        except Exception:
            return TradingProbeResult(
                cfg.environment.value,
                fingerprint,
                TradingProbeState.AUTH_FAILED,
                TradingProbeState.AUTH_FAILED,
                not_tested,
                not_tested,
                not_tested,
            )

        account_ok = (
            str(getattr(account, "status", "")).strip().upper()
            not in {"", "CLOSED", "DISABLED", "INACTIVE"}
        )
        try:
            cash = self._broker.get_cash()
            buying_power = getattr(cash, "buying_power", None)
            buying_power_ok = buying_power is not None and buying_power >= 0
        except Exception:
            buying_power_ok = False
        try:
            positions = self._broker.get_positions()
            positions_ok = isinstance(positions, tuple)
        except Exception:
            positions_ok = False

        paper_enabled = cfg.environment.value in {"TEST", "PAPER", "SANDBOX"}
        return TradingProbeResult(
            cfg.environment.value,
            fingerprint,
            TradingProbeState.CONNECTED,
            TradingProbeState.CONNECTED if account_ok else TradingProbeState.FAILED,
            TradingProbeState.OK if buying_power_ok else TradingProbeState.FAILED,
            TradingProbeState.OK if positions_ok else TradingProbeState.FAILED,
            TradingProbeState.ENABLED if paper_enabled else TradingProbeState.DISABLED,
        )


__all__ = [
    "RuntimeStartupValidator",
    "StartupValidationResult",
    "TradingProbeResult",
    "TradingProbeState",
]
