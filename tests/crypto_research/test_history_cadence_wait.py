from datetime import timedelta
from decimal import Decimal

import pytest

from app.crypto_research import CryptoPair, CryptoObservation, CryptoResearchRuntime
from tests.asset_modules.test_crypto_worker import NOW


def make_runtime(tmp_path, *, discovery_seconds=(1.02, 1.0), cadence=60, refresh=60):
    pair = CryptoPair('BTC', 'USD', 'BTCUSD')
    clock = [NOW+timedelta(seconds=10)]
    durations = iter(discovery_seconds)
    requests = []
    waits = []
    class Provider:
        def discover(self, _):
            clock[0] += timedelta(seconds=next(durations))
            return (pair,)
        def historical_bars(self, _, *, timespan, **kwargs):
            requests.append((timespan, clock[0]))
            return ()
        def snapshots(self, _):
            requests.append(('QUOTE', clock[0]))
            price = Decimal(100+clock[0].minute)
            return (CryptoObservation(pair, clock[0], price,
                price, price+Decimal('.1'), None),)
    class Stop:
        def wait(self, seconds):
            waits.append(seconds)
            clock[0] += timedelta(seconds=seconds)
            return False
        def is_set(self): return False
    runtime = CryptoResearchRuntime(enabled=True, provider=Provider(), clock=lambda: clock[0],
        path=tmp_path/'quotes.jsonl', intelligence_context_sink=lambda _: None,
        refresh_seconds=refresh, intelligence_m1_refresh_seconds=cadence,
        intelligence_m5_refresh_seconds=cadence)
    runtime._research_shortlist = lambda: (pair,)
    runtime._accepting = True
    runtime._stop = Stop()
    return runtime, clock, requests, waits


def test_variable_discovery_reproduces_skipped_history_without_wait(tmp_path):
    runtime, clock, requests, waits = make_runtime(tmp_path)
    assert runtime.refresh_once() == 1
    clock[0] = NOW+timedelta(minutes=1, seconds=10)
    assert runtime.refresh_once() == 1
    assert [kind for kind, _ in requests] == ['M1', 'M5', 'QUOTE', 'QUOTE']
    assert waits == []


def test_short_wait_restores_history_before_quote_without_shortening_cadence(tmp_path):
    runtime, clock, requests, waits = make_runtime(tmp_path)
    assert runtime.refresh_once(wait_for_history_cadence=True) == 1
    clock[0] = NOW+timedelta(minutes=1, seconds=10)
    assert runtime.refresh_once(wait_for_history_cadence=True) == 1
    assert waits == [pytest.approx(.02)]
    assert [kind for kind, _ in requests] == ['M1', 'M5', 'QUOTE']*2
    for kind in ('M1', 'M5'):
        times = [at for k, at in requests if k == kind]
        assert times[1]-times[0] == timedelta(seconds=60)
    assert requests[-1][1] == clock[0]


def test_shutdown_interrupts_cadence_wait_without_history_or_snapshot_requests(tmp_path):
    runtime, clock, requests, waits = make_runtime(tmp_path)
    runtime.refresh_once()
    clock[0] = NOW+timedelta(minutes=1, seconds=10)
    class Stop:
        def wait(self, seconds):
            waits.append(seconds)
            return True
    runtime._stop = Stop()
    assert runtime.refresh_once(wait_for_history_cadence=True) == 0
    assert len(requests) == 3


@pytest.mark.parametrize('cadence,refresh', [(120,60), (60,15)])
def test_custom_timing_does_not_wait(tmp_path, cadence, refresh):
    runtime, clock, requests, waits = make_runtime(tmp_path, cadence=cadence, refresh=refresh)
    runtime.refresh_once()
    clock[0] = NOW+timedelta(minutes=1, seconds=10)
    runtime.refresh_once(wait_for_history_cadence=True)
    assert waits == []


def test_wait_is_bounded_and_does_not_retry_early_cycle(tmp_path):
    runtime, clock, requests, waits = make_runtime(tmp_path)
    runtime.refresh_once()
    clock[0] = NOW+timedelta(seconds=40)
    runtime.refresh_once(wait_for_history_cadence=True)
    assert waits == []
    assert [kind for kind, _ in requests] == ['M1', 'M5', 'QUOTE', 'QUOTE']


def test_early_wait_completion_rechecks_timer_before_history_request(tmp_path):
    runtime, clock, requests, waits = make_runtime(tmp_path)
    runtime.refresh_once()
    clock[0] = NOW+timedelta(minutes=1, seconds=10)
    class Stop:
        def wait(self, seconds):
            waits.append(seconds)
            # Model a timer wakeup/clock reading one millisecond early once.
            elapsed = seconds-.001 if len(waits) == 1 else seconds
            clock[0] += timedelta(seconds=elapsed)
            return False
    runtime._stop = Stop()
    runtime.refresh_once(wait_for_history_cadence=True)
    assert [kind for kind, _ in requests] == ['M1', 'M5', 'QUOTE']*2
    assert len(waits) == 2
    for kind in ('M1', 'M5'):
        times = [at for k, at in requests if k == kind]
        assert times[1]-times[0] >= timedelta(seconds=60)


def test_nonadvancing_clock_cannot_loop_or_issue_early_requests(tmp_path):
    runtime, clock, requests, waits = make_runtime(tmp_path)
    runtime.refresh_once()
    clock[0] = NOW+timedelta(minutes=1, seconds=10)
    class Stop:
        def wait(self, seconds):
            waits.append(seconds)
            return False
    runtime._stop = Stop()
    runtime.refresh_once(wait_for_history_cadence=True)
    assert len(waits) == 8
    assert [kind for kind, _ in requests] == ['M1', 'M5', 'QUOTE', 'QUOTE']


def test_monotonic_wait_budget_limits_rechecking(tmp_path, monkeypatch):
    runtime, clock, requests, waits = make_runtime(tmp_path)
    runtime.refresh_once()
    clock[0] = NOW+timedelta(minutes=1, seconds=10)
    ticks = iter((100, 106))
    monkeypatch.setattr('app.crypto_research.runtime.monotonic', lambda: next(ticks))
    runtime.refresh_once(wait_for_history_cadence=True)
    assert waits == []
    assert [kind for kind, _ in requests] == ['M1', 'M5', 'QUOTE', 'QUOTE']
