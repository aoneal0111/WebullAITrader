import numpy as np
import pytest
pytest.importorskip('numba',reason='Optional accelerated research environment required')
from app.strategy_lab.daily_income_lab import Spec,metrics
from app.strategy_lab.equity_income_study import simulate


def test_equity_marks_and_pending_entries_survive_weekend():
    # A Friday signal enters at Monday's open. Saturday/Sunday are not bars.
    values=np.array([[100,101,99,100,1e6],[110,111,109,110,1e6]],dtype=float)[:,None,:]
    atr=np.full((2,1),2.);buy=np.array([[True],[False]]);sell=np.zeros((2,1),dtype=bool)
    packed=(values,atr,buy,sell,np.array([0,3],dtype=np.int64),4)
    p=dict(slippage_per_side=0.,slots=1)
    result=simulate(packed,Spec('TEST',1,100.,100.,720),p,1000.,0.)
    assert result[0][0]==1
    np.testing.assert_allclose(result[1],[1000.,1000.,1000.,1000.])
    m=metrics(result,1000.,overhead=2.)
    assert m['calendar_days']==4 and m['terminal_net_after_overhead']==pytest.approx(-8.)


def test_weekend_does_not_reset_open_position_mark_to_initial():
    # Position gains on Friday, remains marked through the weekend, exits Monday.
    values=np.array([[100,101,99,100,1e6],[100,121,99,120,1e6],
                     [120,121,119,120,1e6]],dtype=float)[:,None,:]
    packed=(values,np.full((3,1),2.),np.array([[True],[False],[False]]),
            np.zeros((3,1),dtype=bool),np.array([0,1,4],dtype=np.int64),5)
    r=simulate(packed,Spec('TEST',1,100.,100.,720),dict(slippage_per_side=0.,slots=1),1000.,0.)
    np.testing.assert_allclose(r[1],[1000.,1200.,1200.,1200.,1200.])
