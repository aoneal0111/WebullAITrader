from datetime import date,timedelta
import json
import pytest
from app.strategy_research.source_candidate_lab import features,signals,replay,load_csv
from app.strategy_research.source_candidate_capture import capture,SYMBOLS


def bars():
    closes=[100+i*.2 for i in range(220)]+[141,139,138,137,140,143,144,145]
    return {(date(2020,1,1)+timedelta(days=i)).isoformat():dict(open=c+.1,high=c+1,low=c-1,close=c,volume=1000) for i,c in enumerate(closes)}


def test_signals_cannot_see_later_prices():
    b=bars();rows={'SPY':b,'XYZ':b};cut=sorted(b)[224]
    for mode in ('RSI2_SPY_REGIME','CONNORS_RSI2'):
        full=signals(rows,mode);prefix=signals({s:{d:v for d,v in b.items() if d<=cut} for s in rows},mode)
        assert prefix=={k:v for k,v in full.items() if k[0]<=cut}
        assert any(x[0] for x in full.values())


def test_next_open_shared_cash_and_fee_accounting():
    b=bars();rows={'SPY':b,'XYZ':b};start=sorted(b)[210]
    result=replay(rows,'RSI2_SPY_REGIME',start,wallet=1000,stake=1000,overhead=7)
    assert result['closed_trades']>0
    for t in result['trades']:
        assert t['signal_at']<t['entry_at'] and t['exit_signal_at']<t['exit_at']
        assert t['net_pnl']==pytest.approx(t['quantity']*(t['exit_price']*.9995-t['entry_price']*1.0005))
    assert result['ending_cash']>=0
    assert result['realized_after_fixed_overhead']==result['realized_net']-7
    assert result['ending_marked_equity']==pytest.approx(1000+result['realized_net'])


def test_missing_spy_and_duplicate_or_nonfinite_rows_refuse(tmp_path):
    p=tmp_path/'x.csv'
    for body in ['2020-01-01,XYZ,1,2,1,2,5\n',
                 '2020-01-01,SPY,1,2,1,2,5\n'*2,
                 '2020-01-01,SPY,nan,2,1,2,5\n']:
        p.write_text('date,symbol,open,high,low,close,volume\n'+body)
        with pytest.raises(ValueError):load_csv(p)


def test_capture_keeps_failed_evidence_without_feed_fallback(tmp_path):
    calls=[]
    def fetch(params,credentials):
        calls.append(params)
        return json.dumps(dict(bars={'SPY':[dict(t='2030-01-01T05:00:00Z',o=1,h=2,l=1,c=2,v=5)]},next_page_token=None)).encode()
    with pytest.raises(ValueError,match='OUTSIDE'):
        capture(tmp_path,tmp_path/'capture',fetch=fetch,credentials=('fixture','fixture'))
    report=json.loads((tmp_path/'capture/capture-summary.json').read_text())
    assert report['status']=='PARTIAL' and report['attempts']==1
    assert calls[0]['feed']=='sip' and calls[0]['adjustment']=='all'
    assert (tmp_path/'capture/page-000.json').exists()
