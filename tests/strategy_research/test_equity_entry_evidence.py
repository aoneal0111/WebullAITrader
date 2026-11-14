import json
from hashlib import sha256
from pathlib import Path
import tempfile
import unittest
import zipfile
from app.strategy_research.equity_entry_evidence import export


def fixture(root, noise=0):
    session=root/'session';session.mkdir();journal=session/'journal';journal.mkdir()
    manifest=b'{"synthetic":true}';(session/'research-manifest.json').write_bytes(manifest)
    previous='0'*64;lines=[]
    events=[
        ('STREAM_ASSESSMENT',{'reason':'PROVIDER_DATA_STALE','decision':'REJECTED','snapshot':{
            'symbol':'ABC','decision_at':'2026-01-01T12:00:00+00:00','last_timestamp':'2026-01-01T12:00:00.1+00:00'}},'12:00:00'),
        ('MARKET_EVENT',{'symbol':'ABC'},'12:00:01'),
        ('MARKET_EVENT',{'symbol':'DEF'},'13:00:01'),
        ('ASSESSMENT',{'reason':'WAIT'},'13:00:02'),
        ('READINESS_CHECK_FAILED',{'reason':'synthetic'},'13:00:03')]
    events += [('MARKET_EVENT',{'symbol':'DEF','padding':'x'*2000},'12:00:01')]*noise
    events += [('MARKET_ELIGIBILITY',{'symbol':'ABC','padding':'x'*2000},'12:00:01')]*noise
    for sequence,(kind,payload,time) in enumerate(events,1):
        row=dict(sequence=sequence,schema='ATLAS_DAILY_EVIDENCE_V1',kind=kind,payload=payload,
            received_at='2026-01-01T'+time+'+00:00',baseline_sha256='a'*64,previous_sha256=previous)
        row['sha256']=sha256(json.dumps(row,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        previous=row['sha256'];lines.append(json.dumps(row,sort_keys=True,separators=(',',':')).encode()+b'\n')
    raw=b''.join(lines);path=journal/'events-00001.jsonl';path.write_bytes(raw+b'partial ignored tail')
    prefix=dict(scope='INTERRUPTED_SESSION_WRITTEN_PREFIX_REVIEW_V1',records=len(events),bytes=len(raw),
        chain_head=previous,baseline_sha256='a'*64,segments=[dict(name=path.name,bytes=len(raw),sha256=sha256(raw).hexdigest())])
    report=dict(scope='PRIVATE_INTERRUPTED_PAPER_SESSION_REVIEW_V1',recording_session=str(session),
        trial_id='a'*32,verified_prefix=prefix,
        files={'capture/research-manifest.json':{'sha256':sha256(manifest).hexdigest()}},
        native_trial={'outcomes':[{'instrument':'ABC','opened_at':'2026-01-01T12:00:00+00:00','closed_at':'2026-01-01T12:00:10+00:00'}]})
    review=root/'review.zip'
    with zipfile.ZipFile(review,'x') as z:
        z.writestr('paper-day-review.json',json.dumps(report));z.writestr('capture/research-manifest.json',manifest)
    return review,path


class EquityEvidenceTests(unittest.TestCase):
    def test_selection_verified_prefix_and_clock_classification_preserve_source(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);review,path=fixture(root);before=path.read_bytes()
            result=export(review,root/'output.zip')
            self.assertEqual(result['verified_records'],5)
            self.assertEqual(result['selected_records'],4)
            self.assertEqual(result['stale_component_classification'],{'last_FUTURE':1})
            self.assertEqual(path.read_bytes(),before)
            self.assertFalse(result['parameters_changed'])
            with zipfile.ZipFile(root/'output.zip') as z:
                rows=[json.loads(x) for x in z.read('selected-events.jsonl').splitlines()]
                self.assertEqual([x['sequence'] for x in rows],[1,2,4,5])

    def test_noisy_other_symbols_and_eligibility_do_not_consume_selected_bound(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);review,path=fixture(root,noise=100)
            before=path.read_bytes()
            with patch('app.strategy_research.equity_entry_evidence.MAX_SELECTED',4000), patch(
                    'app.strategy_research.equity_entry_evidence.MAX_RECORDS',4):
                result=export(review,root/'output.zip')
            self.assertEqual(result['verified_records'],205)
            self.assertEqual(result['selected_records'],4)
            self.assertEqual(result['excluded_kinds'],{'MARKET_EVENT':101,'MARKET_ELIGIBILITY':100})
            self.assertEqual(path.read_bytes(),before)
            self.assertEqual(result['trade_windows'][0]['symbol'],'ABC')

    def test_excluded_record_corruption_is_still_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);review,path=fixture(root,noise=1)
            path.write_bytes(path.read_bytes().replace(b'"symbol":"DEF"',b'"symbol":"XYZ"'))
            with self.assertRaisesRegex(ValueError,'PREFIX_CHAIN_CHANGED'):
                export(review,root/'output.zip')
            self.assertFalse((root/'output.zip').exists())

    def test_relevant_record_bound_still_refuses_without_silent_truncation(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);review,path=fixture(root)
            with patch('app.strategy_research.equity_entry_evidence.MAX_RECORDS',3):
                with self.assertRaisesRegex(ValueError,'SELECTED_EVIDENCE_BOUND'):
                    export(review,root/'output.zip')
            self.assertFalse((root/'output.zip').exists())
            self.assertFalse(list(root.glob('*.partial-*')))

    def test_corrupt_prefix_leaves_no_output_or_partial_archive(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);review,path=fixture(root)
            path.write_bytes(path.read_bytes().replace(b'PROVIDER_DATA_STALE',b'PROVIDER_DATA_WRONG'))
            with self.assertRaisesRegex(ValueError,'PREFIX_CHAIN_CHANGED'):export(review,root/'output.zip')
            self.assertFalse((root/'output.zip').exists());self.assertFalse(list(root.glob('*.partial-*')))

    def test_source_manifest_change_and_existing_output_are_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);review,path=fixture(root)
            output=root/'output.zip';output.write_bytes(b'keep')
            with self.assertRaisesRegex(ValueError,'NEW_OUTPUT'):export(review,output)
            self.assertEqual(output.read_bytes(),b'keep')
            (root/'session/research-manifest.json').write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError,'MANIFEST_CHANGED'):export(review,root/'different.zip')


if __name__=='__main__':unittest.main()
