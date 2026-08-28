"""Central display policy for per-share prices.

Prices retain up to four meaningful decimal places while always showing cents.
This keeps sub-$10 ticks and structural trigger/stop levels visible without
adding zeroes that imply precision absent from the underlying value.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation


def format_price(value: Decimal | str | int | float | None) -> str:
    if value is None:
        return "--"
    try:
        number = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("price must be Decimal-compatible") from exc
    if not number.is_finite():
        raise ValueError("price must be finite")

    rendered = f"{number:,.4f}"
    whole, fractional = rendered.rsplit(".", 1)
    fractional = fractional.rstrip("0")
    if len(fractional) < 2:
        fractional = fractional.ljust(2, "0")
    return f"{whole}.{fractional}"


__all__ = ["format_price"]
