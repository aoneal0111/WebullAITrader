from datetime import timedelta

import pytest

from app.crypto_research.runtime import CryptoResearchRuntime
from tests.asset_modules.test_crypto_worker import NOW


@pytest.mark.parametrize('second,duration,expected', [
    (0, 6, 64), (59, 6, 65), (10, 6, 54), (10, 90, 30),
])
def test_initial_alignment_preserves_minimum_request_spacing(second, duration, expected):
    runtime = CryptoResearchRuntime(intelligence_context_sink=lambda _: None)
    started = NOW + timedelta(seconds=second)
    finished = started + timedelta(seconds=duration)
    delay = runtime._poll_delay(started, finished, align=True)
    assert delay == expected
    next_start = finished + timedelta(seconds=delay)
    assert next_start.second == 10
    assert next_start - started >= timedelta(seconds=60)


def test_repeated_provider_duration_does_not_drift_into_minute_boundary():
    runtime = CryptoResearchRuntime(intelligence_context_sink=lambda _: None)
    started = NOW + timedelta(seconds=59)
    starts = [started]
    for cycle in range(40):
        finished = started + timedelta(seconds=6)
        delay = runtime._poll_delay(started, finished, align=cycle == 0)
        started = finished + timedelta(seconds=delay)
        starts.append(started)
    assert all(s.second == 10 for s in starts[1:])
    assert all(b-a >= timedelta(seconds=60) for a,b in zip(starts, starts[1:]))


def test_wakeup_jitter_cannot_increase_request_rate():
    runtime = CryptoResearchRuntime(intelligence_context_sink=lambda _: None)
    started = NOW + timedelta(seconds=10, microseconds=1500)
    finished = started + timedelta(seconds=6)
    delay = runtime._poll_delay(started, finished, align=False)
    assert finished + timedelta(seconds=delay) == started + timedelta(seconds=60)


@pytest.mark.parametrize('seconds,history', [(15, True), (120, True), (60, False)])
def test_custom_cadence_and_quote_only_collection_unchanged(seconds, history):
    runtime = CryptoResearchRuntime(refresh_seconds=seconds,
        intelligence_context_sink=(lambda _: None) if history else None)
    assert runtime._poll_delay(NOW, NOW+timedelta(seconds=6), align=True) == seconds


def test_clock_rewind_uses_existing_wait_instead_of_busy_loop():
    runtime = CryptoResearchRuntime(intelligence_context_sink=lambda _: None)
    assert runtime._poll_delay(NOW, NOW-timedelta(seconds=1), align=False) == 60


def test_poller_refreshes_immediately_then_uses_interruptible_aligned_wait():
    clock = [NOW+timedelta(seconds=59)]
    runtime = CryptoResearchRuntime(clock=lambda: clock[0],
        intelligence_context_sink=lambda _: None)
    calls = []
    class Stop:
        def is_set(self): return False
        def wait(self, delay):
            calls.append(('WAIT', delay))
            return True
    def refresh():
        calls.append(('REFRESH', clock[0]))
        clock[0] += timedelta(seconds=6)
    runtime._stop = Stop()
    runtime.refresh_once = refresh
    runtime._run_poller()
    assert calls == [('REFRESH', NOW+timedelta(seconds=59)), ('WAIT', 65)]
