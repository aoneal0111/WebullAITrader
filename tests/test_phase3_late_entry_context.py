from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.strategies.warrior_momentum.entry_extension import assess_entry_extension


T0 = datetime(2026, 9, 23, 15, 0, tzinfo=UTC)


def _assess(price: str, *, continuation: bool = False):
    return assess_entry_extension(
        entry_price=Decimal(price), trigger_price=Decimal("5.50"),
        structural_stop=Decimal("5.20"),
        first_observation_price=Decimal("5.00"),
        forming_price=Decimal("5.30"), hod=Decimal("6.20"),
        vwap=Decimal("5.40"), trigger_at=T0,
        evaluated_at=T0 + timedelta(seconds=30),
        displacement_limit=Decimal("5.55"),
        continuation_confirmed=continuation,
    )


def test_near_trigger_is_allowed_and_metrics_are_bounded() -> None:
    result = _assess("5.52")
    assert result.classification == "NEAR_TRIGGER"
    assert result.trigger_extension_percent == Decimal("0.3636363636363636363636363636")
    assert result.risk_normalized_extension == Decimal("0.06666666666666666666666666667")
    assert result.seconds_since_trigger == Decimal("30")


def test_material_extension_is_explicitly_classified_without_authority() -> None:
    result = _assess("6.10")
    assert result.classification == "EXTENDED"
    assert result.risk_normalized_extension > Decimal("1")
    assert result.move_since_observation_percent > Decimal("20")
    assert result.vwap_extension_percent > Decimal("10")


def test_fresh_continuation_is_distinguished_from_old_trigger_extension() -> None:
    result = _assess("5.85", continuation=True)
    assert result.classification == "VALID_CONTINUATION"
    assert result.move_since_forming_percent > Decimal("10")


def test_invalid_context_is_unknown_and_never_authorizes() -> None:
    result = assess_entry_extension(
        entry_price=Decimal("5.50"), trigger_price=Decimal("5.50"),
        structural_stop=Decimal("5.50"),
    )
    assert result.classification == "UNKNOWN"
    assert result.trigger_extension_percent is None


def test_displacement_boundary_is_observational_and_constant_time() -> None:
    near = _assess("5.55")
    extended = _assess("5.551")
    assert near.classification == "NEAR_TRIGGER"
    assert extended.classification == "EXTENDED"
