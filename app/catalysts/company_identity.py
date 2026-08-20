from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
import re
from typing import Protocol, runtime_checkable
import unicodedata


_SYMBOL = re.compile(r"^[A-Z0-9][A-Z0-9-]{0,19}$")
_HEADLINE_SYMBOL_TOKEN = re.compile(
    r"(?<![A-Za-z0-9])[A-Za-z0-9]+(?:[./-][A-Za-z0-9]+)*(?![A-Za-z0-9])"
)
_COMPANY_SUFFIXES = {
    "co",
    "company",
    "corp",
    "corporation",
    "inc",
    "incorporated",
    "limited",
    "llc",
    "ltd",
    "plc",
}
_UNSAFE_ALIASES = {
    "company",
    "corporation",
    "group",
    "holdings",
    "international",
    "limited",
    "technologies",
    "technology",
}
_AMBIGUOUS_TICKER_WORDS = {
    "A",
    "AI",
    "ALL",
    "ARE",
    "CAN",
    "CEO",
    "CFO",
    "FOR",
    "I",
    "IT",
    "ON",
}
_EVENT_VERB = re.compile(
    r"\b(?:reports?|results?|beats?|misses?|announces?|posts?|raises?|cuts?|lowers?|reaffirms?|issues?|"
    r"updates?|wins?|awarded|receives?|secures?|signs?|enters?|forms?|files?|"
    r"declares?|increases?|launches?|acquires?|merges?|completes?|closes?)\b"
)


@dataclass(frozen=True, slots=True)
class CompanyIdentity:
    """Normalized symbol and explicitly supplied headline-safe aliases."""

    symbol: str
    aliases: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        symbol = normalize_symbol(self.symbol)
        if symbol is None:
            raise ValueError("company identity symbol is malformed")
        aliases = tuple(
            sorted(
                {
                    normalized
                    for value in self.aliases
                    if (normalized := normalize_company_text(value))
                },
                key=lambda value: (-len(value.split()), -len(value), value),
            )
        )
        object.__setattr__(self, "symbol", symbol)
        object.__setattr__(self, "aliases", aliases)


@runtime_checkable
class CompanyIdentityResolver(Protocol):
    def resolve(self, symbol: str) -> CompanyIdentity:
        """Return independently maintained identity data for one symbol."""


class CompanyIdentityRegistry:
    """Immutable identity registry that drops unsafe or multiply-owned aliases."""

    def __init__(
        self,
        identities: Iterable[CompanyIdentity] = (),
        *,
        unsafe_aliases: Iterable[str] = _UNSAFE_ALIASES,
    ) -> None:
        supplied = tuple(identities)
        symbols = [identity.symbol for identity in supplied]
        if len(set(symbols)) != len(symbols):
            raise ValueError("company identity symbols must be unique")
        denied = {
            normalized
            for value in unsafe_aliases
            if (normalized := normalize_company_text(value))
        }
        owners: dict[str, set[str]] = {}
        for identity in supplied:
            for alias in identity.aliases:
                owners.setdefault(alias, set()).add(identity.symbol)
        self._identities: Mapping[str, CompanyIdentity] = {
            identity.symbol: CompanyIdentity(
                identity.symbol,
                tuple(
                    alias
                    for alias in identity.aliases
                    if alias not in denied and owners.get(alias) == {identity.symbol}
                ),
            )
            for identity in supplied
        }

    def resolve(self, symbol: str) -> CompanyIdentity:
        normalized = normalize_symbol(symbol)
        if normalized is None:
            raise ValueError("symbol is malformed")
        return self._identities.get(normalized, CompanyIdentity(normalized))


def company_identity_from_names(
    symbol: str,
    names: Iterable[str],
) -> CompanyIdentity:
    """Build aliases using the legacy Yahoo name-shortening semantics."""

    aliases: set[str] = set()
    for value in names:
        normalized = normalize_company_text(value)
        if not normalized:
            continue
        aliases.add(normalized)
        words = normalized.split()
        while words and words[-1] in _COMPANY_SUFFIXES:
            words.pop()
        shortened = " ".join(words)
        if len(shortened) >= 4:
            aliases.add(shortened)
    return CompanyIdentity(symbol, tuple(aliases))


def headline_names_subject(headline: str, identity: CompanyIdentity) -> bool:
    """Require a ticker token or a normalized whole company alias in the title."""

    for token in _HEADLINE_SYMBOL_TOKEN.findall(str(headline)):
        if normalize_symbol(token) == identity.symbol:
            return True
    normalized_headline = normalize_company_text(headline)
    padded_headline = f" {normalized_headline} "
    return any(f" {alias} " in padded_headline for alias in identity.aliases)


def headline_names_direct_subject(headline: str, identity: CompanyIdentity) -> bool:
    """Apply stricter title-subject positioning for unassociated feed stories.

    Exact ticker signals must be visibly ticker-like. Company aliases normally
    must occur in the leading grammatical subject, while a small set of event
    constructions permits a company named as the regulatory/deal counterparty.
    """

    raw_headline = str(headline)
    for token in _HEADLINE_SYMBOL_TOKEN.findall(raw_headline):
        if (
            token == token.upper()
            and any(character.isalpha() for character in token)
            and normalize_symbol(token) == identity.symbol
            and identity.symbol not in _AMBIGUOUS_TICKER_WORDS
        ):
            return True
    normalized_headline = normalize_company_text(raw_headline)
    event = _EVENT_VERB.search(normalized_headline)
    leading = normalized_headline if event is None else normalized_headline[: event.start()]
    padded_leading = f" {leading} "
    for alias in identity.aliases:
        if f" {alias} " in padded_leading:
            return True
        escaped = re.escape(alias)
        counterparty_patterns = (
            rf"\bfda\b.*\b(?:approves?|clears?|authorizes?|rejects?|denies?|declines?)\b(?: [a-z0-9]+){{0,4}} \b{escaped}\b",
            rf"\b(?:acquires?|buys?|merges? with|partners? with|collaborates? with)\b(?: [a-z0-9]+){{0,2}} \b{escaped}\b",
            rf"\b(?:contract|purchase order|award)\b.*\b(?:awarded|signed|secured|given) to\b(?: [a-z0-9]+){{0,2}} \b{escaped}\b",
        )
        if any(re.search(pattern, normalized_headline) for pattern in counterparty_patterns):
            return True
    return False


def normalize_company_text(value: object) -> str:
    ascii_value = unicodedata.normalize("NFKD", str(value)).encode(
        "ascii", "ignore"
    ).decode("ascii")
    ascii_value = ascii_value.casefold().replace("&", " and ")
    return " ".join(re.sub(r"[^a-z0-9]+", " ", ascii_value).split())


def normalize_symbol(value: object) -> str | None:
    normalized = str(value).strip().upper().replace(".", "-").replace("/", "-")
    return normalized if _SYMBOL.fullmatch(normalized) else None


__all__ = [
    "CompanyIdentity",
    "CompanyIdentityRegistry",
    "CompanyIdentityResolver",
    "company_identity_from_names",
    "headline_names_subject",
    "headline_names_direct_subject",
    "normalize_company_text",
    "normalize_symbol",
]
