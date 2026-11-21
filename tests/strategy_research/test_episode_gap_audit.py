from contextlib import closing, redirect_stdout
from datetime import timedelta
from hashlib import sha256
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from app.strategy_research.episode_gap_audit import coverage, run, session
from app.strategy_research.episode_price_study import BarCache, SOURCE_SHA
from app.strategy_research.equity_corpus_review import REPORT
from tests.strategy_research.test_episode_price_study import bars, file_bars, T, DAY


class GapAuditTests(unittest.TestCase):
    def fixture(self, root):
        normalized = (root / REPORT).parent.parent / 'normalized'
        normalized.mkdir(parents=True)
        values = bars(4)
        path = normalized / f'ABC_{DAY}.jsonl'
        file_bars(path, (values[0], values[2], values[3]))
        study = root / 'study'
        study.mkdir()
        summary = dict(evidence_status='PRICE_REPLAY_WITH_ENTRIES', corpus=dict(source_sha256=SOURCE_SHA), price_partitions_read=1)
        (study / 'episode-price-study-summary.json').write_text(json.dumps(summary))
        cache = BarCache(normalized)
        cache.get('ABC', DAY)
        (study / 'private-price-inputs.json').write_text(json.dumps({f'ABC_{DAY}': cache.proofs[('ABC', DAY)]}))
        with closing(sqlite3.connect(study / 'private-episode-index.sqlite3')) as db:
            db.execute('CREATE TABLE selected (symbol TEXT, day TEXT)')
            db.execute('CREATE TABLE events (symbol TEXT, day TEXT, entry_time TEXT)')
            db.execute('INSERT INTO selected VALUES (?,?)', ('ABC', DAY))
            for at in (T + timedelta(minutes=1), T + timedelta(minutes=2), T + timedelta(minutes=4)):
                db.execute('INSERT INTO events VALUES (?,?,?)', ('ABC', DAY, at.isoformat()))
            db.commit()
        return study, path

    def test_gap_counts_do_not_invent_boundary_minutes(self):
        values = bars(5)
        counts, examples = coverage((values[0], values[2], values[4]))
        self.assertEqual(counts['missing_minutes_between_observed_bars'], 2)
        self.assertEqual(counts['missing_CORE'], 2)
        self.assertEqual(len(examples), 2)
        self.assertEqual(coverage(values)[0]['gap_intervals'], 0)

    def test_session_boundaries_are_eastern(self):
        self.assertEqual(session(T.replace(hour=13, minute=29)), 'PREMARKET')
        self.assertEqual(session(T.replace(hour=13, minute=30)), 'CORE')
        self.assertEqual(session(T.replace(hour=20)), 'AFTER_HOURS')

    def test_end_to_end_preserves_index_and_prices(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            study, path = self.fixture(root)
            originals = {p: sha256(p.read_bytes()).hexdigest() for p in [path, *study.iterdir()]}
            with redirect_stdout(io.StringIO()):
                report = run(root, study, root / 'output')
            self.assertEqual(report['entry_minutes']['exact_entry_missing'], 2)
            self.assertEqual(report['entry_minutes']['entry_missing_inside_observed_span'], 1)
            self.assertEqual(report['entry_minutes']['entry_missing_outside_observed_span'], 1)
            self.assertEqual(report['entry_minutes']['previous_completed_minute_missing'], 1)
            self.assertEqual(report['coverage']['missing_minutes_between_observed_bars'], 1)
            self.assertEqual(report['network_requests'], 0)
            self.assertEqual(originals, {p: sha256(p.read_bytes()).hexdigest() for p in originals})

    def test_changed_prices_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            study, path = self.fixture(root)
            file_bars(path, bars(5))
            with self.assertRaisesRegex(ValueError, 'PRICE_CONTENT_DIFFERS'):
                run(root, study, root / 'output')
            self.assertFalse((root / 'output').exists())

    def test_failed_prefix_study_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            study, _ = self.fixture(root)
            path = study / 'episode-price-study-summary.json'
            summary = json.loads(path.read_text())
            summary['evidence_status'] = 'NO_VALIDATED_SIGNAL_PREFIXES'
            path.write_text(json.dumps(summary))
            with self.assertRaisesRegex(ValueError, 'COMPLETED_CORRECTED_STUDY_REQUIRED'):
                run(root, study, root / 'output')
