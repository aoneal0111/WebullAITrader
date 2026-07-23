from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol, runtime_checkable

from app.momentum_scanner.models import AssetClass
from app.universe.models import UniverseSymbol


class UniverseProviderError(RuntimeError):
    """Base universe-provider error."""


@runtime_checkable
class UniverseProvider(Protocol):
    def list_symbols(
        self,
        asset_class: AssetClass,
    ) -> tuple[UniverseSymbol, ...]:
        """Return symbols available for the asset class."""


class InMemoryUniverseProvider:
    def __init__(
        self,
        symbols: Iterable[UniverseSymbol] = (),
    ) -> None:
        self._symbols: dict[
            tuple[AssetClass, str],
            UniverseSymbol,
        ] = {}

        for item in symbols:
            self.put(item)

    def put(self, item: UniverseSymbol) -> None:
        self._symbols[
            (item.asset_class, item.symbol)
        ] = item

    def remove(
        self,
        symbol: str,
        asset_class: AssetClass,
    ) -> None:
        normalized = _normalize_symbol(symbol)
        self._symbols.pop(
            (asset_class, normalized),
            None,
        )

    def list_symbols(
        self,
        asset_class: AssetClass,
    ) -> tuple[UniverseSymbol, ...]:
        return tuple(
            sorted(
                (
                    item
                    for item in self._symbols.values()
                    if item.asset_class is asset_class
                ),
                key=lambda item: item.symbol,
            )
        )


class CompositeUniverseProvider:
    def __init__(
        self,
        providers: Iterable[UniverseProvider],
    ) -> None:
        self._providers = tuple(providers)

        if not self._providers:
            raise ValueError(
                "at least one universe provider is required"
            )

    def list_symbols(
        self,
        asset_class: AssetClass,
    ) -> tuple[UniverseSymbol, ...]:
        merged: dict[str, UniverseSymbol] = {}

        for provider in self._providers:
            for item in provider.list_symbols(asset_class):
                merged[item.symbol] = item

        return tuple(
            sorted(
                merged.values(),
                key=lambda item: item.symbol,
            )
        )


def _normalize_symbol(symbol: str) -> str:
    normalized = symbol.strip().upper()

    if not normalized:
        raise ValueError("symbol is required")

    return normalized
