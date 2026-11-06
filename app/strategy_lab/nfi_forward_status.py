"""Read-only native NFI ledger summary; never create a missing trade database."""
import json
from pathlib import Path
import sqlite3
from datetime import UTC,datetime


def ledger_status(work):
    path=Path(work)/'nfi-paper.sqlite3'
    if not path.is_file():return dict(ledger_present=False,closed_trades=0,open_positions=0)
    db=sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=2)
    try:
        db.execute('PRAGMA query_only=ON')
        columns={r[1] for r in db.execute('PRAGMA table_info(trades)')}
        if not {'is_open','close_profit_abs','pair','amount','open_rate','id'}<=columns:
            raise ValueError('NATIVE_TRADE_SCHEMA_REQUIRED')
        counts=db.execute('SELECT COUNT(*),COALESCE(SUM(close_profit_abs>0),0),'
            'COALESCE(SUM(close_profit_abs<0),0),COALESCE(SUM(close_profit_abs),0) '
            'FROM trades WHERE is_open=0').fetchone()
        held=[dict(id=r[0],pair=r[1],amount=r[2],open_rate=r[3]) for r in db.execute(
            'SELECT id,pair,amount,open_rate FROM trades WHERE is_open=1 ORDER BY id LIMIT 100')]
        open_count=db.execute('SELECT COUNT(*) FROM trades WHERE is_open=1').fetchone()[0]
        partial=(db.execute('SELECT COALESCE(SUM(realized_profit),0) FROM trades WHERE is_open=1').fetchone()[0]
                 if 'realized_profit' in columns else None)
        return dict(open_lifecycle_realized_usd=partial,ledger_present=True,closed_trades=counts[0],wins=counts[1],losses=counts[2],
            closed_win_rate=counts[1]/counts[0] if counts[0] else None,
            closed_lifecycle_net_usd=counts[3],open_positions=open_count,holdings=held,
            open_positions_counted_as_wins=False)
    finally:db.close()


def main():
    import argparse
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--work',type=Path,required=True)
    p.add_argument('--export',type=Path)
    a=p.parse_args();work=a.work
    if a.export:
        print('UPLOAD_REVIEW='+str(export_review(work,a.export)));return
    report=dict(scope='READ_ONLY_NFI_PAPER_SUMMARY',checked_at=datetime.now(UTC).isoformat(),
                live_trading_enabled=False,**ledger_status(work))
    for name in ('engine-status.json','market-preflight.json','runtime-check.json','capture-final.json'):
        path=work/name
        if path.is_file():report[name]=json.loads(path.read_text())
    print(json.dumps(report,indent=2,allow_nan=False))



def export_review(work,output):
    """Consistent private DB snapshot plus bounded logs; raw journals stay local."""
    from shutil import copyfile
    import zipfile
    work,output=Path(work),Path(output)
    from app.strategy_lab.nfi_forward_paper import selected_config
    selected_config(work) # Refuse unsealed configs or any exchange credentials.
    output.mkdir(parents=True,exist_ok=False)
    ledger=work/'nfi-paper.sqlite3'
    if not ledger.is_file():raise ValueError('NO_NATIVE_LEDGER_TO_EXPORT')
    source=sqlite3.connect(ledger.resolve().as_uri()+'?mode=ro',uri=True,timeout=2)
    target=sqlite3.connect(output/ledger.name)
    try:source.backup(target)
    finally:target.close();source.close()
    result=ledger_status(output)
    (output/'review-summary.json').write_text(json.dumps(result,indent=2,allow_nan=False))
    for name in ('paper-manifest.json','config.json','resolved-config.json','resolved-universe.json',
                 'engine-status.json','market-preflight.json','runtime-check.json','capture-final.json',
                 'recording-session.json'):
        if (work/name).is_file():copyfile(work/name,output/name)
    log=work/'native-paper.log'
    if log.is_file():
        with log.open('rb') as stream:
            stream.seek(max(0,log.stat().st_size-16*1024**2))
            (output/'native-paper-log-tail.txt').write_bytes(stream.read())
    (output/'capture-note.txt').write_text('Raw append-only recording-* journals remain in the original paper directory. This review contains a consistent SQLite backup and the latest status, not a full journal-chain audit.\n')
    archive=output.with_suffix('.zip')
    with zipfile.ZipFile(archive,'x',compression=zipfile.ZIP_DEFLATED) as z:
        for path in output.iterdir():z.write(path,path.name)
    return archive


if __name__=='__main__':main()
