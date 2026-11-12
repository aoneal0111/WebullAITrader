"""Read-only local monitoring of independent native crypto PAPER wallets."""
import argparse
from datetime import UTC, datetime
import json
import math
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
from time import monotonic

MAX_JSON = 2 * 1024**2
MAX_ENGINES = 32
PATTERNS = ('bollinger-paper-*/pilot/paper', 'nfi-crypto-paper-*/paper',
            'crypto-weekend-*/paper-fleet/e0', 'crypto-weekend-*/paper-fleet/nasos',
            'crypto-weekend-*/paper-fleet/bb', 'crypto-weekend-*/paper-fleet/crsi',
            'crypto-weekend-*/paper-fleet/wave', 'paper-candidate-addon-*/paper/crypto')
LABELS = {'FleetBollinger':'Bollinger 4h', 'NostalgiaForInfinityX8':'NFI',
          'FleetE0V1E':'E0V1E', 'FleetNASOS':'NASOS', 'FleetBBRPB':'BB RPB',
          'FleetCRSI':'CRSI', 'FleetWave':'Wave', 'FleetROCPullback':'ROC pullback'}


def default_base():
    return Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'AtlasResearch'


def read_json(path):
    if not path.is_file():return {}
    if path.stat().st_size > MAX_JSON:raise ValueError('MONITOR_JSON_BOUND')
    with path.open('rb') as stream:raw=stream.read(MAX_JSON+1)
    if len(raw)>MAX_JSON:raise ValueError('MONITOR_JSON_BOUND')
    value=json.loads(raw.decode('utf-8-sig'))
    if not isinstance(value,dict):raise ValueError('MONITOR_JSON_OBJECT_REQUIRED')
    return value


def finite(value):
    if value is None:return None
    number=float(value)
    return number if math.isfinite(number) else None


def read_ledger(path):
    """One consistent read transaction, including WAL; never create a database."""
    if not path.is_file():return dict(ledger_present=False,holdings=[],closed=[],orders=[])
    if path.stat().st_size>512*1024**2:raise ValueError('MONITOR_LEDGER_BOUND')
    began=monotonic()
    db=sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=.25)
    db.row_factory=sqlite3.Row
    db.set_progress_handler(lambda: int(monotonic()-began>1),1000)
    try:
        db.execute('PRAGMA query_only=ON');db.execute('BEGIN')
        columns={r[1] for r in db.execute('PRAGMA table_info(trades)')}
        if not {'id','pair','amount','open_rate','is_open','close_profit_abs'}<=columns:
            raise ValueError('NATIVE_CRYPTO_TRADE_SCHEMA_REQUIRED')
        count,wins,losses,net=db.execute('SELECT COUNT(*),COALESCE(SUM(close_profit_abs>0),0),'
            'COALESCE(SUM(close_profit_abs<0),0),COALESCE(SUM(close_profit_abs),0) FROM trades WHERE is_open=0').fetchone()
        result=dict(ledger_present=True,closed_trades=count,wins=wins,losses=losses,
            closed_win_rate=wins/count if count else None,closed_lifecycle_net_usd=finite(net),
            open_positions=db.execute('SELECT COUNT(*) FROM trades WHERE is_open=1').fetchone()[0],
            open_lifecycle_realized_usd=finite(db.execute('SELECT COALESCE(SUM(realized_profit),0) FROM trades WHERE is_open=1').fetchone()[0]) if 'realized_profit' in columns else None,
            unrealized_usd=None,open_positions_counted_as_wins=False)
        fields=[n for n in ('id','pair','amount','open_rate','close_rate','open_date','close_date',
                           'close_profit_abs','exit_reason','realized_profit') if n in columns]
        sql=','.join('"'+n+'"' for n in fields)
        result['holdings']=[dict(r) for r in db.execute(f'SELECT {sql} FROM trades WHERE is_open=1 ORDER BY id DESC LIMIT 50')]
        result['closed']=[dict(r) for r in db.execute(f'SELECT {sql} FROM trades WHERE is_open=0 ORDER BY id DESC LIMIT 20')]
        order_columns={r[1] for r in db.execute('PRAGMA table_info(orders)')}
        fields=[n for n in ('order_id','ft_trade_id','ft_order_side','symbol','order_type','status',
                           'price','amount','filled','remaining','order_date') if n in order_columns]
        result['working_orders']=None;result['orders']=[]
        if fields and 'ft_is_open' in order_columns:
            result['working_orders']=db.execute('SELECT COUNT(*) FROM orders WHERE ft_is_open=1').fetchone()[0]
            sql=','.join('"'+n+'"' for n in fields)
            result['orders']=[dict(r) for r in db.execute(f'SELECT {sql} FROM orders WHERE ft_is_open=1 LIMIT 50')]
        return result
    finally:db.close()


