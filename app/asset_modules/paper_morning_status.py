"""Compact read-only operator report; no provider, broker or GUI imports."""
import argparse
import json
from pathlib import Path
import sqlite3


def read(path):
    if not path.is_file():
        return {}
    if path.stat().st_size > 2 * 1024 ** 2:
        raise ValueError('STATUS_FILE_BOUND')
    return json.loads(path.read_text(encoding='utf-8-sig'))


def report(trial):
    trial = Path(trial).resolve()
    spec = read(trial / 'experiment.json')
    result = {'trial_started': bool(spec), 'expires_at': spec.get('expires_at'),
        'orders_enabled_by_this_check': False}
    pointer = read(trial / 'recording-session.json')
    if pointer:
        if pointer.get('trial_id') != spec.get('trial_id'):
            raise ValueError('RECORDING_TRIAL_ID_MISMATCH')
        session = Path(pointer['session'])
        manifest = read(session / 'research-manifest.json')
        if manifest.get('paper_experiment') != spec:
            raise ValueError('RECORDING_MANIFEST_TRIAL_MISMATCH')
        readiness = read(session / 'engine-readiness.json')
        journal = read(session / 'journal/journal-status.json')
        final = read(session / 'research-summary.json')
        result.update(recording_session=str(session), live_trading_enabled=manifest.get('live_trading_enabled'),
            warrior_running=readiness.get('warrior_service_running'),scalper_enabled=readiness.get('scalper_enabled'),
            engine_status_updated_at=readiness.get('updated_at'), recording_closed=journal.get('closed'),
            writer_alive=journal.get('writer_alive'),writer_failed=journal.get('writer_failed'),
            capacity_reached=journal.get('capacity_reached'),evidence_complete=journal.get('evidence_complete'),
            written_records=journal.get('counts',{}).get('written'),
            contexts_by_engine={k:v for k,v in readiness.get('tap_counts',{}).items() if k.startswith('assessments_queued_')},
            final_reason=final.get('reason'))
    db_path = trial / 'grok-team-budget.sqlite3'
    if db_path.is_file():
        db = sqlite3.connect(db_path.as_uri() + '?mode=ro', uri=True)
        try:
            rows = db.execute('SELECT engine,stage,count(*),sum(result IS NULL) FROM calls GROUP BY engine,stage').fetchall()
            result.update(grok_reserved_attempts=sum(r[2] for r in rows),
                grok_unfinalized_attempts=sum(r[3] for r in rows),
                grok_roles={r[0]+'|'+r[1]:r[2] for r in rows})
        finally:
            db.close()
    else:
        result['grok_reserved_attempts'] = 0
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--trial', required=True, type=Path)
    print(json.dumps(report(p.parse_args().trial), indent=2))


if __name__ == '__main__':
    main()
