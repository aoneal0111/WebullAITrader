from datetime import UTC,datetime,timedelta
from decimal import Decimal as D
from dataclasses import replace
import importlib.util
from pathlib import Path

import pytest

from app.asset_modules.contract_logic import Bar
from app.asset_modules.crypto_signals import CryptoHistory,CryptoQuote,CryptoSignalSettings,propose

path=Path(__file__).resolve().parents[2]/'tools/crypto_warmup_study.py'
spec=importlib.util.spec_from_file_location('warmup_study',path)
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
NOW=datetime(2026,10,25,15,0,tzinfo=UTC)


def bars(prices):
    return tuple(Bar(NOW-timedelta(minutes=len(prices)-i-1),p+D('.01'),p-D(1),p)
                 for i,p in enumerate(prices))


def test_actual_direction_disagreement_and_native_proposal_are_reproducible():
    b=bars([D(110)]*144+[D(100)]*47+[D('100.02')])
    r=m.analyze(b,now=NOW)
    assert r['supplied_full_history_direction'] is None
    short=next(c for c in r['cells'] if c['bars']==48)
    assert short['direction']=='LONG' and short['differs_from_supplied_full_history']
    # Exercise real native signal generation with an invented quote, not an order.
    proposal,reason=propose(CryptoHistory('TEST/USD',b[-48:]),
        CryptoQuote('TEST/USD',D('100.02'),D('100.03'),NOW),NOW,CryptoSignalSettings())
    assert reason=='PROPOSED' and proposal is not None
    assert r['execution_authorized'] is False and r['orders']==0


def test_constant_series_does_not_claim_universal_convergence():
    r=m.analyze(bars([D(100)]*192),now=NOW)
    assert r['status']=='NO_DIRECTION_DIFFERENCE_ON_SUPPLIED_PATH'
    assert all(c['direction'] is None for c in r['cells'])
    assert next(c for c in r['cells'] if c['bars']==384)['status']=='INSUFFICIENT_HISTORY'


def test_fixed_uptrend_is_directionally_stable_across_windows():
    r=m.analyze(bars([D(100)+D(i)/10 for i in range(192)]),now=NOW)
    assert r['supplied_full_history_direction']=='LONG'
    assert all(c['direction']=='LONG' for c in r['cells'] if c['status']=='ASSESSED')


def test_future_or_gapped_or_stale_history_refused():
    b=list(bars([D(100)]*192))
    for invalid in (b[:-2]+b[-1:],b+( [replace(b[-1],end=NOW+timedelta(minutes=1))] )):
        with pytest.raises(ValueError):m.analyze(invalid,now=NOW)
    with pytest.raises(ValueError):m.analyze(b,now=NOW+timedelta(minutes=2))


def test_bounds_and_input_immutability():
    b=bars([D(100)]*48);before=tuple(b)
    r=m.analyze(b,now=NOW)
    assert b==before and len(r['cells'])==6
    for invalid in (b[:21],b*22):
        with pytest.raises(ValueError):m.analyze(invalid,now=NOW)
    with pytest.raises(ValueError):m.analyze(b,now=NOW.replace(tzinfo=None))
