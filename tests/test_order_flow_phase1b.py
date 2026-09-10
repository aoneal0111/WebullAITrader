from datetime import datetime, timedelta, timezone
from decimal import Decimal
from threading import Event

from app.strategies.warrior_momentum.order_flow import OrderFlowClassification
from app.strategies.warrior_momentum.order_flow_runtime import (
    OrderFlowFreshness,
    OrderFlowPollingConfig,
    OrderFlowPollingService,
    OrderFlowPriority,
)


NOW = datetime(2026, 9, 10, 19, 45, tzinfo=timezone.utc)
D = Decimal


class Clock:
    def __init__(self, value=NOW):
        self.value = value

    def __call__(self):
        return self.value


class Provider:
    def __init__(self):
        self.footprint_calls = []
        self.capital_calls = []
        self.footprint_fail = set()
        self.capital_fail = set()

    def footprint(self, symbol):
        self.footprint_calls.append(symbol)
        if symbol in self.footprint_fail:
            raise TimeoutError("provider timeout")
        return [{"buy_volume": "100", "sell_volume": "20", "time": NOW}]

    def capital(self, symbol):
        self.capital_calls.append(symbol)
        if symbol in self.capital_fail:
            raise RuntimeError("subscription unavailable")
        return [{"net_inflow": "1000"}]


def service(clock, provider, **kwargs):
    config = OrderFlowPollingConfig(
        high_footprint_seconds=D("12"), medium_footprint_seconds=D("24"),
        low_footprint_seconds=D("60"), high_capital_flow_seconds=D("45"),
        medium_capital_flow_seconds=D("90"), low_capital_flow_seconds=D("180"),
        **kwargs,
    )
    return OrderFlowPollingService(
        object(), config=config, clock=clock,
        footprint_fetcher=provider.footprint,
        capital_flow_fetcher=provider.capital,
    )


def test_high_priority_symbol_fetches_both_endpoints_and_caches_separately():
    clock, provider = Clock(), Provider()
    polling = service(clock, provider)
    assert polling.update_symbol("dbgi", OrderFlowPriority.HIGH)
    assert polling.poll_once() == 2
    assert provider.footprint_calls == ["DBGI"]
    assert provider.capital_calls == ["DBGI"]
    assert polling.cache_entry("DBGI", "FOOTPRINT").freshness is OrderFlowFreshness.FRESH
    assert polling.cache_entry("DBGI", "CAPITAL_FLOW").freshness is OrderFlowFreshness.FRESH
    assert polling.assessment("DBGI").classification is OrderFlowClassification.SUPPORTIVE


def test_stale_footprint_becomes_unavailable_without_reusing_bullish_data():
    clock, provider = Clock(), Provider()
    polling = service(clock, provider)
    polling.update_symbol("DBGI", OrderFlowPriority.HIGH)
    polling.poll_once()
    clock.value += timedelta(seconds=46)
    assessment = polling.assessment("DBGI")
    assert assessment.classification is OrderFlowClassification.UNAVAILABLE
    assert not assessment.fresh


def test_distribution_or_endpoint_failure_isolated_per_symbol_and_endpoint():
    clock, provider = Clock(), Provider()
    provider.footprint_fail.add("DBGI")
    polling = service(clock, provider)
    polling.update_symbol("DBGI", OrderFlowPriority.HIGH)
    polling.update_symbol("TNON", OrderFlowPriority.HIGH)
    polling.poll_once()
    assert polling.cache_entry("DBGI", "FOOTPRINT").freshness is OrderFlowFreshness.ERROR
    assert polling.cache_entry("DBGI", "CAPITAL_FLOW").freshness is OrderFlowFreshness.FRESH
    assert polling.cache_entry("TNON", "FOOTPRINT").freshness is OrderFlowFreshness.FRESH
    assert polling.cache_entry("TNON", "CAPITAL_FLOW").freshness is OrderFlowFreshness.FRESH
    assert polling.assessment("DBGI").classification is OrderFlowClassification.UNAVAILABLE


def test_rate_limit_error_uses_bounded_backoff_and_does_not_retry_immediately():
    clock, provider = Clock(), Provider()
    provider.footprint = lambda symbol: (_ for _ in ()).throw(RuntimeError("HTTP 429 rate limit"))
    polling = service(clock, provider)
    polling.update_symbol("DBGI", OrderFlowPriority.HIGH)
    assert polling.poll_once() == 2
    first_calls = len(provider.capital_calls)
    assert polling.cache_entry("DBGI", "FOOTPRINT").next_refresh_at > NOW
    assert any(item.event == "ORDER_FLOW_RATE_LIMITED" for item in polling.diagnostics)
    assert polling.poll_once(now=NOW) == 0
    assert len(provider.capital_calls) == first_calls


def test_active_symbol_set_is_bounded_and_duplicate_updates_are_coalesced():
    clock, provider = Clock(), Provider()
    polling = service(clock, provider, maximum_active_symbols=2)
    assert polling.update_symbol("A", OrderFlowPriority.LOW)
    assert polling.update_symbol("A", OrderFlowPriority.HIGH)
    assert polling.update_symbol("B", OrderFlowPriority.LOW)
    assert polling.update_symbol("C", OrderFlowPriority.HIGH)
    assert polling.active_symbols == ("A", "C")
    assert polling.dropped_symbols == 0
    assert not polling.update_symbol("B", OrderFlowPriority.LOW)


def test_symbol_eviction_removes_active_polling_but_keeps_no_pending_work():
    clock, provider = Clock(), Provider()
    polling = service(clock, provider)
    polling.update_symbol("DBGI", OrderFlowPriority.LOW)
    polling.remove_symbol("DBGI")
    assert polling.active_symbols == ()
    assert polling.poll_once() == 0
    assert any(item.event == "ORDER_FLOW_SYMBOL_REMOVED" for item in polling.diagnostics)


def test_worker_starts_and_stops_without_orphaning_runtime():
    clock, provider = Clock(), Provider()
    polling = service(clock, provider)
    polling.update_symbol("DBGI", OrderFlowPriority.HIGH)
    polling.start()
    assert polling.running
    polling.stop(timeout_seconds=2)
    assert not polling.running


def test_client_capability_can_be_absent_without_starting_worker():
    polling = OrderFlowPollingService(None)
    assert not polling.running
    assert not polling.update_symbol("DBGI", OrderFlowPriority.HIGH)


def test_missing_provider_capability_is_explicit_and_isolated():
    class EmptyClient:
        market_data = object()
        fundamentals = object()

    clock = Clock()
    polling = OrderFlowPollingService(EmptyClient(), clock=clock)
    polling.update_symbol("DBGI", OrderFlowPriority.HIGH)
    assert polling.poll_once() == 2
    footprint = polling.cache_entry("DBGI", "FOOTPRINT")
    capital = polling.cache_entry("DBGI", "CAPITAL_FLOW")
    assert footprint.freshness is OrderFlowFreshness.UNAVAILABLE
    assert capital.freshness is OrderFlowFreshness.UNAVAILABLE
    assert sum(
        item.event == "ORDER_FLOW_CAPABILITY_UNAVAILABLE"
        for item in polling.diagnostics
    ) == 2
