from decimal import Decimal as D
import pytest
from app.strategy_lab.cost_attribution import attribute, cost_floor, analyze


def trade(**changes):
    return dict(amount=1, open_rate=100, close_rate=102, fee_open=.004,
                fee_close=.004, profit_abs=1.192, exit_reason='roi', **changes)


def test_known_fill_and_break_even_payoff():
    loss = trade(); loss.update(close_rate=98, profit_abs=-2.792, exit_reason='stop_loss')
    result = attribute([trade(), loss])
    assert result['gross_fill_profit'] == 0
    assert result['modeled_commissions'] == D('1.600')
    assert result['reported_net'] == D('-1.600')
    assert result['break_even_win_rate_at_observed_payoffs'] == D('2.792') / D('3.984')


def test_commission_floor_and_embedded_spread_are_distinct_profiles():
    assert cost_floor('COMMISSION', '.008', '.008') == D('1.008') / D('.992') - 1
    assert cost_floor('EMBEDDED_MARKUP_MARKDOWN', '.01', '.01') == D('1.01') / D('.99') - 1
    assert cost_floor('EMBEDDED_MARKUP_MARKDOWN', '.01', '.01') > D('.02')


@pytest.mark.parametrize('changes', [dict(is_short=True), dict(leverage=2),
    dict(fee_open=-.1), dict(amount=0), dict(profit_abs=7), dict(open_rate='NaN')])
def test_refuses_unsupported_or_unreconciled_fills(changes):
    t = trade(); t.update(changes)
    with pytest.raises(ValueError): attribute([t])


def test_no_trades_does_not_invent_payoffs():
    result = attribute([])
    assert result['break_even_win_rate_at_observed_payoffs'] is None


def test_incomplete_source_refused(tmp_path):
    (tmp_path / 'candidate-screen-summary.json').write_text('{"status":"PARTIAL"}')
    with pytest.raises(ValueError): analyze(tmp_path)
