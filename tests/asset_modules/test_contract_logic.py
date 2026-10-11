from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

import pytest

from app.asset_modules.contract_logic import Bar, Contract, Quote, direction, exit_reason, option_plans, plan
from app.asset_modules.engine_catalog import EngineId

NOW = datetime(2026, 10, 12, 15, tzinfo=UTC)


def contract(engine=EngineId.OPTIONS, **changes):
    values = dict(symbol="QQQ_CALL", engine=engine, expires_at=NOW + timedelta(days=5),
                  multiplier=D(100), tick=D("0.01"), margin=D(1000),
                  underlying="QQQ", right="CALL", strike=D(500))
    values.update(changes)
    return Contract(**values)


def quote(symbol="QQQ_CALL", bid="1.00", ask="1.01", at=NOW):
    return Quote(symbol, D(bid), D(ask), at, at)


def make_plan(c=None, q=None, **changes):
    values = dict(side="LONG", stop_distance=D("0.02"), target_distance=D("0.10"),
                  risk_budget=D(25), capital_budget=D(500), fee_per_side=D("0.65"),
                  hold=timedelta(seconds=60))
    values.update(changes)
    return plan(c or contract(), q or quote(), NOW, **values)


def test_premium_multiplier_and_cost_inclusive_integer_sizing():
    p = make_plan()
    assert p.quantity == 4  # premium capital, not the stock price or fractional contracts
    assert p.stop == D("0.99") and p.target == D("1.11")
    assert p.quantity * (D(2) + D(1) + D("1.30")) <= D(25)


def test_calls_and_puts_are_bought_for_opposite_underlying_directions():
    call, put = contract(), contract(symbol="QQQ_PUT", right="PUT")
    kwargs = dict(stop_distance=D("0.02"), target_distance=D("0.10"),
                  risk_budget=D(25), capital_budget=D(500), fee_per_side=D("0.65"))
    quotes = {c.symbol: quote(c.symbol) for c in (call, put)}
    assert option_plans((call, put), quotes, "QQQ", "LONG", NOW, **kwargs)[0].contract == call
    assert option_plans((call, put), quotes, "QQQ", "SHORT", NOW, **kwargs)[0].contract == put
    with pytest.raises(ValueError):
        make_plan(side="SHORT")


@pytest.mark.parametrize("q", [quote(at=NOW-timedelta(seconds=3)),
                               quote(at=NOW+timedelta(milliseconds=1)),
                               quote(symbol="QQQ"), quote(bid="1.02")])
def test_unusable_quotes_do_not_produce_entries(q):
    assert make_plan(q=q) is None


def test_spread_and_fees_can_consume_a_small_premium_target():
    assert make_plan(q=quote(bid="0.97"), target_distance=D("0.05")) is None


def test_expiry_and_tick_are_enforced():
    assert make_plan(c=contract(expires_at=NOW+timedelta(seconds=60))) is None
    with pytest.raises(ValueError):
        make_plan(stop_distance=D("0.025"))


def test_futures_margin_tick_value_and_short_protection():
    c = contract(EngineId.FUTURES, symbol="MICRO", multiplier=D(5), tick=D("0.25"), margin=D(1000))
    p = make_plan(c, quote("MICRO", "5000", "5000.25"), side="SHORT",
                  stop_distance=D(1), target_distance=D(3), capital_budget=D(2500))
    assert p.quantity == 2
    assert p.stop == D(5001) and p.target == D(4997)
    assert exit_reason(p, quote("MICRO", "5000.75", "5001"), NOW) == "STOP"
    assert exit_reason(p, quote("MICRO", "4996.75", "4997"), NOW) == "TARGET"


def test_long_exits_use_bid_and_stale_quote_cannot_liquidate():
    p = make_plan()
    assert exit_reason(p, quote(bid="1.08", ask="1.11"), NOW) is None
    assert exit_reason(p, quote(bid="1.11", ask="1.12"), NOW) == "TARGET"
    assert exit_reason(p, quote(at=NOW-timedelta(seconds=5)), NOW) is None
    later = NOW+timedelta(seconds=60)
    assert exit_reason(p, quote(at=later), later) == "MAX_HOLD"


def test_breakout_uses_prior_range_and_completed_ordered_bars():
    bars = tuple(Bar(NOW-timedelta(minutes=21-i), D(100+i), D(99+i), D(100+i)) for i in range(22))
    assert direction(bars, NOW) == "LONG"
    assert direction(bars, NOW+timedelta(minutes=2)) is None
    assert direction(bars[:10], NOW) is None
    with pytest.raises(ValueError):
        direction(tuple(reversed(bars)), NOW)
    assert direction(bars[:-2] + bars[-1:], NOW) is None  # missing completed minute


def test_contracts_outside_options_scope_are_rejected():
    with pytest.raises(ValueError):
        contract(underlying="AAPL")
    with pytest.raises(ValueError):
        contract(multiplier=D("NaN"))
