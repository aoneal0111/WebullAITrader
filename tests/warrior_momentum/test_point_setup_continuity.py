from datetime import timedelta
from dataclasses import replace
from decimal import Decimal as D

import pytest

from app.strategies.warrior_momentum import WarriorMomentumRuntime,SetupDetection,SetupType,SetupState
from tests.warrior_momentum.test_strategy import observation


@pytest.mark.parametrize('kind',[
    SetupType.MOMENTUM_ACCELERATION,SetupType.MOMENTUM_REACCELERATION,SetupType.RECLAIM_CONTINUATION,
])
def test_missing_point_detector_is_not_restored_by_bar_setup_continuity(monkeypatch,kind):
    runtime=WarriorMomentumRuntime()
    obs=replace(observation(),current_volume=D(1000))
    prior=SetupDetection(kind,SetupState.FORMING,D(70),D(10),D(9))
    runtime._setup_continuity[obs.symbol]=(prior,obs.timestamp-timedelta(seconds=1),'REGULAR')
    monkeypatch.setattr('app.strategies.warrior_momentum.runtime.detect_best_setup',lambda *args:None)
    candidate=runtime.discover(obs,(),session='REGULAR')
    assert candidate.setup is None
    assert obs.symbol not in runtime._setup_continuity


def test_bar_forming_continuity_is_preserved(monkeypatch):
    runtime=WarriorMomentumRuntime();obs=replace(observation(),current_volume=D(1000))
    prior=SetupDetection(SetupType.MICRO_PULLBACK,SetupState.FORMING,D(70),D(10),D(9))
    runtime._setup_continuity[obs.symbol]=(prior,obs.timestamp-timedelta(seconds=1),'REGULAR')
    monkeypatch.setattr('app.strategies.warrior_momentum.runtime.detect_best_setup',lambda *args:None)
    candidate=runtime.discover(obs,(),session='REGULAR')
    assert candidate.setup is prior
    assert candidate.setup.state is SetupState.FORMING
