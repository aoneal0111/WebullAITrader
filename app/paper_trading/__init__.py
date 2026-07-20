"""Deterministic paper simulation with no live-broker capabilities."""

from app.paper_trading.metrics import calculate_metrics
from app.paper_trading.models import *
from app.paper_trading.portfolio import create_portfolio
from app.paper_trading.simulator import simulate_proposal

__all__ = ["calculate_metrics", "create_portfolio", "simulate_proposal"]
