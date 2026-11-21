"""Read-only native offer timing evidence. Never starts a runtime or requests data."""
import argparse
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
import json
import os
from pathlib import Path
import sqlite3
from time import monotonic


def instant(value):
    result=datetime.fromisoformat(str(value).replace('Z','+00:00'))
    if result.utcoffset() is None:raise ValueError('AWARE_TIME_REQUIRED')
    return result.astimezone(UTC)


def document(path):
    path=Path(path)
    if not path.is_file() or path.is_symlink() or path.stat().st_size>16*1024**2:
        raise ValueError('BOUNDED_REGULAR_JSON_REQUIRED')
    value=json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(value,dict):raise ValueError('JSON_OBJECT_REQUIRED')
    return value


@contextmanager
def readonly(path):
    path=Path(path).resolve(strict=True)
    db=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=.25)
    began=monotonic()
    db.set_progress_handler(lambda:int(monotonic()-began>3),1000)
    try:
        db.execute('PRAGMA query_only=ON');db.execute('BEGIN')
        yield db
    finally:db.close()


def native_gates(path,start,end):
    """Pair exact lifecycle/symbol records from the same serialized batch.

    Native gate timestamp is the signal's time; authorization transition carries
    evaluation time. Neither records the wall clock at the producer timing check.
    """
    result=[]
    with readonly(path) as db:
        rows=db.execute("SELECT sequence,symbol,timestamp,payload_json FROM capture_records "
            "WHERE record_type='EXECUTION_GATE_DECISION' AND timestamp>=? AND timestamp<=? "
            "AND payload_json LIKE '%AI_REVIEW_NOT_SENT%' ORDER BY sequence DESC LIMIT 65",
            ((start-timedelta(seconds=60)).isoformat(),end.isoformat())).fetchall()
        for seq,symbol,stamp,raw in rows[:64]:
            gate=json.loads(raw)
            if gate.get('final_reason')!='AI_REVIEW_NOT_SENT':continue
            life=gate.get('lifecycle')
            matches=[]
            for other_seq,other_stamp,other_raw in db.execute(
                "SELECT sequence,timestamp,payload_json FROM capture_records "
                "WHERE symbol=? AND record_type='STATE_TRANSITION' AND sequence>=? "
                "AND sequence<? ORDER BY sequence DESC LIMIT 16",(symbol,max(0,seq-16),seq)):
                other=json.loads(other_raw)
                if (other.get('lifecycle_id')==life and other.get('to')=='AUTHORIZATION_EVALUATED'
                        and other.get('authorization_reason')=='AI_REVIEW_NOT_SENT'):
                    matches.append((other_seq,other_stamp,other))
            row=dict(sequence=seq,symbol=symbol,lifecycle_id=life,signal_timestamp=stamp,
                classification='EVALUATION_PAIR_UNAVAILABLE',producer_check_time_recorded=False)
            if len(matches)==1:
                other_seq,other_stamp,other=matches[0]
                evaluated=instant(other.get('decision_timestamp') or other_stamp)
                if not start<=evaluated<=end:continue
                signal=instant(stamp);age=(evaluated-signal).total_seconds()
                row.update(evaluation_sequence=other_seq,evaluated_at=evaluated.isoformat(),
                    signal_age_at_evaluation_seconds=age,
                    seconds_left_at_evaluation=60-age,
                    classification=('SIGNAL_FUTURE_AT_EVALUATION' if age<0 else
                        'SIGNAL_EXPIRED_BY_EVALUATION' if age>=60 else
                        'PRODUCER_CLOCK_WINDOW_REQUIRES_ACTUAL_CHECK_TIME'),
                    provider_times={k:other.get(k) for k in ('provider_last_timestamp',
                        'provider_bid_timestamp','provider_ask_timestamp')})
            result.append(row)
    return dict(rows=result,limit=64,truncated=len(rows)>64,
        identity_scope='Exact symbol/lifecycle and adjacent serialized capture sequence; not runtime PID attestation')



def paper_entry_gates(path, start, end):
    """Bounded native/PAPER decisions, irrespective of whether Grok is enabled.

    Treat this as observed gate evidence, not proof of a broker submission.
    The historic native_gates() report intentionally remains available for
    previously recorded AI-review timing investigations.
    """
    counts = {}
    rows = []
    with readonly(path) as db:
        for raw, count in db.execute(
            "SELECT json_extract(payload_json,'$.final_reason'), COUNT(*) "
            "FROM capture_records WHERE record_type='EXECUTION_GATE_DECISION' "
            "AND timestamp>=? AND timestamp<=? GROUP BY 1 ORDER BY COUNT(*) DESC LIMIT 32",
            (start.isoformat(), end.isoformat()),
        ):
            key = str(raw) if raw is not None else 'UNSPECIFIED'
            counts[key] = count
        for sequence, symbol, timestamp, raw in db.execute(
            "SELECT sequence,symbol,timestamp,payload_json FROM capture_records "
            "WHERE record_type='EXECUTION_GATE_DECISION' AND timestamp>=? AND timestamp<=? "
            "ORDER BY sequence DESC LIMIT 64",
            (start.isoformat(), end.isoformat()),
        ):
            payload = json.loads(raw)
            rows.append(dict(
                sequence=sequence, symbol=symbol, at=timestamp,
                result=payload.get('result'), reason=payload.get('final_reason'),
                setup=payload.get('setup'),
                order_constructed=payload.get('order_constructed'),
                submission_attempted=payload.get('submission_attempted'),
                placement_decision=payload.get('placement_decision'),
                failed_gates=[str(g.get('gate')) for g in payload.get('gates', ())
                              if isinstance(g, dict) and g.get('passed') is False][:12],
            ))
    return dict(reason_counts=counts, recent=rows, limit=64,
                scope='OBSERVED_CAPTURE_DECISIONS_NOT_BROKER_ORDER_HISTORY')


