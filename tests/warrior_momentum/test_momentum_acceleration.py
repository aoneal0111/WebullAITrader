from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

from app.momentum_scanner.models import (
    AssetClass, CatalystStatus, CatalystType, FloatProvenance, ScannerObservation,
)
from app.strategies.warrior_momentum.models import ReasonCode, SetupState, SetupType
from app.strategies.warrior_momentum.desktop_sidecar import _fast_mover_refresh_eligible
from app.strategies.warrior_momentum.runtime import WarriorMomentumRuntime
from app.strategies.warrior_momentum.setups import (
    AccelerationPoint, detect_best_setup, detect_momentum_acceleration,
    detect_momentum_reacceleration, detect_reclaim_continuation,
)


T0 = datetime(2026, 9, 29, 14, 0, tzinfo=UTC)


def points(prices=("10", "10.1", "10.3", "10.6"), *, volume=True, spread="0.8", fresh=True,
           tradable=True, halted=False):
    result = []
    for index, value in enumerate(prices):
        scale = Decimal(index + 1) if volume else Decimal("1")
        result.append(AccelerationPoint(
            T0 + timedelta(seconds=index * 5), Decimal(value),
            Decimal("100000") * scale, Decimal("1000000") * scale,
            Decimal(spread), tradable, halted, fresh,
        ))
    return tuple(result)


def test_isolated_spike_and_large_gain_without_acceleration_do_not_form():
    assert detect_momentum_acceleration(points(("10", "10", "15"))).state is SetupState.NOT_FORMED
    assert detect_momentum_acceleration(points(("10", "11", "12"), volume=False)).state is SetupState.NOT_FORMED


def test_multiple_supportive_observations_form_then_trigger():
    forming = detect_momentum_acceleration(points(("10", "10.1", "10.3")))
    triggered = detect_momentum_acceleration(points())
    assert forming.setup_type is SetupType.MOMENTUM_ACCELERATION
    assert forming.state is SetupState.FORMING
    assert triggered.state is SetupState.TRIGGERED
    assert triggered.trigger is not None and triggered.stop_price is not None
    assert triggered.trigger > Decimal("10.1")
    assert triggered.stop_price == Decimal("10")


def test_participation_spread_freshness_and_hard_safety_invalidate():
    for value in (
        points(volume=False),
        points(fresh=False),
        points(tradable=False),
        points(halted=True),
        points(("10", "10.1", "10.0", "9.8")),
    ):
        result = detect_momentum_acceleration(value)
        assert result.state is SetupState.NOT_FORMED
        assert ReasonCode.NO_SETUP in result.reason_codes
    assert detect_momentum_acceleration(
        points(spread="2.5"),
    ).state is SetupState.TRIGGERED


def test_episode_identity_is_stable_for_same_window():
    first = detect_momentum_acceleration(points())
    second = detect_momentum_acceleration(points())
    assert first.structural_episode_id == second.structural_episode_id
    assert first.structural_anchor == second.structural_anchor


def test_acceleration_lifetime_is_bounded():
    value = tuple(
        AccelerationPoint(
            T0 + timedelta(seconds=index * 15), Decimal(str(10 + index / 10)),
            Decimal(100000 + index * 10000), Decimal(1000000 + index * 100000),
            Decimal("0.8"), True, False,
        )
        for index in range(4)
    )
    assert detect_momentum_acceleration(value).state is SetupState.NOT_FORMED


def continuation_points(prices=("10", "10.8", "10.5", "10.6", "10.95")):
    return tuple(
        AccelerationPoint(
            T0 + timedelta(seconds=index * 5), Decimal(value),
            Decimal(100000) * (index + 1), Decimal(1000000) * (index + 1),
            Decimal("0.8"), True, False,
        )
        for index, value in enumerate(prices)
    )


def test_reacceleration_requires_pause_and_new_expansion():
    result = detect_momentum_reacceleration(continuation_points())
    assert result.setup_type is SetupType.MOMENTUM_REACCELERATION
    assert result.state is SetupState.TRIGGERED
    assert result.stop_price == Decimal("10.5")
    noisy = detect_momentum_reacceleration(
        continuation_points(("10", "10.2", "10.3", "10.4", "10.5"))
    )
    assert noisy.state is SetupState.NOT_FORMED


def test_reclaim_requires_level_loss_and_reclaim():
    result = detect_reclaim_continuation(continuation_points())
    assert result.setup_type is SetupType.RECLAIM_CONTINUATION
    assert result.state is SetupState.TRIGGERED
    assert result.trigger is not None and result.stop_price == Decimal("10.5")
    no_loss = detect_reclaim_continuation(
        continuation_points(("10", "10.8", "10.79", "10.8", "10.95"))
    )
    assert no_loss.state is SetupState.NOT_FORMED


def test_lifecycle_setup_identity_is_stable_and_best_setup_is_deterministic():
    value = continuation_points()
    first = detect_momentum_reacceleration(value)
    second = detect_momentum_reacceleration(value)
    assert first.structural_episode_id == second.structural_episode_id
    assert detect_best_setup((), acceleration_points=value).setup_type in {
        SetupType.MOMENTUM_REACCELERATION, SetupType.RECLAIM_CONTINUATION,
    }


def test_fast_mover_refresh_requires_converging_existing_context():
    config = WarriorMomentumRuntime().config
    strong = SimpleNamespace(
        observation_eligible=True, tradable=True, halted=False, price=Decimal("8"),
        spread_percent=Decimal("0.8"), percentage_change=Decimal("18"),
        dollar_volume=Decimal("5000000"), relative_volume=Decimal("8"),
        score=SimpleNamespace(total=Decimal("78")),
    )
    assert _fast_mover_refresh_eligible(strong, None, config)
    ordinary = SimpleNamespace(**{**strong.__dict__, "score": SimpleNamespace(total=Decimal("70"))})
    assert not _fast_mover_refresh_eligible(ordinary, None, config)
    wider = SimpleNamespace(**{**strong.__dict__, "spread_percent": Decimal("2.5")})
    assert _fast_mover_refresh_eligible(wider, None, config)


def _live_observation(timestamp: datetime, *, price: str = "8.00", volume: str = "800000"):
    value = Decimal(price)
    return ScannerObservation(
        symbol="FAST", timestamp=timestamp, price=value,
        previous_close=Decimal("6.50"), current_volume=Decimal(volume),
        average_30_day_volume=Decimal("100000"), float_shares=Decimal("1000000"),
        bid=value - Decimal("0.02"), ask=value + Decimal("0.02"),
        catalyst=CatalystType.NONE, catalyst_headline=None, tradable=True,
        halted=False, asset_class=AssetClass.STOCK,
        catalyst_status=CatalystStatus.FALSE,
        float_provenance=FloatProvenance.AUTHORITATIVE_FLOAT,
    )


def test_runtime_deduplicates_canonical_acceleration_observations():
    runtime = WarriorMomentumRuntime()
    first = _live_observation(T0)
    runtime.discover(first, (), session="REGULAR")
    runtime.discover(first, (), session="REGULAR")
    runtime.discover(_live_observation(T0 + timedelta(seconds=5), price="8.10", volume="850000"), (), session="REGULAR")
    runtime.discover(_live_observation(T0 + timedelta(seconds=10), price="8.25", volume="920000"), (), session="REGULAR")
    points = runtime._acceleration_points["FAST"]
    assert len(points) == 3
    assert len({point.timestamp for point in points}) == 3
