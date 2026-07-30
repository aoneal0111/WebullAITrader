"""GUI-specific formatting adapters.

Formatters convert application read models into immutable models consumed by
Qt widgets. They contain no runtime, broker, or domain behavior.
"""

from app.gui.formatters.orders import format_orders
from app.gui.formatters.positions import format_positions
from app.gui.formatters.decisions import format_decisions
from app.gui.formatters.portfolio import format_portfolio
from app.gui.formatters.health import format_health
from app.gui.formatters.watchlist import format_watchlist
from app.gui.formatters.watchlist import format_sorted_watchlist
from app.gui.formatters.replay import format_replay
from app.gui.formatters.timeline import format_timeline

__all__ = [
    "format_orders",
    "format_positions",
    "format_decisions",
    "format_portfolio",
    "format_health",
    "format_watchlist",
    "format_sorted_watchlist",
    "format_replay",
    "format_timeline",
]
