"""GUI-specific formatting adapters.

Formatters convert application read models into immutable models consumed by
Qt widgets. They contain no runtime, broker, or domain behavior.
"""

from app.gui.formatters.orders import format_orders
from app.gui.formatters.positions import format_positions
from app.gui.formatters.decisions import format_decisions

__all__ = [
    "format_orders",
    "format_positions",
    "format_decisions",
]
