"""Fail-fast identity assertions and safe Webull request diagnostics."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import StrEnum
from threading import RLock

from app.configuration.models import MarketDataConfiguration, TradingConfiguration
from app.webull.credential_identity import credential_fingerprint


class RequestService(StrEnum):
    TRADING = "TRADING"
    MARKET_DATA = "MARKET_DATA"


class RequestIsolationError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class RequestIdentity:
    service: RequestService
    environment: str
    fingerprint: str


@dataclass(frozen=True, slots=True)
class RequestAuditRecord:
    service: str
    environment: str
    fingerprint: str
    endpoint: str
    capability_result: str


class RequestIsolationGuard:
    def __init__(
        self,
        trading: TradingConfiguration,
        market_data: MarketDataConfiguration,
    ) -> None:
        self._expected = {
            RequestService.TRADING: RequestIdentity(
                RequestService.TRADING,
                trading.environment.value,
                credential_fingerprint(trading.api_key, trading.api_secret),
            ),
            RequestService.MARKET_DATA: RequestIdentity(
                RequestService.MARKET_DATA,
                market_data.environment.value,
                credential_fingerprint(market_data.api_key, market_data.api_secret),
            ),
        }
        trade = self._expected[RequestService.TRADING]
        data = self._expected[RequestService.MARKET_DATA]
        if trade.environment != data.environment and trade.fingerprint == data.fingerprint:
            raise RequestIsolationError(
                "cross-environment Webull services require distinct identities"
            )
        self._records: list[RequestAuditRecord] = []
        self._lock = RLock()
        self._logger = logging.getLogger("atlas.webull.request_audit")

    def identity(self, service: RequestService) -> RequestIdentity:
        return self._expected[service]

    def record(
        self,
        identity: RequestIdentity,
        *,
        endpoint: str,
        capability_result: str,
    ) -> RequestAuditRecord:
        expected = self._expected.get(identity.service)
        if expected != identity:
            raise RequestIsolationError(
                f"{identity.service.value} request identity does not match its runtime"
            )
        record = RequestAuditRecord(
            identity.service.value,
            identity.environment,
            identity.fingerprint,
            endpoint,
            capability_result,
        )
        with self._lock:
            self._records.append(record)
        self._logger.info(
            "service=%s environment=%s fingerprint=%s endpoint=%s capability_result=%s",
            record.service,
            record.environment,
            record.fingerprint,
            record.endpoint,
            record.capability_result,
        )
        return record

    @property
    def records(self) -> tuple[RequestAuditRecord, ...]:
        with self._lock:
            return tuple(self._records)


class AuditedMarketDataClient:
    """Proxy official DataClient namespaces through the isolation guard."""

    def __init__(
        self,
        client: object,
        guard: RequestIsolationGuard,
        configuration: MarketDataConfiguration,
    ) -> None:
        self._client = client
        self._guard = guard
        self._identity = guard.identity(RequestService.MARKET_DATA)
        self._endpoint = configuration.api_base_url.rstrip("/")

    def __getattr__(self, name: str):
        value = getattr(self._client, name)
        if name in {"market_data", "instrument", "screener", "fundamentals"}:
            return _AuditedNamespace(
                value, self._guard, self._identity, self._endpoint, name
            )
        return value


class _AuditedNamespace:
    def __init__(self, target, guard, identity, endpoint, namespace) -> None:
        self._target = target
        self._guard = guard
        self._identity = identity
        self._endpoint = endpoint
        self._namespace = namespace

    def __getattr__(self, name: str):
        value = getattr(self._target, name)
        if not callable(value):
            return value

        def audited(*args, **kwargs):
            endpoint = f"{self._endpoint}/{self._namespace}/{name}"
            self._guard.record(
                self._identity, endpoint=endpoint, capability_result="REQUESTED"
            )
            try:
                result = value(*args, **kwargs)
            except Exception:
                self._guard.record(
                    self._identity, endpoint=endpoint, capability_result="FAILED"
                )
                raise
            self._guard.record(
                self._identity, endpoint=endpoint, capability_result="SUCCEEDED"
            )
            return result

        return audited


__all__ = [
    "AuditedMarketDataClient",
    "RequestAuditRecord",
    "RequestIdentity",
    "RequestIsolationError",
    "RequestIsolationGuard",
    "RequestService",
]
