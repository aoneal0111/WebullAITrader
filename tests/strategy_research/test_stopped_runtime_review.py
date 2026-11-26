import json
from pathlib import Path
import zipfile

import pytest
from app.strategy_research.stopped_runtime_review import export


def test_stopped_export_keeps_earlier_consumer_failure_and_matches_process(tmp_path):
    trial=tmp_path/'trial';trial.mkdir();session=tmp_path/'session';session.mkdir()
    (session/'journal').mkdir();perf=tmp_path/'perf';perf.mkdir()
    def write(path,value):path.write_text(json.dumps(value))
    write(trial/'recording-session.json',dict(session=str(session)))
    write(session/'research-manifest.json',dict(started_at='2026-11-06T12:44:10+00:00'))
    with pytest.raises(ValueError,match='CLOSE_ATLAS'):export(trial,perf,tmp_path/'not-created')
    assert not (tmp_path/'not-created').exists()
    write(session/'research-summary.json',dict(reason='ATLAS_CLOSED'))
    (session/'journal'/'events-00001.jsonl').write_text('{"synthetic":"event"}\n')
    write(perf/'failed.json',dict(process_started_at='2026-11-06T12:44:09+00:00',
        shutdown_origin='GUI_CLOSE',shutdown=dict(events=[dict(event_type='UNEXPECTED_CONSUMER_TERMINATION',exception_class='AuthoritativeLaneFailure'),dict(event_type='RUNTIME_STOPPED',failure_present=False)])))
    write(perf/'unrelated.json',dict(process_started_at='2026-11-07T12:44:09+00:00'))
    archive=export(trial,perf,tmp_path/'out')
    with zipfile.ZipFile(archive) as z:
        report=json.loads(z.read('review.json'))
        assert report['performance_files']==['failed.json']
        assert report['failure_events'][0]['exception_class']=='AuthoritativeLaneFailure'
        assert not report['original_exception_recovered']
        assert report['full_journal_included']
        assert 'journal/events-00001.jsonl' in z.namelist()
