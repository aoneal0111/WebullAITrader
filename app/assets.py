from __future__ import annotations

from enum import StrEnum


class AssetType(StrEnum):
    """Explicit cross-domain asset identity; never inferred from a ticker."""

    EQUITY = "EQUITY"
    CRYPTO = "CRYPTO"
    FUTURES = "FUTURES"
    OPTIONS = "OPTIONS"


__all__ = ["AssetType"]
