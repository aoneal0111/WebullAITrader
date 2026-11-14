import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from app.strategy_lab.crypto_monitor import CryptoMonitorReader, read_ledger, inspect_containers, prepare_launchers

NOW=datetime(2026,11,12,tzinfo=UTC)

def wallet(tmp_path, name='bollinger-paper-test/pilot/paper'):
    work=tmp_path/name;work.mkdir(parents=True)
    for filename,value in {
        'config.json':{'dry_run':True,'strategy':'FleetBollinger','exchange':{'name':'kraken'}},
        'profile.json':{'live_trading_enabled':False,'fleet_id':'a'*32,'key':'boll'},
        'engine-status.json':{'live_trading_enabled':False,'state':'running','updated_at':NOW.isoformat(),
            'fresh_ready_pairs':27,'pause_new_entries':False,'capture':{'writer_alive':True}}}.items():
        (work/filename).write_text(json.dumps(value))
    db=sqlite3.connect(work/'paper.sqlite3')
    db.execute('CREATE TABLE trades(id INTEGER,pair TEXT,amount REAL,open_rate REAL,is_open INTEGER,close_profit_abs REAL,realized_profit REAL)')
    db.executemany('INSERT INTO trades VALUES(?,?,?,?,?,?,?)',[(1,'BTC/USD',1,100,0,12,12),(2,'ETH/USD',2,50,0,-3,-3),(3,'QNT/USD',4,20,1,None,2)])
    db.execute('CREATE TABLE orders(order_id TEXT,ft_is_open INTEGER,status TEXT)')
    db.execute("INSERT INTO orders VALUES('test',1,'open')");db.commit();db.close()
    return work

def reader(base,**kw):return CryptoMonitorReader(base,clock=lambda:NOW,container_reader=lambda names:{},**kw)

def test_independent_wallets_and_no_open_wins(tmp_path):
    a=wallet(tmp_path);b=wallet(tmp_path,'nfi-crypto-paper-test/paper')
    report=reader(tmp_path).read();assert len(report['engines'])==2
    for row in report['engines']:
        assert row['closed_trades']==2 and row['closed_win_rate']==.5
        assert row['closed_lifecycle_net_usd']==9 and row['open_lifecycle_realized_usd']==2
        assert row['open_positions']==1 and row['unrealized_usd'] is None
        assert row['working_orders']==1
    assert report['orders_sent']==0
    assert read_ledger(tmp_path/'missing.sqlite3')['ledger_present'] is False
    assert not (tmp_path/'missing.sqlite3').exists()

def test_stale_and_container_stopped_are_visible(tmp_path):
    work=wallet(tmp_path);path=work/'engine-status.json';data=json.loads(path.read_text())
    data['updated_at']=(NOW-timedelta(minutes=5)).isoformat();path.write_text(json.dumps(data))
    assert reader(tmp_path).read()['engines'][0]['health']=='STALE_STATUS'
    monitor=CryptoMonitorReader(tmp_path,clock=lambda:NOW,container_reader=lambda names:{names[0]:{'running':False,'state':'exited'}})
    row=monitor.read()['engines'][0]
    assert row['health']=='CONTAINER_STOPPED' and row['process_liveness_verified']

def test_live_scope_rejected_without_hiding_others(tmp_path):
    work=wallet(tmp_path);wallet(tmp_path,'nfi-crypto-paper-test/paper')
    (work/'config.json').write_text('{"dry_run":false}')
    rows=reader(tmp_path).read()['engines']
    assert {r['health'] for r in rows}=={'READ_ERROR','RECENT_CHECKPOINT'}

def test_read_only_wal_and_no_ledger_mutation(tmp_path):
    work=wallet(tmp_path);path=work/'paper.sqlite3'
    db=sqlite3.connect(path);db.execute('PRAGMA journal_mode=WAL')
    db.execute("INSERT INTO trades VALUES(4,'SOL/USD',1,10,0,6,6)");db.commit()
    assert read_ledger(path)['closed_lifecycle_net_usd']==15
    assert db.execute('SELECT COUNT(*) FROM trades').fetchone()[0]==4
    db.close()

