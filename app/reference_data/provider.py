from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol, runtime_checkable

from app.momentum_scanner.models import AssetClass
from app.reference_data.models import ReferenceRecord


class ReferenceDataError(RuntimeError):
    """Base exception raised by reference-data components."""


class ReferenceDataNotFoundError(
    LookupError,
    ReferenceDataError,
):
    """Raised when no reference record exists for a symbol."""


class ReferenceDataProviderUnavailableError(ReferenceDataError):
    """Raised when a provider cannot currently serve data."""


@runtime_checkable
class ReferenceDataProvider(Protocol):
    def get_reference_data(
        self,
        symbol: str,
        asset_class: AssetClass,
    ) -> ReferenceRecord:
        """Return reference data for one symbol."""


class InMemoryReferenceDataProvider:
    def __init__(
        self,
        records: Iterable[ReferenceRecord] = (),
    ) -> None:
        self._records: dict[
            tuple[AssetClass, str],
            ReferenceRecord,
        ] = {}

        for record in records:
            self.put(record)

    def put(self, record: ReferenceRecord) -> None:
        key = (record.asset_class, record.symbol)
        self._records[key] = record

    def remove(
        self,
        symbol: str,
        asset_class: AssetClass,
    ) -> None:
        key = (asset_class, _normalize_symbol(symbol))
        self._records.pop(key, None)

    def get_reference_data(
        self,
        symbol: str,
        asset_class: AssetClass,
    ) -> ReferenceRecord:
        key = (asset_class, _normalize_symbol(symbol))

        try:
            return self._records[key]
        except KeyError as exc:
            raise ReferenceDataNotFoundError(
                f"reference data not found for "
                f"{asset_class.value}:{key[1]}"
            ) from exc


class CompositeReferenceDataProvider:
    """Tries providers in order until one returns a record."""

    def __init__(
        self,
        providers: Iterable[ReferenceDataProvider],
    ) -> None:
        self._providers = tuple(providers)

        if not self._providers:
            raise ValueError("at least one provider is required")

    def get_reference_data(
        self,
        symbol: str,
        asset_class: AssetClass,
    ) -> ReferenceRecord:
        errors: list[Exception] = []

        for provider in self._providers:
            try:
                return provider.get_reference_data(
                    symbol,
                    asset_class,
                )
            except (
                ReferenceDataNotFoundError,
                ReferenceDataProviderUnavailableError,
            ) as exc:
                errors.append(exc)

        details = "; ".join(str(error) for error in errors)

        raise ReferenceDataNotFoundError(
            f"no provider returned reference data for "
            f"{asset_class.value}:{_normalize_symbol(symbol)}"
            + (f": {details}" if details else "")
        )


def _normalize_symbol(symbol: str) -> str:
    normalized = symbol.strip().upper()

    if not normalized:
        raise ValueError("symbol is required")

    return normalized
