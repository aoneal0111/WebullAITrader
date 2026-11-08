import json
import pytest
pytest.importorskip('numba',reason='Optional accelerated research environment required')
from app.strategy_lab.current_income_grid import frame_from_raw


def raw(times):
    return json.dumps(dict(error=[],result={'XXBTZUSD':[
        [t,'100','101','99','100','100','12',4] for t in times],'last':9000})).encode()


def test_current_incomplete_candle_is_excluded():
    frame=frame_from_raw(raw([0,3600,7200]),7500,1)
    assert len(frame)==2
    assert frame.date.iloc[-1].timestamp()==3600


def test_duplicate_current_bars_are_refused():
    with pytest.raises(ValueError,match='UNIQUE_COMPLETED'):
        frame_from_raw(raw([0,0,3600]),7500,1)


def test_nonchronological_current_bars_are_refused():
    with pytest.raises(ValueError,match='UNIQUE_COMPLETED'):
        frame_from_raw(raw([3600,0]),7500,1)


def test_provider_rejection_does_not_become_an_empty_strategy_result():
    with pytest.raises(ValueError,match='PROVIDER_REJECTED'):
        frame_from_raw(json.dumps(dict(error=['EQuery:Unknown asset pair'])).encode(),7500,1)
