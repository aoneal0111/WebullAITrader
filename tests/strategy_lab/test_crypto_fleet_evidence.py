"""Read-only live-prefix export without importing a trading runtime."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from app.asset_modules.daily_research_journal import canonical
from app.strategy_lab.crypto_fleet_evidence import export, archive_prefix, recording, KEYS


class SignalEvidenceTests(unittest.TestCase):
    def setUp(self):
        quiet=patch('builtins.print');quiet.start();self.addCleanup(quiet.stop)
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)/'fleet';self.root.mkdir()
        self.name='recording-20261107T043000Z-'+'a'*32
        self.works={key:self.root/key for key in KEYS}
        self.works['nfi']=Path(self.temp.name)/'original-nfi'
        for key,work in self.works.items():
            work.mkdir();config=work/('resolved-config.json' if key=='nfi' else 'config.json')
            config.write_text(json.dumps({'dry_run':True,'profile':key}))
            baseline=sha256(config.read_bytes()).hexdigest()
            rec=work/self.name;rec.mkdir()
            row=dict(schema='ATLAS_DAILY_EVIDENCE_V1',sequence=1,kind='FLEET_NATIVE_CANDLES',
                received_at='2026-11-07T04:30:00+00:00',received_monotonic=1.,baseline_sha256=baseline,
                previous_sha256='0'*64,payload={'pair':'BTC/USD','rows':[{'close':100.}]})
            row['sha256']=sha256(canonical(row)).hexdigest();raw=canonical(row)+b'\n'
            (rec/'events-00001.jsonl').write_bytes(raw)
            anchor=dict(counts={'written':1},bytes_written=len(raw),chain_head=row['sha256'],
                baseline_sha256=baseline,updated_at='2026-11-07T04:30:01+00:00',closed=False,
                evidence_complete=True,writer_failed=False,capacity_reached=False)
            (rec/'journal-status.json').write_text(json.dumps(anchor))
            (work/'recording-session.json').write_text(json.dumps({'session':'/work/'+self.name}))
            (work/'engine-status.json').write_text(json.dumps({'recording_id':self.name}))
            (work/'strategies').mkdir();(work/'strategies/Source.py').write_text('# retained source\n')
        (self.root/'fleet.json').write_text(json.dumps(dict(
            scope='FIVE_INDEPENDENT_CRYPTO_PAPER_EXPERIMENTS_V1',live_trading_enabled=False,
            id='b'*32,nfi_work=str(self.works['nfi']))))

    def test_exports_six_verified_prefixes_without_network_or_source_mutation(self):
        before={str(p):p.read_bytes() for p in Path(self.temp.name).rglob('*') if p.is_file()}
        with patch('socket.socket',side_effect=AssertionError('NO_NETWORK')):
            path=export(self.root)
        for name,raw in before.items():self.assertEqual(Path(name).read_bytes(),raw)
        with zipfile.ZipFile(path) as z:
            report=json.loads(z.read('evidence-manifest.json'))
            self.assertEqual(set(report['profiles']),set(KEYS));self.assertFalse(report['runtime_modified'])
            for key,p in report['profiles'].items():
                self.assertEqual(p['records'],1);self.assertFalse(p['source_recording_closed'])
                self.assertEqual(p['coverage'],'VERIFIED_WRITTEN_CHECKPOINT_PREFIX')
                self.assertIn(key+'/strategies/Source.py',z.namelist())
                self.assertIn(key+'/journal/events-00001.jsonl',z.namelist())

    def test_live_partial_append_after_checkpoint_is_excluded(self):
        rec,anchor,_=recording(self.works['e0']);path=rec/'events-00001.jsonl';old=path.read_bytes()
        with path.open('ab') as f:f.write(b'{"currently_writing":')
        output=export(self.root)
        with zipfile.ZipFile(output) as z:self.assertEqual(z.read('e0/journal/events-00001.jsonl'),old)
        self.assertTrue(path.read_bytes().endswith(b':'))

    def test_future_segment_is_excluded_and_missing_committed_segment_refused(self):
        rec,anchor,_=recording(self.works['e0']);(rec/'events-00003.jsonl').write_text('future')
        with zipfile.ZipFile(Path(self.temp.name)/'prefix.zip','x') as z:
            archive_prefix(z,'e0',rec,anchor)
        (rec/'events-00001.jsonl').rename(rec/'events-00002.jsonl')
        with self.assertRaisesRegex(ValueError,'SEGMENT_SEQUENCE'):export(self.root)

    def test_tamper_refuses_success_archive_and_cleans_partial_output(self):
        rec,_,_=recording(self.works['e0']);p=rec/'events-00001.jsonl'
        p.write_bytes(p.read_bytes().replace(b'100.0',b'101.0'))
        target=self.root/'bad.zip'
        with self.assertRaisesRegex(ValueError,'JOURNAL_CHAIN_CHANGED'):export(self.root,target)
        self.assertFalse(target.exists());self.assertFalse(list(self.root.glob('*.partial-*')))

    def test_checkpoint_count_bytes_and_head_must_all_match(self):
        rec,anchor,_=recording(self.works['e0'])
        for field,value in [('bytes_written',anchor['bytes_written']+1),('chain_head','f'*64),('counts',{'written':2})]:
            changed=deepcopy(anchor);changed[field]=value
            with self.subTest(field=field),zipfile.ZipFile(Path(self.temp.name)/(field+'.zip'),'x') as z:
                with self.assertRaisesRegex(ValueError,'CHECKPOINT_PREFIX_MISMATCH'):
                    archive_prefix(z,'e0',rec,changed)

    def test_changed_config_refuses_binding(self):
        (self.works['e0']/'config.json').write_text('{}')
        with self.assertRaisesRegex(ValueError,'RECORDED_CONFIG_CHANGED'):export(self.root)

    def test_pointer_traversal_and_recording_transition_refused(self):
        work=self.works['e0'];pointer=work/'recording-session.json';old=pointer.read_bytes()
        pointer.write_text(json.dumps({'session':'../not-a-recording'}))
        with self.assertRaisesRegex(ValueError,'POINTER_REQUIRED'):recording(work)
        pointer.write_bytes(old);(work/'engine-status.json').write_text(json.dumps({'recording_id':'new-recording'}))
        with self.assertRaisesRegex(ValueError,'TRANSITION_RETRY'):recording(work)

    def test_incomplete_source_is_labeled_instead_of_misrepresented(self):
        rec,anchor,_=recording(self.works['bb']);anchor['evidence_complete']=False;anchor['capacity_reached']=True
        (rec/'journal-status.json').write_text(json.dumps(anchor))
        with zipfile.ZipFile(export(self.root)) as z:
            profile=json.loads(z.read('evidence-manifest.json'))['profiles']['bb']
            self.assertFalse(profile['source_evidence_complete']);self.assertTrue(profile['source_capacity_reached'])

    def make_review(self, fleet_id=None):
        path=Path(self.temp.name)/'prior-review.zip'
        fleet=json.loads((self.root/'fleet.json').read_text())
        if fleet_id:fleet['id']=fleet_id
        with zipfile.ZipFile(path,'x') as z:
            z.writestr('fleet.json',json.dumps(fleet))
            for key,work in self.works.items():
                anchor=json.loads((work/self.name/'journal-status.json').read_text())
                z.writestr(key+'/engine-status.json',json.dumps({'recording_id':self.name,'capture':anchor}))
        return path

    def test_review_pins_overnight_recording_even_after_a_restart(self):
        review=self.make_review()
        for work in self.works.values():
            (work/'recording-session.json').write_text(json.dumps({'session':'new-recording'}))
            (work/'engine-status.json').write_text(json.dumps({'recording_id':'new-recording'}))
        with zipfile.ZipFile(export(self.root,review=review)) as z:
            report=json.loads(z.read('evidence-manifest.json'))
            self.assertEqual(report['anchor_origin'],'REVIEW_CHECKPOINTS')
            self.assertEqual(report['review_sha256'],sha256(review.read_bytes()).hexdigest())
            for p in report['profiles'].values():self.assertEqual(p['recording_id'],self.name)
            self.assertNotIn('e0/recording-session.json',z.namelist())
            self.assertEqual(json.loads(z.read('e0/engine-status.json'))['recording_id'],self.name)

    def test_review_checkpoint_excludes_later_recording_appends(self):
        review=self.make_review();rec=self.works['e0']/self.name
        original=(rec/'events-00001.jsonl').read_bytes()
        with (rec/'events-00001.jsonl').open('ab') as f:f.write(b'future uncommitted bytes')
        with zipfile.ZipFile(export(self.root,review=review)) as z:
            self.assertEqual(z.read('e0/journal/events-00001.jsonl'),original)

    def test_review_from_another_fleet_is_refused(self):
        review=self.make_review(fleet_id='c'*32)
        with self.assertRaisesRegex(ValueError,'REVIEW_FLEET_MISMATCH'):export(self.root,review=review)

    def test_existing_output_is_not_overwritten(self):
        target=self.root/'existing.zip';target.write_bytes(b'keep')
        with self.assertRaisesRegex(ValueError,'ALREADY_EXISTS'):export(self.root,target)
        self.assertEqual(target.read_bytes(),b'keep')


if __name__=='__main__':unittest.main()