def native_calls(path,start,end):
    result=[]
    with readonly(path) as db:
        for started,raw in db.execute("SELECT started,result FROM calls WHERE engine=? "
                "AND stage='NATIVE_REVIEW' AND started>=? AND started<=? ORDER BY started DESC LIMIT 32",
                ('WARRIOR_MOMENTUM_V1',start.isoformat(),end.isoformat())):
            answer=json.loads(raw) if raw else {}
            content=answer.get('answer') or {}
            result.append(dict(started_at=started,status=answer.get('status','UNFINALIZED'),
                latency_ms=answer.get('latency_ms'),selected_ids=content.get('selected_ids'),
                reason=content.get('reason'),failure_category=answer.get('failure_category')))
    return result


def collect(paper,capture,diagnostics,*,now):
    paper=Path(paper);pointer=document(paper/'recording-session.json')
    spec=document(paper/'experiment.json');session=Path(pointer['session'])
    manifest=document(session/'research-manifest.json')
    if (pointer.get('trial_id')!=spec.get('trial_id') or manifest.get('paper_experiment')!=spec
            or manifest.get('live_trading_enabled') is not False):
        raise ValueError('EXACT_PAPER_RECORDING_REQUIRED')
    start=instant(manifest['started_at']);end=now
    final=session/'research-summary.json'
    if final.is_file():
        summary=document(final)
        if (summary.get('journal') or {}).get('closed'):
            end=min(end,instant(summary['journal']['updated_at']))
    report=dict(scope='READ_ONLY_WARRIOR_NATIVE_TIMING_V1',checked_at=now.isoformat(),
        recording_session=str(session),recording_started_at=start.isoformat(),window_end=end.isoformat(),
        capture_path=str(capture),orders=0,provider_requests=0,grok_requests=0,parameters_changed=False)
    for key,reader,path in (('native_gate_evidence',native_gates,capture),
                           ('paper_entry_gate_evidence',paper_entry_gates,capture),
                           ('native_model_calls',native_calls,paper/'grok-team-budget.sqlite3')):
        try:report[key]=reader(path,start,end)
        except (OSError,ValueError,TypeError,sqlite3.Error) as exc:
            report[key]=dict(read_error=type(exc).__name__)
    candidates=[]
    files=sorted(Path(diagnostics).glob('*.json'),key=lambda p:p.stat().st_mtime,reverse=True)[:60]
    for path in files:
        try:
            data=document(path);at=instant(data['captured_at'])
            if path.stem!=data.get('run_id') or not start<=at<=now or not 0<=(now-at).total_seconds()<=120:continue
            if not data.get('entry_conversion') or (data.get('metrics') or {}).get('market_events_received',0)<1000:continue
            entry=data['entry_conversion'];metrics=data.get('metrics') or {}
            candidates.append(dict(run_id=data['run_id'],captured_at=at.isoformat(),
                process_started_at=data.get('process_started_at'),
                entry_counts={k:entry.get(k) for k in ('counters','execution_gate_counters','funnel_counts','quote_age_summary')},
                component_timings={k:{f:v.get(f) for f in ('calls','failures','max_ms','latest_ms','total_ms')}
                    for k,v in (metrics.get('component_timings') or {}).items() if 'warrior' in k.lower()},
                stream=data.get('stream')))
            if len(candidates)>=3:break
        except (OSError,ValueError,TypeError,KeyError):continue
    report['runtime_candidates']=candidates
    report['limitations']=['Capture pairs do not contain the actual producer check clock; unresolved timing causes stay unclassified',
        'Runtime candidates are time-matched, not selected solely by newest filename',
        'Bounded read transactions include WAL; no source database or checkpoint is modified',
        'No deadline extension, retimestamping, model call, order or runtime activation']
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo',type=Path,required=True);parser.add_argument('--paper',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    from dotenv import dotenv_values
    from app.configuration.loader import load_configuration
    repo=args.repo.resolve();os.chdir(repo)
    # Resolve only the configured local capture path. Never export environment values.
    config=load_configuration({**dotenv_values(repo/'.env'),**os.environ})
    report=collect(args.paper,config.warrior_forward_capture_path,
        Path(os.environ.get('TEMP','/tmp'))/'atlas-performance',now=datetime.now(UTC))
    with args.output.open('x',encoding='utf-8') as stream:json.dump(report,stream,indent=2)
    print('UPLOAD_FILE='+str(args.output))


if __name__=='__main__':main()
