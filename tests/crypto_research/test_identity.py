from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.crypto_research import (
    CryptoAssetIdentityRegistry,
    CryptoAssociationConfidence,
    CryptoBridgedAssetRelation,
    CryptoContractTokenIdentity,
    CryptoHistoricalSymbol,
    CryptoNativeAssetIdentity,
    CryptoNetworkIdentity,
    CryptoProviderAssetReference,
    CryptoProjectIdentity,
    CryptoTokenMigration,
    CryptoWrappedAssetRelation,
    bootstrap_pair_identity,
)


T0 = datetime(2026, 9, 8, 12, tzinfo=UTC)


def registry() -> CryptoAssetIdentityRegistry:
    btc_net = CryptoNetworkIdentity("bitcoin", "Bitcoin", "UTXO", "BTC", aliases=("BTC-NET",))
    eth_net = CryptoNetworkIdentity("ethereum", "Ethereum", "EVM", "ETH", chain_id=1)
    sol_net = CryptoNetworkIdentity("solana", "Solana", "NON_EVM", "SOL")
    btc = CryptoNativeAssetIdentity("btc-native", "bitcoin", "BTC", "Bitcoin")
    eth = CryptoNativeAssetIdentity("eth-native", "ethereum", "ETH", "Ether")
    sol = CryptoNativeAssetIdentity("sol-native", "solana", "SOL", "Solana")
    wbtc = CryptoContractTokenIdentity(
        "wbtc-eth", "ethereum", "0x" + "a" * 40, "WBTC", "Wrapped Bitcoin",
        historical_symbols=(CryptoHistoricalSymbol("XBTC", T0 - timedelta(days=10), T0),),
        provider_references=(CryptoProviderAssetReference("fixture", "wbtc-1", "WBTC"),),
    )
    weth = CryptoContractTokenIdentity("weth-eth", "ethereum", "0x" + "b" * 40, "WETH", "Wrapped Ether")
    usdc_eth = CryptoContractTokenIdentity("usdc-eth", "ethereum", "0x" + "c" * 40, "USDC", "USD Coin")
    usdc_sol = CryptoContractTokenIdentity("usdc-sol", "solana", "So11111111111111111111111111111111111111112", "USDC", "USD Coin")
    return CryptoAssetIdentityRegistry(
        networks=(btc_net, eth_net, sol_net),
        native_assets=(btc, eth, sol),
        contract_tokens=(wbtc, weth, usdc_eth, usdc_sol),
        provider_references=(CryptoProviderAssetReference("fixture", "wbtc-1", "WBTC"),),
    )


def test_native_and_wrapped_assets_are_distinct():
    r = registry()
    assert r.resolve(network_id="ethereum", symbol="ETH").asset_ids == ("eth-native",)
    assert r.resolve(network_id="ethereum", symbol="WETH").asset_ids == ("weth-eth",)
    assert r.resolve(network_id="ethereum", symbol="ETH").asset_ids != r.resolve(network_id="ethereum", symbol="WETH").asset_ids


def test_same_ticker_on_networks_is_ambiguous_without_network():
    r = registry()
    assert r.resolve(symbol="USDC").confidence is CryptoAssociationConfidence.AMBIGUOUS
    assert r.resolve(network_id="ethereum", symbol="USDC").asset_ids == ("usdc-eth",)
    assert r.resolve(network_id="solana", symbol="USDC").asset_ids == ("usdc-sol",)


def test_chain_and_contract_override_conflicting_weak_symbol():
    r = registry()
    result = r.resolve(network_id="ethereum", contract_address="0x" + "a" * 40, symbol="ETH")
    assert result.confidence is CryptoAssociationConfidence.AMBIGUOUS
    assert r.resolve(network_id="ethereum", contract_address="0x" + "a" * 40, symbol="WBTC").asset_ids == ("wbtc-eth",)


def test_provider_reference_cannot_override_contract_identity():
    r = registry()
    result = r.resolve(network_id="ethereum", contract_address="0x" + "b" * 40, provider_id="fixture", provider_asset_id="wbtc-1")
    assert result.asset_ids == ("weth-eth",)
    assert result.confidence is CryptoAssociationConfidence.EXACT


def test_non_evm_and_pair_bootstrap_do_not_invent_project_or_contract():
    r = registry()
    assert r.resolve(network_id="solana", symbol="SOL").asset_ids == ("sol-native",)
    pair = bootstrap_pair_identity("SOL/USD", "SOLUSD", "webull-sol")
    assert pair.instrument_id == "webull-sol"
    assert pair.base_asset_id is None
    assert r.resolve(symbol=pair.base_token_symbol).confidence is CryptoAssociationConfidence.AMBIGUOUS or r.resolve(symbol=pair.base_token_symbol).asset_ids == ("sol-native",)


def test_wrapped_bridged_and_migration_relationships_are_explicit_and_point_in_time():
    r = registry().with_relationships(
        wrapped=(CryptoWrappedAssetRelation("wbtc-eth", "btc-native", "ethereum", "0x" + "a" * 40),),
        bridged=(CryptoBridgedAssetRelation("usdc-sol", "usdc-eth", "ethereum", "solana", "So11111111111111111111111111111111111111112", "fixture-bridge"),),
        migrations=(CryptoTokenMigration("old-token", "new-token", T0, "migration-1", "contract replacement"),),
    )
    assert r.wrapped_relations[0].underlying_asset_id == "btc-native"
    assert r.bridged_relations[0].origin_network_id == "ethereum"
    assert r.asset_at_cutoff("old-token", T0 - timedelta(seconds=1)) == "old-token"
    assert r.asset_at_cutoff("old-token", T0) == "new-token"


def test_historical_symbol_is_valid_only_in_interval():
    r = registry()
    assert r.resolve(symbol="XBTC", cutoff=T0 - timedelta(days=11)).confidence is CryptoAssociationConfidence.UNRESOLVED
    assert r.resolve(symbol="XBTC", cutoff=T0 - timedelta(days=5)).asset_ids == ("wbtc-eth",)
    assert r.resolve(symbol="XBTC", cutoff=T0).confidence is CryptoAssociationConfidence.UNRESOLVED


def test_bounds_and_invalid_identity_are_contained():
    with pytest.raises(ValueError):
        CryptoContractTokenIdentity("bad", "ethereum", "", "BAD", "Bad")
    with pytest.raises(ValueError):
        CryptoAssetIdentityRegistry(networks=tuple(CryptoNetworkIdentity(str(i), str(i), "OTHER") for i in range(3)), maximum_networks=2)


def test_scalar_metrics_are_bounded_and_network_alias_lookup_is_explicit():
    r = registry()
    assert r.network_lookup("BTC-NET").network_id == "bitcoin"
    assert r.metrics.networks == 3
    assert r.metrics.tokens == 7
    assert r.metrics.contracts == 4
    assert r.metrics.evictions == 0


def test_reverse_lookups_cover_project_chain_and_pair():
    pair = bootstrap_pair_identity("BTC/USD", "BTCUSD", "webull-btc")
    project = CryptoProjectIdentity("bitcoin-project", "Bitcoin", "BTC", token_ids=("btc-native",))
    r = CryptoAssetIdentityRegistry(
        projects=(project,), networks=registry().networks,
        native_assets=registry().native_assets, contract_tokens=registry().contract_tokens,
        pairs=(pair,),
    )
    assert r.project_lookup("bitcoin-project").token_ids == ("BTC-NATIVE",)
    assert r.chain_lookup(1)[0].network_id == "ethereum"
    assert r.pair_lookup("btc/usd")[0].instrument_id == "webull-btc"