def container_identity(work,profile):
    identifier=profile.get('fleet_id','');key=profile.get('key')
    if re.fullmatch('[0-9a-f]{32}',str(identifier)):
        if key=='boll':return 'atlas-bollinger-paper-'+identifier
        if key=='roc':return 'atlas-paper-addons-'+identifier+'-crypto'
        if key in ('e0','nasos','bb','crsi','wave'):return 'atlas-crypto-'+identifier+'-'+key
    match=re.fullmatch(r'nfi-crypto-paper-([0-9a-f]{32})',work.parent.name)
    return 'atlas-nfi-paper-'+match[1] if match else None


def inspect_containers(names):
    names=sorted({n for n in names if n and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}',n)})
    docker=shutil.which('docker')
    if not docker or not names:return {}
    template='{"name":{{json .Name}},"running":{{json .State.Running}},"state":{{json .State.Status}}}'
    try:
        value=subprocess.run([docker,'inspect','--type','container','--format',template,*names],
            capture_output=True,text=True,timeout=3,check=False)
        if len(value.stdout)>65536:return {}
        rows={}
        for line in value.stdout.splitlines():
            item=json.loads(line);name=item.get('name','').lstrip('/')
            if name in names and type(item.get('running')) is bool:
                rows[name]=dict(running=item['running'],state=str(item.get('state','UNKNOWN'))[:32])
        return rows
    except (OSError,subprocess.TimeoutExpired,ValueError,TypeError):return {}


class CryptoMonitorReader:
    def __init__(self,base=None,*,workdirs=None,clock=lambda:datetime.now(UTC),container_reader=inspect_containers):
        self.base=Path(base or default_base()).resolve()
        self.workdirs=None if workdirs is None else tuple(Path(p).resolve() for p in workdirs)
        self.clock,self.container_reader=clock,container_reader

    def discover(self):
        if self.workdirs is not None:return tuple(dict.fromkeys(self.workdirs))[:MAX_ENGINES]
        found=[]
        for pattern in PATTERNS:
            for path in sorted(self.base.glob(pattern),reverse=True)[:64]:
                resolved=path.resolve()
                if resolved.is_relative_to(self.base) and (resolved/'config.json').is_file():found.append(resolved)
        return tuple(dict.fromkeys(found))[:MAX_ENGINES]

    def _read_engine(self,work,now):
        row=dict(id=str(work),work=str(work),strategy=work.parent.name,label=work.parent.name,
            health='UNAVAILABLE',container=None,container_running=None,process_liveness_verified=False,
            ledger_present=False,holdings=[],closed=[],orders=[],live_trading_enabled=None)
        try:
            config=read_json(work/('resolved-config.json' if (work/'resolved-config.json').is_file() else 'config.json'))
            profile=read_json(work/'profile.json') or read_json(work/'paper-manifest.json')
            engine=read_json(work/'engine-status.json');final=read_json(work/'capture-final.json')
            if config.get('dry_run') is not True or engine.get('live_trading_enabled',False) is not False:
                raise ValueError('PAPER_SCOPE_REQUIRED')
            if not profile or profile.get('live_trading_enabled') is not False:
                raise ValueError('PAPER_PROFILE_REQUIRED')
            strategy=str(config.get('strategy',profile.get('strategy','UNKNOWN')))[:80]
            key=str(profile.get('fleet_id',work.parent.name))[-8:]
            row.update(strategy=strategy,label=LABELS.get(strategy,strategy)+' · '+key,
                container=container_identity(work,profile),live_trading_enabled=False,
                exchange=str(config.get('exchange',{}).get('name','UNKNOWN'))[:32],
                timeframe=config.get('timeframe') or profile.get('timeframe'),
                updated_at=engine.get('updated_at'),state=str(engine.get('state','NOT_STARTED')).upper(),
                pause_new_entries=engine.get('pause_new_entries'),capture_error=engine.get('capture_error'))
            from app.strategy_lab.nfi_readiness import runtime_freshness
            fresh=runtime_freshness({'engine-status.json':engine,'capture-final.json':final},now)
            row.update(health=fresh['state'],checkpoint_age_seconds=fresh.get('checkpoint_age_seconds'))
            if row['state'] in ('FAILED','STOPPED'):row['health']=row['state']
            capture=engine.get('capture') or {}
            row.update(writer_alive=capture.get('writer_alive'),evidence_complete=capture.get('evidence_complete'),
                capacity_reached=capture.get('capacity_reached'),
                fresh_ready_pairs=engine.get('fresh_ready_pairs') if fresh['state']=='RECENT_CHECKPOINT' else None,
                source_states=(engine.get('source_readiness') or {}).get('latest_counts',{}),
                entry_reviews=(engine.get('source_readiness') or {}).get('entry_reviews',{}))
            if row['capture_error'] or capture.get('writer_failed') or capture.get('capacity_reached'):
                row['health']='CAPTURE_ERROR'
            if (work/'PAUSE_NEW_ENTRIES').is_file():row['pause_new_entries']=True
            row.update(read_ledger(work/('nfi-paper.sqlite3' if (work/'paper-manifest.json').is_file() else 'paper.sqlite3')))
        except (OSError,ValueError,TypeError,sqlite3.Error) as error:
            row.update(health='READ_ERROR',read_error=type(error).__name__)
        return row

    def read(self):
        now=self.clock();rows=[self._read_engine(work,now) for work in self.discover()]
        states=self.container_reader([row['container'] for row in rows if row['container']])
        for row in rows:
            actual=states.get(row['container'])
            if actual:
                row.update(container_running=actual['running'],container_state=actual['state'],process_liveness_verified=True)
                if not actual['running']:row.update(health='CONTAINER_STOPPED',fresh_ready_pairs=None)
        return dict(scope='READ_ONLY_CRYPTO_MONITOR_V1',checked_at=now.isoformat(),engines=rows,
            independent_wallets=True,orders_sent=0,provider_requests=0,
            note='Separate simulated wallets. Closed P/L excludes open-position unrealized P/L. A recent checkpoint alone does not verify a process.')


def prepare_launchers(repo,output,base=None):
    repo,output=Path(repo).resolve(),Path(output).resolve();output.mkdir(parents=True,exist_ok=False)
    quote=lambda value:"'"+str(value).replace("'","''")+"'"
    python=repo/'.venv/Scripts/python.exe';base=Path(base or default_base()).resolve()
    for name,module,extra in [('Open-Crypto-Monitor.ps1','app.gui.crypto_monitor',''),
                              ('Show-Crypto-Monitor.ps1','app.gui.crypto_monitor',' --check')]:
        text="$ErrorActionPreference='Stop'\nPush-Location "+quote(repo)+"\ntry {\n    & "+quote(python)+' -m '+module+' --base '+quote(base)+extra+"\n    if ($LASTEXITCODE -ne 0) { throw 'Crypto monitor failed. Trading bots were not changed.' }\n} finally { Pop-Location }\n"
        (output/name).write_text(text,encoding='utf-8')
    print('MANUAL_MONITOR='+str(output/'Open-Crypto-Monitor.ps1'))
    print('READ_ONLY_STATUS='+str(output/'Show-Crypto-Monitor.ps1'))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepare-launchers',type=Path,required=True);parser.add_argument('--repo',type=Path,required=True)
    args=parser.parse_args();prepare_launchers(args.repo,args.prepare_launchers)


if __name__=='__main__':main()
