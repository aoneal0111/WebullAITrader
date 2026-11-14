"""Disconnected archive integrity checks; no market-performance assertions."""
from io import BytesIO
import zipfile
import pytest
from app.strategy_lab import archive_history as history


def test_sparse_archive_rows_are_preserved_without_invented_bars():
    csv=b'0,10,11,9,10,2,3\n600,10,12,9,11,4,5\n'
    assert history.parse_member(BytesIO(csv),5,0,900)==[
        [0,10.,11.,9.,10.,2.],[600000,10.,12.,9.,11.,4.]]
    assert history.parse_member(BytesIO(csv),5,300,900)==[[600000,10.,12.,9.,11.,4.]]


@pytest.mark.parametrize('csv',[
    b'1,10,11,9,10,2,3\n',b'0,10,9,9,10,2,3\n',
    b'0,10,11,9,nan,2,3\n',b'0,10,11,9,10,-1,3\n',
    b'0,10,11,9,10,2,3\n0,10,11,9,10,2,3\n',b'timestamp,open,high,low,close,volume,trades\n'])
def test_malformed_archive_candles_refused(csv):
    with pytest.raises(ValueError):history.parse_member(BytesIO(csv),5,0,900)


def test_exact_member_alias_and_ambiguity():
    data=BytesIO()
    with zipfile.ZipFile(data,'w') as z:z.writestr('candles/XBTUSD_5.csv','0,1,1,1,1,1,1\n')
    data.seek(0)
    with zipfile.ZipFile(data) as z:
        assert history.select_member(z,'BTC/USD',5).filename=='candles/XBTUSD_5.csv'
        with pytest.raises(ValueError):history.select_member(z,'BTC/USD',15)
    with zipfile.ZipFile(data,'a') as z:z.writestr('BTCUSD_5.csv','0,1,1,1,1,1,1\n')
    data.seek(0)
    with zipfile.ZipFile(data) as z:
        with pytest.raises(ValueError):history.select_member(z,'BTC/USD',5)


def test_range_reader_concatenates_parts_and_requires_exact_ranges(monkeypatch):
    parts={history.FULL[0]:b'abcdef',history.FULL[1]:b'ghijkl'}
    requests=[];bad=[False]
    class Response(BytesIO):
        def __init__(self,url,data,status,headers):
            super().__init__(data);self.url=url;self.status=status;self.headers=headers
        def geturl(self):return self.url
    def urlopen(request,timeout):
        url=request.full_url;data=parts[url];requests.append(request)
        if request.get_method()=='HEAD':return Response(url,b'',200,{'Content-Length':str(len(data)),'ETag':'"fixed"'})
        assert request.get_header('If-match')=='"fixed"'
        lo,hi=map(int,request.get_header('Range').split('=')[1].split('-'))
        return Response(url,data[lo:hi+1],200 if bad[0] else 206,
            {'ETag':'"fixed"','Content-Range':f'bytes {lo}-{hi}/{len(data)}'})
    monkeypatch.setattr(history.urllib.request,'urlopen',urlopen)
    budget=history.Budget()
    with history.RangeZip(list(parts),budget) as reader:
        assert reader.read(12)==b'abcdefghijkl'
        reader.seek(-4,2);assert reader.read()==b'ijkl'
    assert budget.reads==4 and budget.bytes==12
    bad[0]=True
    with history.RangeZip(list(parts),history.Budget()) as reader:
        with pytest.raises(ValueError,match='EXACT_ARCHIVE_RANGE_REQUIRED'):reader.read(1)
    monkeypatch.setattr(history,'MAX_BYTES',1)
    with pytest.raises(ValueError,match='BOUND'):history.Budget().reserve(2)


def test_archive_failure_keeps_diagnostics_and_never_reuses_output(tmp_path):
    import json
    def failed(*args):raise TimeoutError('public timeout')
    output=tmp_path/'history'
    with pytest.raises(TimeoutError):history.import_history(output,opener=failed)
    manifest=json.loads((output/'history-manifest.json').read_text())
    assert manifest['status']=='PARTIAL' and manifest['orders']==manifest['grok_requests']==0
    assert manifest['error_code']=='ARCHIVE_READ_FAILED'
    with pytest.raises(FileExistsError):history.import_history(output,opener=failed)


def test_resume_refuses_changed_completed_frames(tmp_path):
    import json
    root=tmp_path/'history';(root/'candles').mkdir(parents=True)
    file=root/'candles/BTC_USD-5m.json';file.write_text('[]')
    manifest={'scope':'FROZEN_KRAKEN_Q3_2026_HISTORY_V1','status':'PARTIAL','pairs':list(history.PAIRS),
        'warmup_start':'2024-06-01T00:00:00+00:00','evaluation_start':'2026-07-01T00:00:00+00:00',
        'evaluation_end_exclusive':'2026-10-01T00:00:00+00:00','requests':0,'downloaded_bytes':0,
        'files':{file.name:{'pair':'BTC/USD','timeframe':'5m','sha256':'wrong'}}}
    (root/'history-manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError,match='RESUME_CANDLE_CHANGED'):history.import_history(root,resume=True)
    assert file.read_text()=='[]'
