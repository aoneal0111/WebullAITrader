import pytest

from app.execution_coordinator.runtime_context_assembler import (
    RuntimeContextAssembler,
)
from app.order_compliance.account_state_builder import build_account_state


def test_runtime_context_assembler_uses_default_account_state_builder() -> None:
    assembler = RuntimeContextAssembler()

    assert assembler.account_state_builder is build_account_state


def test_build_assembles_coordination_context() -> None:
    expected_account_state = object()
    calls = []

    def fake_account_state_builder(**kwargs):
        calls.append(kwargs)
        return expected_account_state

    assembler = RuntimeContextAssembler(
        account_state_builder=fake_account_state_builder
    )

    portfolio = object()
    account_type = object()
    timestamp = object()
    market_state = object()
    risk_limits = object()
    compliance_limits = object()
    gfv_decision = object()
    kill_switch = object()
    market_quote = object()
    execution_config = object()
    journal = object()
    equity_curve = object()

    context = assembler.build(
        portfolio=portfolio,
        account_type=account_type,
        filled_orders=7,
        symbol="AAPL",
        timestamp=timestamp,
        market_state=market_state,
        risk_limits=risk_limits,
        compliance_limits=compliance_limits,
        gfv_decision=gfv_decision,
        kill_switch=kill_switch,
        market_quote=market_quote,
        execution_config=execution_config,
        journal=journal,
        equity_curve=equity_curve,
    )

    assert calls == [
        {
            "portfolio": portfolio,
            "account_type": account_type,
            "filled_orders": 7,
            "symbol": "AAPL",
            "timestamp": timestamp,
        }
    ]

    assert context.account_state is expected_account_state
    assert context.market_state is market_state
    assert context.risk_limits is risk_limits
    assert context.compliance_limits is compliance_limits
    assert context.gfv_decision is gfv_decision
    assert context.kill_switch is kill_switch
    assert context.portfolio is portfolio
    assert context.market_quote is market_quote
    assert context.execution_config is execution_config
    assert context.journal is journal
    assert context.equity_curve is equity_curve
