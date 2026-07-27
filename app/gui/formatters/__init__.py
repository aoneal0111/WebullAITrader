"""GUI-specific formatting adapters.

Formatters convert application read models into immutable models consumed by
Qt widgets. They contain no runtime, broker, or domain behavior.
"""

from app.gui.formatters.orders import format_orders

__all__ = [
    "format_orders",
]
