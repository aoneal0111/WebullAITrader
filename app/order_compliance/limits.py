from decimal import Decimal

from app.order_compliance.models import ComplianceLimits

DEFAULT_LIMITS = ComplianceLimits(
    maximum_daily_loss_amount=Decimal("500"),
    maximum_daily_loss_percent=Decimal("5"),
    maximum_trades_per_day=10,
    maximum_position_percent=Decimal("10"),
    maximum_gross_exposure_percent=Decimal("50"),
    maximum_market_status_age_seconds=30,
    allow_extended_hours=False,
    allow_market_orders_in_extended_hours=False,
)