def test_inspect_only_returns_safe_fields():
    from types import SimpleNamespace
    with patch('app.strategy_lab.crypto_monitor.shutil.which',return_value='docker'),patch('app.strategy_lab.crypto_monitor.subprocess.run',return_value=SimpleNamespace(stdout='{"name":"/atlas-test","running":true,"state":"running"}\n')) as run:
        assert inspect_containers(['atlas-test'])['atlas-test']['running']
        command=run.call_args.args[0]
        assert command[:4]==['docker','inspect','--type','container']
        assert '.Config' not in command[5] and run.call_args.kwargs['timeout']==3

def test_prepare_only_writes_monitor_launchers(tmp_path):
    output=tmp_path/'launchers';prepare_launchers(tmp_path/'repo',output,tmp_path)
    assert len(list(output.iterdir()))==2
    assert '--check' in (output/'Show-Crypto-Monitor.ps1').read_text()
    assert 'app.gui.crypto_monitor' in (output/'Open-Crypto-Monitor.ps1').read_text()

def test_gui_displays_separate_wallets_and_reopens(tmp_path):
    import os
    os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
    from PySide6.QtWidgets import QApplication
    from app.gui.crypto_monitor import create_window
    app=QApplication.instance() or QApplication([])
    wallet(tmp_path);monitor=reader(tmp_path);window=create_window(monitor)
    window.render_report(monitor.read())
    assert window.tables['Bots'].rowCount()==1
    assert window.tables['Open positions'].rowCount()==1
    assert window.tables['Closed trades'].rowCount()==2
    window.show();app.processEvents();assert window.timer.isActive()
    window.close();assert not window.timer.isActive()
    window.show();app.processEvents();assert window.timer.isActive();window.close()

def test_crypto_navigation_does_not_activate_unrelated_supervisor():
    from PySide6.QtWidgets import QApplication
    from app.gui.widgets.asset_navigation import AssetNavigation
    from app.assets import AssetType
    app=QApplication.instance() or QApplication([])
    widget=AssetNavigation();events=[]
    widget.asset_selected.connect(events.append)
    widget.tabs.setCurrentIndex(widget.assets.index(AssetType.CRYPTO))
    assert events==[AssetType.CRYPTO] and widget.task is None
    assert widget.toggle.isHidden()
    assert 'PAPER BOTS' in widget.status.text()
    widget.close()


def test_old_capture_final_does_not_mark_restart_stopped(tmp_path):
    work=wallet(tmp_path);status=json.loads((work/'engine-status.json').read_text())
    status['recording_id']='new';(work/'engine-status.json').write_text(json.dumps(status))
    (work/'capture-final.json').write_text(json.dumps({'recording_id':'old','journal':{'closed':True,'updated_at':NOW.isoformat()}}))
    assert reader(tmp_path).read()['engines'][0]['health']=='RECENT_CHECKPOINT'

def test_bad_ledger_is_not_reported_as_zero_profit(tmp_path):
    work=wallet(tmp_path);(work/'paper.sqlite3').write_bytes(b'not sqlite')
    row=reader(tmp_path).read()['engines'][0]
    assert row['health']=='READ_ERROR' and 'closed_lifecycle_net_usd' not in row

def test_check_does_not_import_gui_or_execution(tmp_path):
    import subprocess,sys
    result=subprocess.run([sys.executable,'-c',
        'import sys; from app.gui.crypto_monitor import main; '
        'sys.argv=["monitor","--check","--no-docker","--base",sys.argv[1]]; main(); '
        'assert "PySide6" not in sys.modules; assert "app.composition.desktop" not in sys.modules',str(tmp_path)],
        capture_output=True,text=True,check=True)
    assert json.loads(result.stdout)['orders_sent']==0

def test_refresh_reads_off_gui_thread(tmp_path):
    from threading import Event
    from PySide6.QtWidgets import QApplication
    from app.gui.crypto_monitor import create_window
    app=QApplication.instance() or QApplication([]);entered=Event();release=Event()
    class SlowReader:
        base=tmp_path
        def read(self):
            entered.set();release.wait(3)
            return dict(engines=[],checked_at=NOW.isoformat())
    window=create_window(SlowReader());window.refresh()
    assert entered.wait(1) and window.busy
    # The GUI thread can close immediately while its daemon performs only reads.
    window.close();release.set()
