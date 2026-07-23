from decimal import Decimal
from app.analytics import DrawdownStatus
from tests.analytics.helpers import entry,request,runtime

def test_equity_curve_skips_missing_contiguous_and_cumulative_pnl():
    entries=(entry(0,None,"100"),entry(1,"5",None),entry(2,None,"90"),entry(3,"-2","110"))
    curve=runtime().evaluate(request(entries)).summary.equity_curve
    assert tuple(x.sequence for x in curve)==(0,1,2) and tuple(x.entry_id for x in curve)==("entry-0","entry-2","entry-3")
    assert tuple(x.cumulative_net_profit for x in curve)==(None,Decimal("5"),Decimal("3"))

def test_drawdown_peak_decline_recovery_and_metrics():
    entries=(entry(0,"1","100"),entry(1,"1","80"),entry(2,"1","100"),entry(3,"1","120"),entry(4,"1","90"))
    result=runtime().evaluate(request(entries)).summary;curve=result.drawdown_curve;m=result.metrics
    assert tuple(x.peak_equity for x in curve)==(100,100,100,120,120)
    assert tuple(x.drawdown_amount for x in curve)==(0,20,0,0,30)
    assert tuple(x.status for x in curve)==(DrawdownStatus.AT_PEAK,DrawdownStatus.IN_DRAWDOWN,DrawdownStatus.AT_PEAK,DrawdownStatus.AT_PEAK,DrawdownStatus.IN_DRAWDOWN)
    assert m.maximum_drawdown_amount==30 and m.maximum_drawdown_percentage==Decimal("0.25")
    assert m.current_drawdown_amount==30 and m.current_drawdown_percentage==Decimal("0.25")

def test_zero_peak_percentage_none():
    point=runtime().evaluate(request((entry(equity="0",starting="0"),))).summary.drawdown_curve[0]
    assert point.drawdown_amount==0 and point.drawdown_percentage is None

def test_empty_curves_and_optional_drawdown_metrics():
    summary=runtime().evaluate(request((entry(equity=None),))).summary
    assert summary.equity_curve==() and summary.drawdown_curve==()
    assert summary.metrics.maximum_drawdown_amount is None and summary.metrics.current_drawdown_amount is None

def test_curve_policy_exclusion():
    summary=runtime(include_equity_curve=False,include_drawdown_curve=False).evaluate(request((entry(),))).summary
    assert summary.equity_curve==() and summary.drawdown_curve==() and summary.metrics.maximum_equity==100
