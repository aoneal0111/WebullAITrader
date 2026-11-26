"""Publish native PAPER status without terminating position management on I/O contention."""
import json
from pathlib import Path
import time
from uuid import uuid4

STATUS_ERROR = 'STATUS_PUBLICATION_FAILED'
RETRY_DELAYS = (0.01, 0.025, 0.05, 0.1, 0.2)


def publish_status(path, status, evidence, *, replace=None, sleep=time.sleep):
    """Keep the last complete JSON on failure; gate new entries through Evidence.error.

    This function never submits orders, changes native strategy parameters, or
    retries native trade operations. Only status-file permission failures retry.
    """
    path=Path(path)
    replace=replace or (lambda source,target:source.replace(target))
    recovered=evidence.error==STATUS_ERROR
    prior_failures=getattr(evidence,'status_publication_failures',0)
    value=dict(status)
    value['capture_error']=None if recovered else evidence.error
    value['status_publication']=dict(state='OK',previous_failed_cycles=prior_failures)
    temporary=path.with_name(path.name+'.'+uuid4().hex+'.tmp')
    failure=None
    try:
        raw=json.dumps(value,indent=2,allow_nan=False)+'\n'
        temporary.write_text(raw,encoding='utf-8')
        for attempt in range(len(RETRY_DELAYS)+1):
            try:
                replace(temporary,path)
                evidence.status_publication_failures=0
                if evidence.error==STATUS_ERROR:evidence.error=None
                status.update(capture_error=evidence.error,status_publication=value['status_publication'])
                return True
            except PermissionError:
                if attempt==len(RETRY_DELAYS):raise
                sleep(RETRY_DELAYS[attempt])
    except OSError as exc:
        failure=exc
    finally:
        try:temporary.unlink(missing_ok=True)
        except OSError:pass
    evidence.status_publication_failures=prior_failures+1
    if evidence.error in (None,STATUS_ERROR):evidence.error=STATUS_ERROR
    status.update(capture_error=evidence.error,status_publication=dict(state='FAILED',
        consecutive_failed_cycles=prior_failures+1,error_type=type(failure).__name__,errno=failure.errno))
    if prior_failures==0 or (prior_failures+1)%12==0:
        print('NATIVE_STATUS_PUBLICATION_FAILED; FILE='+path.name+'; ERROR='+type(failure).__name__+
              '; ERRNO='+str(failure.errno)+'; NEW_ENTRIES_PAUSED; STATUS_IO_WILL_NOT_TERMINATE_WORKER',flush=True)
    return False
