from datetime import UTC, datetime
from decimal import Decimal

from app.strategies.warrior_momentum.configuration import SessionManagementConfig
from app.strategies.warrior_momentum.session_risk import (
    assess_overnight_carry,
    entry_cutoff_reached,
    flatten_window_reached,
    overnight_session_follows,
)


def instant(hour: int, minute: int) -> datetime:
    # September is EDT, so 23:xx UTC is 19:xx New York.
    return datetime(2026, 9, 21, hour, minute, tzinfo=UTC)


def test_after_hours_cutoff_and_flatten_windows_are_distinct() -> None:
    config = SessionManagementConfig(
        after_hours_entry_cutoff_minutes=30,
        flatten_lead_minutes=5,
    )
    assert entry_cutoff_reached(instant(23, 30), config)
    assert not flatten_window_reached(instant(23, 30), config)
    assert flatten_window_reached(instant(23, 55), config)
    assert not entry_cutoff_reached(datetime(2026, 9, 22, 0, 0, tzinfo=UTC), config)


def test_overnight_carry_requires_opt_in_protection_profit_and_fresh_support() -> None:
    disabled = assess_overnight_carry(
        config=SessionManagementConfig(), protection_active=True,
        current_r=Decimal("1.5"), peak_r=Decimal("2"),
        giveback_r=Decimal("0.2"), quote_fresh=True, pressure_score=2,
    )
    assert not disabled.carry
    assert "OVERNIGHT_NOT_ENABLED" in disabled.reasons

    approved = assess_overnight_carry(
        config=SessionManagementConfig(overnight_carry_enabled=True),
        protection_active=True, current_r=Decimal("1.5"),
        peak_r=Decimal("2"), giveback_r=Decimal("0.2"),
        quote_fresh=True, pressure_score=1,
    )
    assert approved.carry
    assert approved.reasons == ("OVERNIGHT_CARRY_APPROVED",)


def test_stale_or_adverse_microstructure_forces_flatten_even_when_profitable() -> None:
    config = SessionManagementConfig(overnight_carry_enabled=True)
    stale = assess_overnight_carry(
        config=config, protection_active=True,
        current_r=Decimal("2"), peak_r=Decimal("3"),
        giveback_r=Decimal("0.25"), quote_fresh=False, pressure_score=2,
    )
    adverse = assess_overnight_carry(
        config=config, protection_active=True,
        current_r=Decimal("2"), peak_r=Decimal("3"),
        giveback_r=Decimal("0.25"), quote_fresh=True, pressure_score=-1,
    )
    assert not stale.carry and "MICROSTRUCTURE_STALE" in stale.reasons
    assert not adverse.carry and "PRESSURE_ADVERSE_OR_UNKNOWN" in adverse.reasons


def test_weekend_boundary_never_approves_overnight_carry() -> None:
    friday = datetime(2026, 9, 25, 23, 55, tzinfo=UTC)
    assert not overnight_session_follows(friday)
    result = assess_overnight_carry(
        config=SessionManagementConfig(overnight_carry_enabled=True),
        protection_active=True, current_r=Decimal("2"), peak_r=Decimal("3"),
        giveback_r=Decimal("0.1"), quote_fresh=True, pressure_score=2,
        overnight_available=overnight_session_follows(friday),
    )
    assert not result.carry
    assert "NO_OVERNIGHT_SESSION" in result.reasons
