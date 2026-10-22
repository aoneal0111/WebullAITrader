"""Synthetic data plumbing checks; no provider credentials or performance claims."""
from datetime import UTC, datetime
from decimal import Decimal as D
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from app.market.calendar import NYSE
from app.strategy_research.capture import capture, daily_bars
from app.strategy_research.replay import Costs

AT = datetime(2026, 10, 22, 15, tzinfo=UTC)
SPLIT = datetime(2025, 10, 22, tzinfo=UTC)


def rows():
    dates = NYSE.valid_days(start_date='2022-01-01', end_date='2026-10-22')[-1200:]
    return tuple(dict(time=d.isoformat(), open='100', high='101', low='99', close='100', volume='1000000') for d in dates)


class HistoryOnly:
    def __init__(self, values):
        self.values = values
        self.calls = []

    def get_history_bar(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.values


class CaptureTests(unittest.TestCase):
    def test_completed_daily_labels_use_actual_sessions_and_exclude_today(self):
        bars, excluded = daily_bars('SPY', rows(), captured_at=AT)
        self.assertEqual(excluded, 1)
        self.assertEqual(bars[-1].opened.date().isoformat(), '2026-10-21')
        early = next(b for b in bars if b.opened.date().isoformat() == '2025-11-28')
        self.assertEqual(early.closed.hour, 18)  # actual 13:00 Eastern close
        self.assertEqual(early.session_end, early.closed)

    def test_missing_day_conflicts_naive_future_and_wrong_symbol_rejected(self):
        source = rows()
        cases = (source[:100] + source[101:],
                 (*source[:-1], {**source[100], 'close': '100.5'}),
                 ({**source[0], 'time': '2022-03-01'}, *source[1:]),
                 ({**source[0], 'time': '2027-01-01T00:00:00+00:00'}, *source[1:]),
                 ({**source[0], 'symbol': 'OTHER'}, *source[1:]))
        for value in cases:
            with self.subTest(case=value[0]['time']), self.assertRaises(ValueError):
                daily_bars('SPY', value, captured_at=AT)

    def test_market_data_only_archive_is_reproducible_and_never_authorizes(self):
        provider = HistoryOnly(rows())
        with tempfile.TemporaryDirectory() as root, patch('app.strategy_research.capture.SYMBOLS', ('SPY',)):
            output = Path(root) / 'research'
            report, archive = capture(output, provider, tuple, costs=Costs(D(10), D('0.005')),
                split_at=SPLIT, captured_at=AT, pause=lambda _: None)
            self.assertEqual(report['status'], 'COMPLETE')
            self.assertFalse(report['execution_authorized'])
            self.assertEqual(report['orders'], 0)
            self.assertEqual(provider.calls, [(('SPY', 'US_ETF', 'D'), {'count': '1200', 'real_time_required': False})])
            with ZipFile(archive) as zipped:
                self.assertEqual(set(zipped.namelist()), {'SPY-daily.csv', 'SPY-screening.json', 'summary.json', 'manifest.json'})
                comparison = json.loads(zipped.read('SPY-screening.json'))
                self.assertFalse(comparison['execution_authorized'])
                self.assertEqual(len(comparison['results']), 3)
                # Flat synthetic input cannot manufacture a performance claim.
                self.assertTrue(all(x['validation']['win_rate'] is None for x in comparison['results']))
            before = len(provider.calls)
            with self.assertRaises(FileExistsError):
                capture(output, provider, tuple, costs=Costs(D(10), D(0)), split_at=SPLIT, captured_at=AT)
            self.assertEqual(len(provider.calls), before)

    def test_provider_exception_cannot_leak_signed_request_to_archive(self):
        class Failing:
            def get_history_bar(self, *args, **kwargs):
                raise RuntimeError('SECRET_SIGNED_REQUEST')
        with tempfile.TemporaryDirectory() as root, patch('app.strategy_research.capture.SYMBOLS', ('SPY',)):
            report, archive = capture(Path(root) / 'research', Failing(), tuple,
                costs=Costs(D(10), D(0)), split_at=SPLIT, captured_at=AT)
            self.assertEqual(report['status'], 'INCOMPLETE')
            self.assertEqual(report['errors'], [{'symbol': 'SPY', 'error_type': 'RuntimeError'}])
            with ZipFile(archive) as zipped:
                self.assertTrue(all(b'SECRET_SIGNED_REQUEST' not in zipped.read(n) for n in zipped.namelist()))


    def test_unexpected_local_files_are_not_included_in_bundle(self):
        with tempfile.TemporaryDirectory() as root, patch('app.strategy_research.capture.SYMBOLS', ('SPY',)):
            output = Path(root) / 'research'
            class ExtraFile(HistoryOnly):
                def get_history_bar(self, *args, **kwargs):
                    (output / '.env').write_text('PRIVATE_LOCAL_VALUE')
                    return super().get_history_bar(*args, **kwargs)
            _, archive = capture(output, ExtraFile(rows()), tuple, costs=Costs(D(10), D(0)),
                                 split_at=SPLIT, captured_at=AT)
            with ZipFile(archive) as zipped:
                self.assertNotIn('.env', zipped.namelist())
                self.assertTrue(all(b'PRIVATE_LOCAL_VALUE' not in zipped.read(n) for n in zipped.namelist()))

    def test_existing_archive_is_preserved_before_any_request(self):
        with tempfile.TemporaryDirectory() as root:
            output = Path(root) / 'research'
            archive = output.with_suffix('.zip')
            archive.write_bytes(b'original')
            provider = HistoryOnly(rows())
            with self.assertRaises(FileExistsError):
                capture(output, provider, tuple, costs=Costs(D(10), D(0)), split_at=SPLIT, captured_at=AT)
            self.assertFalse(provider.calls)
            self.assertEqual(archive.read_bytes(), b'original')


if __name__ == '__main__':
    unittest.main()
