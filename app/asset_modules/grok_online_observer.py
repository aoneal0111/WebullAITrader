"""Separate, explicitly invoked fresh-context Grok observer process.

Reads the existing native capture only. No Atlas, broker, order service,
management dispatcher, account ledger or configuration writer is constructed.
"""
from collections import Counter
from dataclasses import asdict
from datetime import UTC, datetime
import json
from pathlib import Path
from threading import Event
from time import monotonic

from .grok import GrokClient
from .grok_observer import GrokObserverTrial
from .grok_observer_capture import MAX_BYTES,decode_capture


def run(capture_path, baseline, output, client, *, clock=lambda:datetime.now(UTC),
        timer=monotonic, stop=None, wait=None):
    if not isinstance(client,GrokClient):raise TypeError('Shared bounded GrokClient required')
    output=Path(output);output.mkdir(parents=True,exist_ok=False)
    stop=stop or Event();wait=wait or stop.wait
    trial=GrokObserverTrial(client,baseline,clock=clock)
    started=timer();active=None;seen=set();counts=Counter();owners=Counter()
    reason='STOPPED';before=client.requests;previous_complete=b''
    initial_usage=client.usage()
    path=Path(capture_path)
    # New output directory makes this invocation reviewable and never appends
    # to a prior trial. Current freshness still prevents replay of old records.
    try:
        with (output/'private-observations.jsonl').open('x',encoding='utf-8') as log:
            while not stop.is_set():
                now=timer()
                if active is None and now-started>=900:reason='STARTUP_TIMEOUT';break
                if active is not None and now-active>=300:reason='TIME_LIMIT';break
                if client.requests-before>=20:reason='REQUEST_LIMIT';break
                if len(seen)>=100:reason='RECORD_LIMIT';break
                if not path.exists():
                    wait(.25);continue
                with path.open('rb') as source:raw=source.read(MAX_BYTES+1)
                if len(raw)>MAX_BYTES:reason='INPUT_BOUND_EXCEEDED';break
                # A writer can be in the middle of its final line. Never parse
                # that partial record or count it as corrupt evidence.
                end=raw.rfind(b'\n')
                if end<0:
                    wait(.25);continue
                complete=raw[:end+1]
                if not complete.startswith(previous_complete):reason='CAPTURE_REVISED';break
                previous_complete=complete
                try:records=decode_capture(complete,baseline)
                except Exception:reason='INVALID_CAPTURE';break
                if any(s.source!='WEBULL_OPENAPI' for s,c in records):
                    reason='NON_NATIVE_CAPTURE';break
                for snapshot,context in records:
                    identity=(snapshot.engine,snapshot.event_id)
                    if identity in seen:continue
                    if stop.is_set():reason='STOPPED';break
                    if client.requests-before>=20:reason='REQUEST_LIMIT';break
                    if active is not None and timer()-active>=300:reason='TIME_LIMIT';break
                    seen.add(identity)
                    if active is None and snapshot.fresh(clock()):active=timer()
                    observation=trial.observe(snapshot,strategy_context=context)
                    counts[observation.status]+=1;owners[snapshot.engine.value]+=1
                    log.write(json.dumps(asdict(observation),sort_keys=True)+'\n');log.flush()
                wait(.25)
    except KeyboardInterrupt:
        reason='INTERRUPTED'
    except Exception:
        # No SDK exceptions, credentials, raw context or response bodies escape.
        reason='LOCAL_OBSERVER_FAILED'
    usage=client.usage()
    report=dict(scope='SEPARATE_ONLINE_GROK_OBSERVER_V1',stop_reason=reason,
        orders=0,execution_authorized=False,baseline_modified=False,
        inference_attempts=client.requests-before,records_by_engine=dict(owners),statuses=dict(counts),
        usage={**{k:v-initial_usage.get(k,0) for k,v in usage.items()},
               'attempts_without_reported_usage':client.requests-before-(usage['responses_with_usage']-initial_usage['responses_with_usage'])},
        elapsed_seconds=round(timer()-started,3),
        startup_wait_seconds=round((timer() if active is None else active)-started,3),
        active_observation_seconds=0 if active is None else round(timer()-active,3),
        client_diagnostics=client.diagnostics(),baseline_sha256=baseline,
        online_requests_enabled=True,desktop_order_review_enabled=False,
        limitations=['Observer only; no entry, exit, account, capital or policy decisions dispatched',
                     'Missing token usage is not evidence of zero provider charges',
                     'Expired advice is recorded but never becomes actionable'])
    (output/'summary.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    return report


def main():
    import argparse,os
    from dotenv import dotenv_values
    from app.configuration.loader import load_configuration
    from .grok import configured_client
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo',type=Path,required=True)
    parser.add_argument('--capture',type=Path,required=True)
    identity=parser.add_mutually_exclusive_group(required=True)
    identity.add_argument('--baseline')
    identity.add_argument('--baseline-manifest',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--online',action='store_true',help='Allow up to twenty paid observer requests')
    args=parser.parse_args()
    if not args.online:parser.error('--online required; no requests sent')
    if args.output.exists():parser.error('output exists; use a new private directory')
    args.repo=args.repo.resolve();args.capture=args.capture.resolve();args.output=args.output.resolve()
    if args.baseline_manifest is not None:args.baseline_manifest=args.baseline_manifest.resolve()
    os.chdir(args.repo)
    try:
        env={**dotenv_values(args.repo/'.env'),**os.environ}
        c=load_configuration(env)
        if c.environment.value!='PAPER' or c.live_trading_enabled or c.grok_paper_entries_enabled:
            raise ValueError('Unsafe flags')
        baseline=args.baseline
        receipt=None
        if args.baseline_manifest is not None:
            from .grok_observer_session import verify_receipt,configuration_digest,_digest
            with args.baseline_manifest.open('rb') as stream:raw=stream.read(4097)
            if len(raw)>4096:raise ValueError('Manifest bound exceeded')
            manifest=json.loads(raw)
            required={'scope','baseline_sha256','source_sha256','settings_sha256'}
            if (not isinstance(manifest,dict) or set(manifest)!=required
                    or manifest['scope']!='NATIVE_OBSERVER_SESSION_MANIFEST_V1'):
                raise ValueError('Native observer manifest required')
            import re
            if any(not isinstance(manifest[k],str) or not re.fullmatch('[0-9a-f]{64}',manifest[k])
                   for k in ('baseline_sha256','source_sha256','settings_sha256')):
                raise ValueError('Exact manifest hashes required')
            directory=args.baseline_manifest.parent
            if args.capture.resolve()!=(directory/'private-capture/private-observer-context.jsonl').resolve():
                raise ValueError('Capture must belong to this manifest')
            original,receipt=verify_receipt(directory)
            if (original.resolve()!=args.repo.resolve() or configuration_digest(c)!=receipt['configuration_sha256']
                    or manifest['source_sha256']!=receipt['source_sha256']
                    or _digest(dict(source=manifest['source_sha256'],settings=manifest['settings_sha256']))!=manifest['baseline_sha256']):
                raise ValueError('Frozen source/configuration/identity mismatch')
            baseline=manifest['baseline_sha256']
        from functools import partial
        from .grok import _post
        from .grok_background_observer import run_background
        # Observer-only timeout; the default execution-review client stays at 15s.
        client=configured_client(env,transport=partial(_post,timeout=30))
        if client is None:raise ValueError('Missing credentials')
        report=run_background(args.capture,baseline,args.output,client)
        report['native_manifest_verified']=receipt is not None
        if receipt is not None:
            try:
                verify_receipt(args.baseline_manifest.parent)
                current=load_configuration({**dotenv_values(args.repo/'.env'),**os.environ})
                report['source_and_configuration_still_match']=configuration_digest(current)==receipt['configuration_sha256']
            except Exception:report['source_and_configuration_still_match']=False
            if not report['source_and_configuration_still_match']:
                report['stop_reason']='BASELINE_CHANGED'
        (args.output/'summary.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    except Exception:
        print('STATUS=LOCAL_OBSERVER_SETUP_FAILED');return 2
    print('OBSERVER_STATUS='+report['stop_reason'])
    print('UPLOAD_SUMMARY='+str((args.output/'summary.json').resolve()))
    return 0 if report['stop_reason'] in {'TIME_LIMIT','REQUEST_LIMIT','RECORD_LIMIT','STOPPED','INTERRUPTED'} else 2


if __name__=='__main__':raise SystemExit(main())
