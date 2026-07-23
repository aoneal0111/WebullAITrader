"""Narrow immediate dependency contracts for Live Trading."""
from typing import Protocol
from app.broker import BrokerOrderExecutor
from app.research_portfolio import ResearchPortfolioRequest,ResearchPortfolioResult

class ResearchPortfolioExecutor(Protocol):
    def run(self,request:ResearchPortfolioRequest)->ResearchPortfolioResult:...
__all__=("ResearchPortfolioExecutor","BrokerOrderExecutor")
