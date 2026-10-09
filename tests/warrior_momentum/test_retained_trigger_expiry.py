from dataclasses import replace
from datetime import timedelta

import pytest

from app.strategies.warrior_momentum.forward_runtime import WarriorForwardCaptureService
from app.strategies.warrior_momentum.forward_store import ForwardCaptureStore
from app.strategies.warrior_momentum.models import SetupState
from app.strategies.warrior_momentum.opportunity_engine import (
    AdaptiveOpportunityResult, WarriorOpportunityState,
)
from tests.warrior_momentum.test_forward_capture import point


@pytest.mark.parametrize("reconfirmed,expiry_seconds", [(False, 121), (True, 240)])
def test_only_genuine_confirmation_renews_trigger_deadline(
    tmp_path, reconfirmed, expiry_seconds,
):
    service = WarriorForwardCaptureService(
        ForwardCaptureStore(tmp_path / "capture.sqlite3"), None,
    )
    value = point()
    candidate = service.runtime.discover(
        value.observation, value.bars, session=value.session,
    )
    signal = service.runtime.technical_entry_signal(candidate)
    assert signal is not None
    service._remember_armed_signal(signal)
    service.opportunity_engine.arm(service._build_opportunity_assessment(
        value, candidate, signal, adaptive_result=AdaptiveOpportunityResult.ARMED,
    ))
    forming = replace(candidate, setup=replace(candidate.setup, state=SetupState.FORMING))
    # Repeated evaluations refresh quote/price evidence, not the time at which
    # the breakout was first established.
    for seconds in (60, 119, 120):
        refreshed = service._retained_generation_signal(replace(
            forming, timestamp=candidate.timestamp + timedelta(seconds=seconds),
        ))
        assert refreshed is not None
        assert refreshed.timestamp == candidate.timestamp + timedelta(seconds=seconds)
        service._remember_armed_signal(refreshed, renew_continuity=False)
        if reconfirmed and seconds == 119:
            # A real new technical confirmation, unlike a retained FORMING
            # update, legitimately starts another continuity interval.
            service._remember_armed_signal(refreshed)
    assert service._retained_generation_signal(replace(
        forming, timestamp=candidate.timestamp + timedelta(seconds=expiry_seconds),
    )) is None
    assert service.opportunity_engine.get(candidate.symbol).state is WarriorOpportunityState.EXPIRED
