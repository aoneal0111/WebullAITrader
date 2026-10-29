from contextlib import redirect_stdout
from datetime import timedelta
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.strategy_research.selected_sip_capture import capture, CaptureError
from tests.strategy_research import test_episode_gap_audit as gap_fixtures
from tests.strategy_research.test_episode_price_study import T


def payload(times, token=None):
    return json.dumps(dict(bars={'ABC':[dict(t=t.isoformat(), o=10, h=11, l=9, c=10, v=1000) for t in times]}, next_page_token=token)).encode()


class SipCaptureTests(unittest.TestCase):
    def setup_paths(self, temp):
        root=Path(temp)
        study, _ = gap_fixtures.GapAuditTests().fixture(root)
        return root, study

    def test_exact_feed_dates_pagination_and_original_preserved(self):
        with tempfile.TemporaryDirectory() as temp:
            root, study = self.setup_paths(temp)
            originals={p:p.read_bytes() for p in study.iterdir()}
            calls=[]
            def fetch(params, credentials):
                calls.append(dict(params))
                self.assertEqual(params['feed'], 'sip')
                self.assertEqual(params['adjustment'], 'raw')
                self.assertEqual(params['asof'], '-')
                self.assertEqual(params['start'], '2026-06-01T08:00:00+00:00')
                return payload([T], 'next') if len(calls)==1 else payload([T+timedelta(minutes=1)])
            with redirect_stdout(io.StringIO()):
                result=capture(study, root/'out', ('local','private'), fetch=fetch, pause=lambda _:None)
            self.assertEqual(result['status'], 'COMPLETE')
            self.assertEqual(result['partitions'][0]['rows'], 2)
            self.assertEqual(result['market_data_attempts'], 2)
            self.assertEqual(calls[1]['page_token'], 'next')
            self.assertEqual(originals, {p:p.read_bytes() for p in originals})
            self.assertNotIn('private', (root/'out'/'manifest.json').read_text())

    def test_http_failure_persists_safe_partial_and_never_falls_back(self):
        with tempfile.TemporaryDirectory() as temp:
            root, study=self.setup_paths(temp)
            calls=[]
            def fetch(params, credentials):
                calls.append(params['feed'])
                raise CaptureError('HTTP_403')
            with redirect_stdout(io.StringIO()):
                result=capture(study, root/'out', ('local','private'), fetch=fetch)
            self.assertEqual(calls, ['sip'])
            self.assertEqual(result['status'], 'PARTIAL')
            self.assertEqual(result['error'], 'HTTP_403')
            self.assertTrue((root/'out'/'sip-capture-summary.json').exists())

    def test_missing_credentials_make_no_requests(self):
        with tempfile.TemporaryDirectory() as temp:
            root, study=self.setup_paths(temp)
            with self.assertRaisesRegex(CaptureError, 'CREDENTIALS_MISSING'):
                capture(study, root/'out', ('',''), fetch=lambda *_:self.fail('request'))
            self.assertFalse((root/'out').exists())

    def test_out_of_request_bar_does_not_become_normalized_input(self):
        with tempfile.TemporaryDirectory() as temp:
            root, study=self.setup_paths(temp)
            with redirect_stdout(io.StringIO()):
                result=capture(study, root/'out', ('local','private'), fetch=lambda *_:payload([T+timedelta(days=1)]))
            self.assertEqual(result['error'], 'BAR_OUTSIDE_REQUEST_OR_UNALIGNED')
            self.assertFalse(list((root/'out'/'normalized').iterdir()))

    def test_empty_response_is_reported_without_inventing_bars(self):
        with tempfile.TemporaryDirectory() as temp:
            root, study=self.setup_paths(temp)
            with redirect_stdout(io.StringIO()):
                result=capture(study, root/'out', ('local','private'), fetch=lambda *_:payload([]))
            self.assertEqual(result['status'], 'COMPLETE_WITH_EMPTY_PARTITIONS')
            self.assertEqual(result['partitions'][0]['rows'], 0)

    def test_repeated_pagination_is_bounded(self):
        with tempfile.TemporaryDirectory() as temp:
            root, study=self.setup_paths(temp)
            with redirect_stdout(io.StringIO()):
                result=capture(study, root/'out', ('local','private'), fetch=lambda *_:payload([T], 'repeat'), pause=lambda _:None)
            self.assertEqual(result['error'], 'INVALID_OR_REPEATED_PAGE_TOKEN')
            self.assertEqual(result['market_data_attempts'], 2)
