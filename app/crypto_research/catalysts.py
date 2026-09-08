"""Provider-neutral, research-only crypto catalyst identity and evidence contracts."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol, runtime_checkable

from app.assets import AssetType
from app.research_core import EvidenceProvenance, semantic_digest


SCHEMA_VERSION = "crypto-catalyst-v1"
MAX_PROJECTS = 512
MAX_EVENTS_PER_PROJECT = 32
MAX_CANONICAL_EVENTS = 4096
MAX_REVISION_IDENTITIES = 8192
MAX_NETWORKS = 256
MAX_TOKEN_IDENTITIES = 4096
MAX_CONTRACT_IDENTITIES = 8192
MAX_PROVIDER_ASSET_REFERENCES = 16384
MAX_ALIASES_PER_IDENTITY = 32


class CryptoCatalystType(StrEnum):
    EXCHANGE_LISTING = "EXCHANGE_LISTING"
    EXCHANGE_DELISTING = "EXCHANGE_DELISTING"
    PROTOCOL_UPGRADE = "PROTOCOL_UPGRADE"
    HARD_FORK = "HARD_FORK"
    TOKEN_UNLOCK = "TOKEN_UNLOCK"
    EMISSIONS_CHANGE = "EMISSIONS_CHANGE"
    SECURITY_EXPLOIT = "SECURITY_EXPLOIT"
    SECURITY_PATCH = "SECURITY_PATCH"
    NETWORK_OUTAGE = "NETWORK_OUTAGE"
    NETWORK_RECOVERY = "NETWORK_RECOVERY"
    NETWORK_CONGESTION = "NETWORK_CONGESTION"
    GOVERNANCE_PROPOSAL = "GOVERNANCE_PROPOSAL"
    GOVERNANCE_VOTE = "GOVERNANCE_VOTE"
    GOVERNANCE_RESULT = "GOVERNANCE_RESULT"
    REGULATORY_ACTION = "REGULATORY_ACTION"
    REGULATORY_APPROVAL = "REGULATORY_APPROVAL"
    REGULATORY_RESTRICTION = "REGULATORY_RESTRICTION"
    ETF_FILING = "ETF_FILING"
    ETF_APPROVAL = "ETF_APPROVAL"
    ETF_REJECTION = "ETF_REJECTION"
    INSTITUTIONAL_FLOW_EVENT = "INSTITUTIONAL_FLOW_EVENT"
    STABLECOIN_DEPEG = "STABLECOIN_DEPEG"
    STABLECOIN_RECOVERY = "STABLECOIN_RECOVERY"
    PARTNERSHIP = "PARTNERSHIP"
    ECOSYSTEM_ANNOUNCEMENT = "ECOSYSTEM_ANNOUNCEMENT"
    TREASURY_EVENT = "TREASURY_EVENT"
    FUNDING_EVENT = "FUNDING_EVENT"
    MACRO_EVENT = "MACRO_EVENT"
    PROJECT_ANNOUNCEMENT = "PROJECT_ANNOUNCEMENT"
    UNKNOWN = "UNKNOWN"
    OTHER = "OTHER"


class CryptoCatalystDirection(StrEnum):
    POSITIVE = "POSITIVE"
    NEGATIVE = "NEGATIVE"
    MIXED = "MIXED"
    UNKNOWN = "UNKNOWN"


class CryptoCatalystStatus(StrEnum):
    ANNOUNCED = "ANNOUNCED"
    SCHEDULED = "SCHEDULED"
    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    DELAYED = "DELAYED"
    RESOLVED = "RESOLVED"
    REVISED = "REVISED"
    UNVERIFIED = "UNVERIFIED"
    UNKNOWN = "UNKNOWN"


class CryptoAssociationConfidence(StrEnum):
    EXACT = "EXACT"
    VERIFIED_ALIAS = "VERIFIED_ALIAS"
    PROVIDER_MAPPED = "PROVIDER_MAPPED"
    MULTI_ASSET = "MULTI_ASSET"
    AMBIGUOUS = "AMBIGUOUS"
    UNRESOLVED = "UNRESOLVED"


class CryptoAssociationType(StrEnum):
    PROJECT = "PROJECT"
    TOKEN = "TOKEN"
    NETWORK = "NETWORK"
    PAIR = "PAIR"
    EXCHANGE = "EXCHANGE"


class CryptoFreshness(StrEnum):
    FRESH = "FRESH"
    STALE = "STALE"
    UNAVAILABLE = "UNAVAILABLE"


class CryptoAssetIdentityType(StrEnum):
    NATIVE_ASSET = "NATIVE_ASSET"
    CONTRACT_TOKEN = "CONTRACT_TOKEN"


class CryptoAssetRelationship(StrEnum):
    WRAPPED_FROM = "WRAPPED_FROM"
    BRIDGED_FROM = "BRIDGED_FROM"
    MIGRATED_FROM = "MIGRATED_FROM"
    MAPS_TO_PAIR = "MAPS_TO_PAIR"


def _bounded_symbols(values: Iterable[str], name: str = "aliases") -> tuple[str, ...]:
    result = _symbols(values)
    if len(result) > MAX_ALIASES_PER_IDENTITY:
        raise ValueError(f"{name} exceeds bounded identity limit")
    return result


def _contract(value: str, name: str = "contract_address") -> str:
    result = _text(value, name)
    # EVM addresses are case-insensitive for identity matching.  Preserve
    # non-EVM address syntax instead of imposing a fake EVM checksum/format.
    if result.lower().startswith("0x") and len(result) == 42:
        return result.lower()
    return result


@dataclass(frozen=True, slots=True)
class CryptoNetworkIdentity:
    network_id: str
    canonical_name: str
    network_type: str
    native_asset_symbol: str | None = None
    chain_id: str | int | None = None
    genesis_identity: str | None = None
    aliases: tuple[str, ...] = ()
    provider_references: tuple[tuple[str, str], ...] = ()
    identity_version: str = "crypto-network-v1"

    def __post_init__(self) -> None:
        object.__setattr__(self, "network_id", _text(self.network_id, "network_id"))
        object.__setattr__(self, "canonical_name", _text(self.canonical_name, "canonical_name"))
        object.__setattr__(self, "network_type", _text(self.network_type, "network_type"))
        object.__setattr__(self, "native_asset_symbol", self.native_asset_symbol.strip().upper() if self.native_asset_symbol else None)
        object.__setattr__(self, "aliases", _bounded_symbols(self.aliases))
        refs = tuple(sorted((str(k).strip(), str(v).strip()) for k, v in self.provider_references if str(k).strip() and str(v).strip()))
        object.__setattr__(self, "provider_references", refs)
        object.__setattr__(self, "identity_version", _text(self.identity_version, "identity_version"))


@dataclass(frozen=True, slots=True)
class CryptoHistoricalSymbol:
    symbol: str
    valid_from: datetime | None = None
    valid_to: datetime | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", _text(self.symbol, "symbol").upper())
        start = _aware(self.valid_from, "valid_from")
        end = _aware(self.valid_to, "valid_to")
        if start and end and end < start:
            raise ValueError("valid_to must not precede valid_from")
        object.__setattr__(self, "valid_from", start)
        object.__setattr__(self, "valid_to", end)

    def valid_at(self, cutoff: datetime | None) -> bool:
        if cutoff is None:
            return True
        point = _aware(cutoff, "cutoff", required=True)
        return (self.valid_from is None or self.valid_from <= point) and (self.valid_to is None or point < self.valid_to)


@dataclass(frozen=True, slots=True)
class CryptoProviderAssetReference:
    provider_id: str
    provider_asset_id: str
    provider_symbol: str | None = None
    provider_slug: str | None = None
    provider_contract_reference: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "provider_id", _text(self.provider_id, "provider_id"))
        object.__setattr__(self, "provider_asset_id", _text(self.provider_asset_id, "provider_asset_id"))
        object.__setattr__(self, "provider_symbol", self.provider_symbol.strip().upper() if self.provider_symbol else None)
        object.__setattr__(self, "provider_slug", self.provider_slug.strip() if self.provider_slug else None)
        object.__setattr__(self, "provider_contract_reference", self.provider_contract_reference.strip() if self.provider_contract_reference else None)


@dataclass(frozen=True, slots=True)
class CryptoNativeAssetIdentity:
    asset_id: str
    network_id: str
    symbol: str
    canonical_name: str
    project_id: str | None = None
    historical_symbols: tuple[CryptoHistoricalSymbol, ...] = ()
    provider_references: tuple[CryptoProviderAssetReference, ...] = ()
    identity_version: str = "crypto-native-asset-v1"

    identity_type: CryptoAssetIdentityType = CryptoAssetIdentityType.NATIVE_ASSET

    def __post_init__(self) -> None:
        object.__setattr__(self, "asset_id", _text(self.asset_id, "asset_id"))
        object.__setattr__(self, "network_id", _text(self.network_id, "network_id"))
        object.__setattr__(self, "symbol", _text(self.symbol, "symbol").upper())
        object.__setattr__(self, "canonical_name", _text(self.canonical_name, "canonical_name"))
        object.__setattr__(self, "historical_symbols", tuple(self.historical_symbols))
        object.__setattr__(self, "provider_references", tuple(self.provider_references))
        object.__setattr__(self, "identity_version", _text(self.identity_version, "identity_version"))

    def symbol_matches(self, value: str, cutoff: datetime | None = None) -> bool:
        query = str(value).strip().upper()
        return query == self.symbol or any(item.symbol == query and item.valid_at(cutoff) for item in self.historical_symbols)


@dataclass(frozen=True, slots=True)
class CryptoContractTokenIdentity:
    asset_id: str
    network_id: str
    contract_address: str
    symbol: str
    canonical_name: str
    project_id: str | None = None
    decimals: int | None = None
    historical_symbols: tuple[CryptoHistoricalSymbol, ...] = ()
    provider_references: tuple[CryptoProviderAssetReference, ...] = ()
    identity_version: str = "crypto-contract-token-v1"

    identity_type: CryptoAssetIdentityType = CryptoAssetIdentityType.CONTRACT_TOKEN

    def __post_init__(self) -> None:
        object.__setattr__(self, "asset_id", _text(self.asset_id, "asset_id"))
        object.__setattr__(self, "network_id", _text(self.network_id, "network_id"))
        object.__setattr__(self, "contract_address", _contract(self.contract_address))
        object.__setattr__(self, "symbol", _text(self.symbol, "symbol").upper())
        object.__setattr__(self, "canonical_name", _text(self.canonical_name, "canonical_name"))
        if self.decimals is not None and self.decimals < 0:
            raise ValueError("decimals must be non-negative")
        object.__setattr__(self, "historical_symbols", tuple(self.historical_symbols))
        object.__setattr__(self, "provider_references", tuple(self.provider_references))
        object.__setattr__(self, "identity_version", _text(self.identity_version, "identity_version"))

    def symbol_matches(self, value: str, cutoff: datetime | None = None) -> bool:
        query = str(value).strip().upper()
        return query == self.symbol or any(item.symbol == query and item.valid_at(cutoff) for item in self.historical_symbols)


@dataclass(frozen=True, slots=True)
class CryptoWrappedAssetRelation:
    wrapped_asset_id: str
    underlying_asset_id: str
    wrapping_network_id: str
    contract_address: str | None = None
    relationship_type: CryptoAssetRelationship = CryptoAssetRelationship.WRAPPED_FROM


@dataclass(frozen=True, slots=True)
class CryptoBridgedAssetRelation:
    bridged_asset_id: str
    origin_asset_id: str
    origin_network_id: str
    destination_network_id: str
    destination_contract_address: str | None = None
    bridge_provider: str | None = None
    relationship_type: CryptoAssetRelationship = CryptoAssetRelationship.BRIDGED_FROM


@dataclass(frozen=True, slots=True)
class CryptoTokenMigration:
    old_asset_id: str
    new_asset_id: str
    effective_at: datetime
    supersedes: str
    reason: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "effective_at", _aware(self.effective_at, "effective_at", required=True))
        object.__setattr__(self, "supersedes", _text(self.supersedes, "supersedes"))
        object.__setattr__(self, "reason", _text(self.reason, "reason"))


@dataclass(frozen=True, slots=True)
class CryptoAssetLookup:
    query: str
    confidence: CryptoAssociationConfidence
    asset_ids: tuple[str, ...] = ()
    reason: str = ""

    @property
    def ambiguous(self) -> bool:
        return self.confidence in {CryptoAssociationConfidence.AMBIGUOUS, CryptoAssociationConfidence.UNRESOLVED}


@dataclass(frozen=True, slots=True)
class CryptoIdentityMetrics:
    projects: int
    networks: int
    tokens: int
    contracts: int
    provider_refs: int
    aliases: int
    ambiguous_resolutions: int
    unresolved_resolutions: int
    conflicts: int
    revisions: int
    evictions: int = 0


def _text(value: object, name: str) -> str:
    result = str(value).strip()
    if not result:
        raise ValueError(f"{name} is required")
    return result


def _aware(value: datetime | None, name: str, *, required: bool = False) -> datetime | None:
    if value is None:
        if required:
            raise ValueError(f"{name} is required")
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


def _symbols(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(sorted({str(value).strip().upper() for value in values if str(value).strip()}))


@dataclass(frozen=True, slots=True)
class CryptoProjectIdentity:
    project_id: str
    canonical_project_name: str
    canonical_token_symbol: str | None = None
    canonical_token_name: str | None = None
    aliases: tuple[str, ...] = ()
    historical_symbols: tuple[str, ...] = ()
    network: str | None = None
    provider_references: tuple[tuple[str, str], ...] = ()
    identity_version: str = "crypto-project-v1"
    network_ids: tuple[str, ...] = ()
    token_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "project_id", _text(self.project_id, "project_id"))
        object.__setattr__(self, "canonical_project_name", _text(self.canonical_project_name, "canonical_project_name"))
        object.__setattr__(self, "canonical_token_symbol", self.canonical_token_symbol.strip().upper() if self.canonical_token_symbol else None)
        object.__setattr__(self, "canonical_token_name", self.canonical_token_name.strip() if self.canonical_token_name else None)
        object.__setattr__(self, "aliases", _symbols(self.aliases))
        object.__setattr__(self, "historical_symbols", _symbols(self.historical_symbols))
        object.__setattr__(self, "network", self.network.strip() if self.network else None)
        refs = tuple(sorted((str(key).strip(), str(value).strip()) for key, value in self.provider_references if str(key).strip() and str(value).strip()))
        object.__setattr__(self, "provider_references", refs)
        object.__setattr__(self, "identity_version", _text(self.identity_version, "identity_version"))
        object.__setattr__(self, "network_ids", _symbols(self.network_ids))
        object.__setattr__(self, "token_ids", _symbols(self.token_ids))

    @property
    def identity(self) -> str:
        return semantic_digest("crypto-project", self.project_id, self.identity_version)


@dataclass(frozen=True, slots=True)
class CryptoPairIdentity:
    canonical_pair: str
    provider_symbol: str
    instrument_id: str | None = None
    base_token_symbol: str | None = None
    quote_token_symbol: str | None = None
    base_asset_id: str | None = None
    quote_asset_id: str | None = None
    identity_version: str = "crypto-pair-v1"

    def __post_init__(self) -> None:
        pair = _text(self.canonical_pair, "canonical_pair").upper()
        if pair.count("/") != 1 or any(not part for part in pair.split("/")):
            raise ValueError("canonical_pair must be BASE/QUOTE")
        object.__setattr__(self, "canonical_pair", pair)
        object.__setattr__(self, "provider_symbol", _text(self.provider_symbol, "provider_symbol").upper())
        object.__setattr__(self, "instrument_id", self.instrument_id.strip() if self.instrument_id else None)
        base, quote = pair.split("/")
        object.__setattr__(self, "base_token_symbol", (self.base_token_symbol or base).strip().upper())
        object.__setattr__(self, "quote_token_symbol", (self.quote_token_symbol or quote).strip().upper())
        object.__setattr__(self, "base_asset_id", self.base_asset_id.strip() if self.base_asset_id else None)
        object.__setattr__(self, "quote_asset_id", self.quote_asset_id.strip() if self.quote_asset_id else None)
        object.__setattr__(self, "identity_version", _text(self.identity_version, "identity_version"))


@dataclass(frozen=True, slots=True)
class CryptoPairAssociation:
    pair: CryptoPairIdentity
    association_type: CryptoAssociationType
    confidence: CryptoAssociationConfidence
    reason: str
    identity_version: str = "crypto-association-v1"

    def __post_init__(self) -> None:
        object.__setattr__(self, "reason", _text(self.reason, "association reason"))
        object.__setattr__(self, "identity_version", _text(self.identity_version, "identity_version"))


@dataclass(frozen=True, slots=True)
class CryptoIdentityLookup:
    query: str
    confidence: CryptoAssociationConfidence
    matches: tuple[CryptoProjectIdentity, ...] = ()

    @property
    def ambiguous(self) -> bool:
        return self.confidence in {CryptoAssociationConfidence.AMBIGUOUS, CryptoAssociationConfidence.UNRESOLVED}


class CryptoProjectRegistry:
    """Bounded immutable project registry; collisions never silently resolve."""

    def __init__(self, identities: Iterable[CryptoProjectIdentity] = (), *, maximum_projects: int = MAX_PROJECTS) -> None:
        if maximum_projects <= 0:
            raise ValueError("maximum_projects must be positive")
        supplied = tuple(identities)
        if len(supplied) > maximum_projects:
            raise ValueError("project registry bound exceeded")
        if len({item.project_id for item in supplied}) != len(supplied):
            raise ValueError("project_id values must be unique")
        self.maximum_projects = maximum_projects
        self._identities = tuple(sorted(supplied, key=lambda item: item.project_id))

    @property
    def identities(self) -> tuple[CryptoProjectIdentity, ...]:
        return self._identities

    def lookup(self, value: str) -> CryptoIdentityLookup:
        query = str(value).strip().upper()
        matches = tuple(item for item in self._identities if query in {item.project_id.upper(), item.canonical_token_symbol or "", *item.aliases, *item.historical_symbols})
        if not matches:
            return CryptoIdentityLookup(query, CryptoAssociationConfidence.UNRESOLVED)
        if len(matches) > 1:
            return CryptoIdentityLookup(query, CryptoAssociationConfidence.AMBIGUOUS, matches)
        item = matches[0]
        confidence = CryptoAssociationConfidence.EXACT if query == item.project_id.upper() else CryptoAssociationConfidence.VERIFIED_ALIAS
        return CryptoIdentityLookup(query, confidence, (item,))

    def add(self, identity: CryptoProjectIdentity) -> "CryptoProjectRegistry":
        return CryptoProjectRegistry((*self._identities, identity), maximum_projects=self.maximum_projects)


class CryptoAssetIdentityRegistry:
    """Bounded immutable identity graph; weak identifiers never override strong ones."""

    def __init__(
        self,
        *,
        projects: Iterable[CryptoProjectIdentity] = (),
        networks: Iterable[CryptoNetworkIdentity] = (),
        native_assets: Iterable[CryptoNativeAssetIdentity] = (),
        contract_tokens: Iterable[CryptoContractTokenIdentity] = (),
        provider_references: Iterable[CryptoProviderAssetReference] = (),
        wrapped_relations: Iterable[CryptoWrappedAssetRelation] = (),
        bridged_relations: Iterable[CryptoBridgedAssetRelation] = (),
        migrations: Iterable[CryptoTokenMigration] = (),
        pairs: Iterable[CryptoPairIdentity] = (),
        maximum_projects: int = MAX_PROJECTS,
        maximum_networks: int = MAX_NETWORKS,
        maximum_tokens: int = MAX_TOKEN_IDENTITIES,
        maximum_contracts: int = MAX_CONTRACT_IDENTITIES,
        maximum_provider_references: int = MAX_PROVIDER_ASSET_REFERENCES,
        maximum_pairs: int = MAX_TOKEN_IDENTITIES,
    ) -> None:
        self.maximum_projects = maximum_projects
        self.maximum_networks = maximum_networks
        self.maximum_tokens = maximum_tokens
        self.maximum_contracts = maximum_contracts
        self.maximum_provider_references = maximum_provider_references
        self.maximum_pairs = maximum_pairs
        self.projects = tuple(sorted(projects, key=lambda item: item.project_id))
        self.networks = tuple(sorted(networks, key=lambda item: item.network_id))
        self.native_assets = tuple(sorted(native_assets, key=lambda item: item.asset_id))
        self.contract_tokens = tuple(sorted(contract_tokens, key=lambda item: item.asset_id))
        self.provider_references = tuple(sorted(provider_references, key=lambda item: (item.provider_id, item.provider_asset_id)))
        self.wrapped_relations = tuple(wrapped_relations)
        self.bridged_relations = tuple(bridged_relations)
        self.migrations = tuple(sorted(migrations, key=lambda item: (item.effective_at, item.old_asset_id, item.new_asset_id)))
        self.pairs = tuple(sorted(pairs, key=lambda item: item.canonical_pair))
        self._validate_bounds()

    def _validate_bounds(self) -> None:
        if len(self.projects) > self.maximum_projects or len(self.networks) > self.maximum_networks:
            raise ValueError("identity registry bound exceeded")
        if len(self.native_assets) + len(self.contract_tokens) > self.maximum_tokens:
            raise ValueError("token identity bound exceeded")
        if len(self.contract_tokens) > self.maximum_contracts:
            raise ValueError("contract identity bound exceeded")
        if len(self.provider_references) > self.maximum_provider_references:
            raise ValueError("provider reference bound exceeded")
        if len(self.pairs) > self.maximum_pairs:
            raise ValueError("pair identity bound exceeded")
        if len({item.network_id for item in self.networks}) != len(self.networks):
            raise ValueError("network_id values must be unique")
        asset_ids = [item.asset_id for item in (*self.native_assets, *self.contract_tokens)]
        if len(set(asset_ids)) != len(asset_ids):
            raise ValueError("asset_id values must be unique")
        addresses = [(item.network_id, item.contract_address) for item in self.contract_tokens]
        if len(set(addresses)) != len(addresses):
            raise ValueError("network and contract address values must be unique")

    @property
    def assets(self) -> tuple[CryptoNativeAssetIdentity | CryptoContractTokenIdentity, ...]:
        return (*self.native_assets, *self.contract_tokens)

    def with_asset(self, asset: CryptoNativeAssetIdentity | CryptoContractTokenIdentity) -> "CryptoAssetIdentityRegistry":
        native = tuple(item for item in self.native_assets if item.asset_id != asset.asset_id)
        contracts = tuple(item for item in self.contract_tokens if item.asset_id != asset.asset_id)
        if asset.identity_type is CryptoAssetIdentityType.NATIVE_ASSET:
            native += (asset,)  # type: ignore[assignment]
        else:
            contracts += (asset,)  # type: ignore[assignment]
        return CryptoAssetIdentityRegistry(
            projects=self.projects, networks=self.networks, native_assets=native,
            contract_tokens=contracts, provider_references=self.provider_references,
            wrapped_relations=self.wrapped_relations, bridged_relations=self.bridged_relations,
            migrations=self.migrations, maximum_projects=self.maximum_projects,
            maximum_networks=self.maximum_networks, maximum_tokens=self.maximum_tokens,
            maximum_contracts=self.maximum_contracts, maximum_provider_references=self.maximum_provider_references,
            pairs=self.pairs, maximum_pairs=self.maximum_pairs,
        )

    def with_network(self, network: CryptoNetworkIdentity) -> "CryptoAssetIdentityRegistry":
        values = tuple(item for item in self.networks if item.network_id != network.network_id) + (network,)
        return CryptoAssetIdentityRegistry(
            projects=self.projects, networks=values, native_assets=self.native_assets,
            contract_tokens=self.contract_tokens, provider_references=self.provider_references,
            wrapped_relations=self.wrapped_relations, bridged_relations=self.bridged_relations,
            migrations=self.migrations, maximum_projects=self.maximum_projects,
            maximum_networks=self.maximum_networks, maximum_tokens=self.maximum_tokens,
            maximum_contracts=self.maximum_contracts, maximum_provider_references=self.maximum_provider_references,
            pairs=self.pairs, maximum_pairs=self.maximum_pairs,
        )

    def network_lookup(self, network_id: str) -> CryptoNetworkIdentity | None:
        query = str(network_id).strip()
        return next((item for item in self.networks if item.network_id == query or query.upper() in item.aliases), None)

    def chain_lookup(self, chain_id: str | int) -> tuple[CryptoNetworkIdentity, ...]:
        query = str(chain_id)
        return tuple(item for item in self.networks if item.chain_id is not None and str(item.chain_id) == query)

    def project_lookup(self, project_id: str) -> CryptoProjectIdentity | None:
        query = str(project_id).strip()
        return next((item for item in self.projects if item.project_id == query), None)

    def pair_lookup(self, canonical_pair: str) -> tuple[CryptoPairIdentity, ...]:
        query = str(canonical_pair).strip().upper()
        return tuple(item for item in self.pairs if item.canonical_pair == query)

    @property
    def metrics(self) -> CryptoIdentityMetrics:
        aliases = sum(len(item.aliases) for item in self.networks)
        aliases += sum(len(item.historical_symbols) for item in self.assets)
        return CryptoIdentityMetrics(
            projects=len(self.projects), networks=len(self.networks),
            tokens=len(self.assets), contracts=len(self.contract_tokens),
            provider_refs=len(self.provider_references), aliases=aliases,
            ambiguous_resolutions=0, unresolved_resolutions=0,
            conflicts=0, revisions=len(self.migrations), evictions=0,
        )

    def contract_lookup(self, network_id: str, contract_address: str) -> CryptoContractTokenIdentity | None:
        normalized = _contract(contract_address)
        return next((item for item in self.contract_tokens if item.network_id == network_id and item.contract_address == normalized), None)

    def provider_lookup(self, provider_id: str, provider_asset_id: str, *, symbol: str | None = None) -> CryptoAssetLookup:
        refs = [item for item in self.provider_references if item.provider_id == provider_id and item.provider_asset_id == provider_asset_id]
        candidates = [asset.asset_id for asset in self.assets if any(ref in asset.provider_references for ref in refs)]
        if len(candidates) == 1:
            return CryptoAssetLookup(provider_asset_id, CryptoAssociationConfidence.PROVIDER_MAPPED, tuple(candidates), "provider asset reference")
        if len(candidates) > 1:
            return CryptoAssetLookup(provider_asset_id, CryptoAssociationConfidence.AMBIGUOUS, tuple(sorted(candidates)), "provider reference collision")
        if symbol:
            return self.resolve(symbol=symbol)
        return CryptoAssetLookup(provider_asset_id, CryptoAssociationConfidence.UNRESOLVED, (), "provider reference is not verified")

    def resolve(
        self, *, symbol: str | None = None, network_id: str | None = None,
        contract_address: str | None = None, provider_id: str | None = None,
        provider_asset_id: str | None = None, cutoff: datetime | None = None,
    ) -> CryptoAssetLookup:
        if contract_address is not None:
            if network_id is None:
                return CryptoAssetLookup(contract_address, CryptoAssociationConfidence.AMBIGUOUS, (), "contract requires network identity")
            item = self.contract_lookup(network_id, contract_address)
            if item is None:
                return CryptoAssetLookup(contract_address, CryptoAssociationConfidence.UNRESOLVED, (), "contract identity not registered")
            if symbol and not item.symbol_matches(symbol, cutoff):
                return CryptoAssetLookup(contract_address, CryptoAssociationConfidence.AMBIGUOUS, (item.asset_id,), "strong contract identity conflicts with weak symbol")
            return CryptoAssetLookup(contract_address, CryptoAssociationConfidence.EXACT, (item.asset_id,), "network and contract address")
        if provider_id and provider_asset_id:
            mapped = self.provider_lookup(provider_id, provider_asset_id, symbol=symbol)
            if mapped.confidence is not CryptoAssociationConfidence.UNRESOLVED:
                return mapped
        if symbol is None:
            return CryptoAssetLookup("", CryptoAssociationConfidence.UNRESOLVED, (), "no identity evidence")
        query = symbol.strip().upper()
        matches = [item for item in self.assets if (network_id is None or item.network_id == network_id) and item.symbol_matches(query, cutoff)]
        if len(matches) == 1:
            return CryptoAssetLookup(query, CryptoAssociationConfidence.VERIFIED_ALIAS if query != matches[0].symbol else CryptoAssociationConfidence.EXACT, (matches[0].asset_id,), "verified symbol")
        if len(matches) > 1:
            return CryptoAssetLookup(query, CryptoAssociationConfidence.AMBIGUOUS, tuple(sorted(item.asset_id for item in matches)), "symbol collision")
        return CryptoAssetLookup(query, CryptoAssociationConfidence.UNRESOLVED, (), "symbol is not verified")

    def asset_at_cutoff(self, asset_id: str, cutoff: datetime) -> str:
        """Return the identity valid at cutoff without rewriting historical identities."""
        point = _aware(cutoff, "cutoff", required=True)
        current = _text(asset_id, "asset_id")
        changed = True
        while changed:
            changed = False
            for migration in self.migrations:
                if migration.old_asset_id == current and migration.effective_at <= point:
                    current = migration.new_asset_id
                    changed = True
                    break
        return current

    def with_relationships(
        self, *, wrapped: Iterable[CryptoWrappedAssetRelation] = (),
        bridged: Iterable[CryptoBridgedAssetRelation] = (),
        migrations: Iterable[CryptoTokenMigration] = (),
    ) -> "CryptoAssetIdentityRegistry":
        return CryptoAssetIdentityRegistry(
            projects=self.projects, networks=self.networks, native_assets=self.native_assets,
            contract_tokens=self.contract_tokens, provider_references=self.provider_references,
            wrapped_relations=wrapped, bridged_relations=bridged, migrations=migrations,
            maximum_projects=self.maximum_projects, maximum_networks=self.maximum_networks,
            maximum_tokens=self.maximum_tokens, maximum_contracts=self.maximum_contracts,
            maximum_provider_references=self.maximum_provider_references,
            pairs=self.pairs, maximum_pairs=self.maximum_pairs,
        )


def bootstrap_pair_identity(canonical_pair: str, provider_symbol: str, instrument_id: str | None = None) -> CryptoPairIdentity:
    """Bootstrap pair metadata from Webull discovery without claiming project identity."""

    return CryptoPairIdentity(canonical_pair, provider_symbol, instrument_id)


@dataclass(frozen=True, slots=True)
class CryptoCatalystEvidence:
    event_type: CryptoCatalystType
    status: CryptoCatalystStatus
    provider_id: str
    source_name: str
    source_reference: str
    published_at: datetime
    observed_at: datetime
    effective_at: datetime | None = None
    project: CryptoProjectIdentity | None = None
    token_symbol: str | None = None
    associated_pairs: tuple[CryptoPairAssociation, ...] = ()
    association_confidence: CryptoAssociationConfidence = CryptoAssociationConfidence.UNRESOLVED
    expected_direction: CryptoCatalystDirection = CryptoCatalystDirection.UNKNOWN
    title: str = ""
    summary: str | None = None
    source_url: str | None = None
    provider_event_id: str | None = None
    underlying_event_key: str | None = None
    revision: int = 1
    supersedes: str | None = None
    provenance: EvidenceProvenance | None = None
    decision_cutoff: datetime | None = None
    freshness_policy_seconds: int | None = None
    schema_version: str = SCHEMA_VERSION
    research_only: bool = True

    def __post_init__(self) -> None:
        if not self.research_only:
            raise ValueError("crypto catalyst evidence must remain research-only")
        object.__setattr__(self, "provider_id", _text(self.provider_id, "provider_id"))
        object.__setattr__(self, "source_name", _text(self.source_name, "source_name"))
        object.__setattr__(self, "source_reference", _text(self.source_reference, "source_reference"))
        object.__setattr__(self, "title", str(self.title).strip())
        object.__setattr__(self, "summary", self.summary.strip() if self.summary else None)
        object.__setattr__(self, "source_url", self.source_url.strip() if self.source_url else None)
        object.__setattr__(self, "token_symbol", self.token_symbol.strip().upper() if self.token_symbol else None)
        object.__setattr__(self, "provider_event_id", self.provider_event_id.strip() if self.provider_event_id else None)
        object.__setattr__(self, "underlying_event_key", self.underlying_event_key.strip() if self.underlying_event_key else None)
        object.__setattr__(self, "published_at", _aware(self.published_at, "published_at", required=True))
        object.__setattr__(self, "observed_at", _aware(self.observed_at, "observed_at", required=True))
        object.__setattr__(self, "effective_at", _aware(self.effective_at, "effective_at"))
        cutoff = _aware(self.decision_cutoff, "decision_cutoff")
        object.__setattr__(self, "decision_cutoff", cutoff)
        if self.observed_at < self.published_at:
            raise ValueError("observed_at cannot precede published_at")
        if self.revision <= 0:
            raise ValueError("revision must be positive")
        if self.freshness_policy_seconds is not None and self.freshness_policy_seconds <= 0:
            raise ValueError("freshness policy must be positive")
        if self.provenance is not None and self.provenance.observed_at > self.observed_at:
            raise ValueError("provenance cannot be observed after evidence")
        if cutoff is not None and not self.eligible_at(cutoff):
            raise ValueError("evidence is not eligible at its decision_cutoff")

    @property
    def event_identity(self) -> str:
        stable_key = self.underlying_event_key or self.provider_event_id or self.title.casefold()
        project_key = self.project.project_id if self.project else (self.token_symbol or "UNRESOLVED")
        effective = self.effective_at.isoformat() if self.effective_at else ""
        return semantic_digest("crypto-event", project_key, self.event_type.value, stable_key, self.published_at.isoformat(), effective)

    @property
    def revision_identity(self) -> str:
        return semantic_digest("crypto-event-revision", self.event_identity, self.revision, self.status.value, self.effective_at, self.association_confidence.value)

    @property
    def evidence_identity(self) -> str:
        # A provider's syndicated copies are one observation even when URLs differ;
        # independent providers remain distinct corroborating evidence.
        return semantic_digest("crypto-evidence", self.event_identity, self.revision_identity, self.provider_id)

    def eligible_at(self, cutoff: datetime) -> bool:
        boundary = _aware(cutoff, "decision_cutoff", required=True)
        return self.published_at <= boundary and self.observed_at <= boundary

    def freshness_at(self, cutoff: datetime) -> CryptoFreshness:
        boundary = _aware(cutoff, "decision_cutoff", required=True)
        if not self.eligible_at(boundary):
            return CryptoFreshness.UNAVAILABLE
        if self.freshness_policy_seconds is None:
            return CryptoFreshness.FRESH
        age = (boundary - self.published_at).total_seconds()
        return CryptoFreshness.FRESH if age <= self.freshness_policy_seconds else CryptoFreshness.STALE

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version, "event_identity": self.event_identity,
            "revision_identity": self.revision_identity, "event_type": self.event_type.value,
            "status": self.status.value, "provider_id": self.provider_id, "source_name": self.source_name,
            "source_reference": self.source_reference, "source_url": self.source_url,
            "provider_event_id": self.provider_event_id, "project_id": self.project.project_id if self.project else None,
            "token_symbol": self.token_symbol, "associated_pairs": [item.pair.canonical_pair for item in self.associated_pairs],
            "association_confidence": self.association_confidence.value, "expected_direction": self.expected_direction.value,
            "title": self.title, "summary": self.summary, "published_at": self.published_at.isoformat(),
            "effective_at": self.effective_at.isoformat() if self.effective_at else None, "observed_at": self.observed_at.isoformat(),
            "revision": self.revision, "supersedes": self.supersedes, "research_only": True,
            "production_promoted": False, "selection_authorized": False, "execution_authorized": False,
        }


@dataclass(frozen=True, slots=True)
class CryptoCatalystEvent:
    identity: str
    revisions: tuple[CryptoCatalystEvidence, ...]

    @property
    def latest(self) -> CryptoCatalystEvidence:
        return max(self.revisions, key=lambda item: (item.revision, item.observed_at, item.revision_identity))

    @property
    def source_count(self) -> int:
        return len({item.source_reference for item in self.revisions})

    @property
    def independent_provider_count(self) -> int:
        return len({item.provider_id for item in self.revisions})

    @property
    def corroborated(self) -> bool:
        return self.independent_provider_count >= 2


@dataclass(frozen=True, slots=True)
class CryptoCatalystMetrics:
    crypto_catalyst_projects: int
    crypto_catalyst_events_retained: int
    crypto_catalyst_event_high_water: int
    crypto_catalyst_events_evicted: int
    crypto_catalyst_ambiguous: int
    crypto_catalyst_unresolved: int
    crypto_catalyst_corroborated: int
    crypto_catalyst_revisions: int
    crypto_catalyst_provider_failures: int
    crypto_catalyst_duplicates_suppressed: int


@dataclass(frozen=True, slots=True)
class CryptoCatalystAggregation:
    events: tuple[CryptoCatalystEvent, ...]
    rejected_future: int
    material_changes: tuple[str, ...]
    metrics: CryptoCatalystMetrics


@dataclass(frozen=True, slots=True)
class CryptoCatalystSummary:
    events_by_type: tuple[tuple[str, int], ...]
    events_by_project: tuple[tuple[str, int], ...]
    fresh_count: int
    stale_count: int
    ambiguous_count: int
    unresolved_count: int
    corroborated_count: int
    revision_count: int
    provider_failures: int


@runtime_checkable
class CryptoCatalystProvider(Protocol):
    @property
    def provider_id(self) -> str: ...

    def collect(self, as_of: datetime) -> Iterable[CryptoCatalystEvidence]: ...


class CryptoCatalystAggregator:
    """Bounded provider-neutral aggregation; no network or execution behavior."""

    def __init__(self, *, maximum_events_per_project: int = MAX_EVENTS_PER_PROJECT, maximum_canonical_events: int = MAX_CANONICAL_EVENTS, maximum_revision_identities: int = MAX_REVISION_IDENTITIES) -> None:
        if min(maximum_events_per_project, maximum_canonical_events, maximum_revision_identities) <= 0:
            raise ValueError("catalyst bounds must be positive")
        self.maximum_events_per_project = maximum_events_per_project
        self.maximum_canonical_events = maximum_canonical_events
        self.maximum_revision_identities = maximum_revision_identities
        self._events: OrderedDict[str, list[CryptoCatalystEvidence]] = OrderedDict()
        self._revisions: OrderedDict[str, None] = OrderedDict()
        self._signatures: OrderedDict[str, None] = OrderedDict()
        self._evicted = 0
        self._high_water = 0
        self._failures = 0
        self._duplicates = 0

    def aggregate(self, evidence: Iterable[CryptoCatalystEvidence], *, decision_cutoff: datetime) -> CryptoCatalystAggregation:
        cutoff = _aware(decision_cutoff, "decision_cutoff", required=True)
        rejected = 0
        changes: list[str] = []
        for item in evidence:
            try:
                if not item.eligible_at(cutoff):
                    rejected += 1
                    continue
                if item.evidence_identity in self._revisions:
                    self._duplicates += 1
                    continue
                self._revisions[item.evidence_identity] = None
                self._revisions.move_to_end(item.evidence_identity)
                bucket = self._events.setdefault(item.event_identity, [])
                if not bucket:
                    changes.append(item.event_identity)
                elif item.revision_identity not in {existing.revision_identity for existing in bucket}:
                    changes.append(item.revision_identity)
                bucket.append(item)
                bucket.sort(key=lambda value: (value.revision, value.observed_at, value.revision_identity))
                self._events.move_to_end(item.event_identity)
                project_key = item.project.project_id if item.project is not None else "UNRESOLVED"
                project_events = [
                    identity for identity, values in self._events.items()
                    if (values[0].project.project_id if values and values[0].project is not None else "UNRESOLVED") == project_key
                ]
                while len(project_events) > self.maximum_events_per_project:
                    oldest = project_events.pop(0)
                    self._events.pop(oldest, None)
                    self._evicted += 1
                signature = semantic_digest(item.event_identity, item.revision_identity, item.association_confidence.value, item.status.value)
                if signature not in self._signatures:
                    self._signatures[signature] = None
                while len(self._revisions) > self.maximum_revision_identities:
                    oldest_revision, _ = self._revisions.popitem(last=False)
                    for event_identity, revisions in tuple(self._events.items()):
                        remaining = [item for item in revisions if item.evidence_identity != oldest_revision]
                        if len(remaining) != len(revisions):
                            if remaining:
                                self._events[event_identity] = remaining
                            else:
                                self._events.pop(event_identity, None)
                            break
                while len(self._events) > self.maximum_canonical_events:
                    self._events.popitem(last=False)
                    self._evicted += 1
                while len(self._signatures) > self.maximum_revision_identities:
                    self._signatures.popitem(last=False)
            except Exception:
                self._failures += 1
        self._high_water = max(self._high_water, len(self._events))
        events = tuple(CryptoCatalystEvent(identity, tuple(values)) for identity, values in self._events.items())
        metrics = self.metrics()
        return CryptoCatalystAggregation(events, rejected, tuple(changes), metrics)

    def aggregate_provider(self, provider: CryptoCatalystProvider, *, decision_cutoff: datetime) -> CryptoCatalystAggregation:
        try:
            evidence = provider.collect(decision_cutoff)
        except Exception:
            self._failures += 1
            evidence = ()
        return self.aggregate(evidence, decision_cutoff=decision_cutoff)

    def evidence_at(self, cutoff: datetime) -> tuple[CryptoCatalystEvidence, ...]:
        """Return bounded normalized evidence eligible at a historical cutoff."""
        boundary = _aware(cutoff, "cutoff", required=True)
        values = [item for revisions in self._events.values() for item in revisions if item.eligible_at(boundary)]
        return tuple(sorted(values, key=lambda item: (item.published_at, item.provider_id, item.event_identity, item.revision_identity)))

    def metrics(self) -> CryptoCatalystMetrics:
        projects = {item.project.project_id for values in self._events.values() for item in values if item.project is not None}
        ambiguous = sum(1 for values in self._events.values() for item in values if item.association_confidence is CryptoAssociationConfidence.AMBIGUOUS)
        unresolved = sum(1 for values in self._events.values() for item in values if item.association_confidence is CryptoAssociationConfidence.UNRESOLVED)
        corroborated = sum(1 for values in self._events.values() if len({item.provider_id for item in values}) >= 2)
        revisions = sum(max(0, len(values) - 1) for values in self._events.values())
        return CryptoCatalystMetrics(len(projects), len(self._events), self._high_water, self._evicted, ambiguous, unresolved, corroborated, revisions, self._failures, self._duplicates)


def material_event_signature(event: CryptoCatalystEvidence) -> str:
    """Signature for meaningful state changes, excluding retrieval time noise."""

    return semantic_digest(event.event_identity, event.revision_identity, event.status.value, event.effective_at, event.association_confidence.value)


def summarize_crypto_catalysts(aggregation: CryptoCatalystAggregation, *, decision_cutoff: datetime) -> CryptoCatalystSummary:
    """Return bounded, read-only introspection over an explicit aggregation."""

    cutoff = _aware(decision_cutoff, "decision_cutoff", required=True)
    by_type: dict[str, int] = {}
    by_project: dict[str, int] = {}
    fresh = stale = ambiguous = unresolved = corroborated = revisions = 0
    for event in aggregation.events:
        latest = event.latest
        by_type[latest.event_type.value] = by_type.get(latest.event_type.value, 0) + 1
        project_id = latest.project.project_id if latest.project is not None else "UNRESOLVED"
        by_project[project_id] = by_project.get(project_id, 0) + 1
        freshness = latest.freshness_at(cutoff)
        fresh += freshness is CryptoFreshness.FRESH
        stale += freshness is CryptoFreshness.STALE
        ambiguous += latest.association_confidence is CryptoAssociationConfidence.AMBIGUOUS
        unresolved += latest.association_confidence is CryptoAssociationConfidence.UNRESOLVED
        corroborated += event.corroborated
        revisions += max(0, len(event.revisions) - 1)
    return CryptoCatalystSummary(tuple(sorted(by_type.items())), tuple(sorted(by_project.items())), fresh, stale, ambiguous, unresolved, corroborated, revisions, aggregation.metrics.crypto_catalyst_provider_failures)


__all__ = [
    "SCHEMA_VERSION", "MAX_PROJECTS", "MAX_EVENTS_PER_PROJECT", "MAX_CANONICAL_EVENTS", "MAX_REVISION_IDENTITIES",
    "CryptoCatalystType", "CryptoCatalystDirection", "CryptoCatalystStatus", "CryptoAssociationConfidence", "CryptoAssociationType", "CryptoFreshness",
    "CryptoProjectIdentity", "CryptoPairIdentity", "CryptoPairAssociation", "CryptoIdentityLookup", "CryptoProjectRegistry", "bootstrap_pair_identity",
    "CryptoCatalystEvidence", "CryptoCatalystEvent", "CryptoCatalystMetrics", "CryptoCatalystAggregation", "CryptoCatalystSummary", "CryptoCatalystProvider", "CryptoCatalystAggregator", "material_event_signature", "summarize_crypto_catalysts",
]
