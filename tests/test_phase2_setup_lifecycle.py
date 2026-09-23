from collections import OrderedDict
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

from app.strategies.warrior_momentum.forward_runtime import WarriorForwardCaptureService
from app.strategies.warrior_momentum.models import SetupState, SetupType
from app.strategies.warrior_momentum.setups import detect_bull_flag
from tests.warrior_momentum.test_strategy import bull_flag_bars


def _service() -> object:
    service = object.__new__(WarriorForwardCaptureService)
    service._setup_lifecycles = OrderedDict()
    service._setup_lifecycle_capacity = 512
    service.writer = None
    return service


def _value(timestamp: datetime, price: str = "10.20") -> object:
    return SimpleNamespace(
        observation=SimpleNamespace(price=Decimal(price)),
        bars=(),
    )


def _candidate(
    *, state: SetupState | None, qualified: bool = True,
    symbol: str = "XYZ", price: str = "10.20",
) -> object:
    setup = None if state is None else SimpleNamespace(
        state=state, setup_type=SetupType.BULL_FLAG,
        trigger=Decimal("10.30"), stop_price=Decimal("10.00"),
        structural_episode_id="episode-1",
    )
    return SimpleNamespace(
        symbol=symbol, discovery_qualified=qualified,
        distance_from_hod_percent=Decimal("0.5"), session="REGULAR",
        price=Decimal(price), setup=setup,
    )


def test_completed_bar_detector_progression_is_unchanged_and_bounded() -> None:
    bars = bull_flag_bars()
    assert detect_bull_flag(bars[:5]).state in {
        SetupState.UNKNOWN, SetupState.NOT_FORMED,
    }
    assert detect_bull_flag(bars[:-1]).state is SetupState.FORMING
    assert detect_bull_flag(bars).state is SetupState.TRIGGERED


def test_setup_lifecycle_is_transition_only_and_monotonic() -> None:
    service = _service()
    first = datetime(2026, 9, 23, 14, 30, tzinfo=UTC)
    service._record_setup_lifecycle(
        _value(first), _candidate(state=None), setup_state="NO_SETUP",
        technical_signal=None, signal=None, timestamp=first,
    )
    service._record_setup_lifecycle(
        _value(first + timedelta(seconds=5), "10.25"),
        _candidate(state=SetupState.FORMING, price="10.25"),
        setup_state="FORMING", technical_signal=None, signal=None,
        timestamp=first + timedelta(seconds=5),
    )
    service._record_setup_lifecycle(
        _value(first + timedelta(seconds=10), "10.31"),
        _candidate(state=SetupState.TRIGGERED, price="10.31"),
        setup_state="TRIGGERED", technical_signal=object(), signal=None,
        timestamp=first + timedelta(seconds=10),
    )
    snapshot = service.setup_lifecycle_snapshot("XYZ")
    assert snapshot is not None
    assert snapshot["first_setup_none_at"] == first
    assert snapshot["first_setup_forming_at"] == first + timedelta(seconds=5)
    assert snapshot["first_setup_triggered_at"] == first + timedelta(seconds=10)
    assert snapshot["first_observed_at"] == first
    assert snapshot["price_at_first_observation"] == Decimal("10.20")
    assert snapshot["price_at_first_forming"] == Decimal("10.25")
    assert snapshot["price_at_first_trigger"] == Decimal("10.31")
    assert snapshot["setup_development_reason"] == "SETUP_TRIGGERED"


def test_setup_lifecycle_retention_is_bounded() -> None:
    service = _service()
    timestamp = datetime(2026, 9, 23, 14, 30, tzinfo=UTC)
    for index in range(513):
        symbol = f"S{index:03d}"
        service._record_setup_lifecycle(
            _value(timestamp), _candidate(state=None, symbol=symbol),
            setup_state="NO_SETUP", technical_signal=None, signal=None,
            timestamp=timestamp,
        )
    snapshots = service.setup_lifecycle_snapshot()
    assert isinstance(snapshots, tuple)
    assert len(snapshots) == 512
    assert snapshots[0]["symbol"] == "S001"


def test_lifecycle_execution_fields_are_observational_only() -> None:
    service = _service()
    timestamp = datetime(2026, 9, 23, 14, 30, tzinfo=UTC)
    candidate = _candidate(state=SetupState.TRIGGERED)
    service._record_setup_lifecycle(
        _value(timestamp), candidate, setup_state="TRIGGERED",
        technical_signal=object(), signal=object(), timestamp=timestamp,
        lifecycle_stage="ORDER_SUBMITTED",
    )
    snapshot = service.setup_lifecycle_snapshot("XYZ")
    assert snapshot is not None
    assert snapshot["first_execution_eligible_at"] == timestamp
    assert snapshot["first_entry_authorized_at"] == timestamp
    assert snapshot["first_order_submitted_at"] == timestamp
