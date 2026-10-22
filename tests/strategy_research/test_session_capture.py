"""Calendar, bounded-request, coverage and privacy checks with fake history only."""
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from app.strategy_research.session_capture import (
    SessionDataError, capture_sessions, session_bars, session_windows,
)

AT=datetime(2026,10,22,18,tzinfo=UTC)


def minute_rows(opened,closed):
    return [dict(time=(opened+timedelta(minutes=i)).isoformat(),open='100',high='101',
                 low='99',close='100.5',volume='1000',private_field='SECRET_VALUE')
            for i in range(int((closed-opened).total_seconds()//60))]


class HistoryOnly:
    def __init__(self):self.calls=[]
    def get_history_bar(self,*args,**kw):
        self.calls.append((args,kw))
        opened=datetime.fromtimestamp(kw['start_time']/1000,UTC)
        closed=datetime.fromtimestamp((kw['end_time']+1)/1000,UTC)
        return minute_rows(opened,closed)


class SessionCaptureTests(unittest.TestCase):
    def test_calendar_excludes_today_and_preserves_early_close_and_dst(self):
        windows=session_windows(AT,count=12)
        self.assertEqual(len(windows),12)
        self.assertEqual(windows[-1][0].date().isoformat(),'2026-10-21')
        winter=session_windows(datetime(2025,12,2,tzinfo=UTC),count=20)
        early=next(w for w in winter if w[0].date().isoformat()=='2025-11-28')
        self.assertEqual(int((early[1]-early[0]).total_seconds()/60),210)
        self.assertEqual(early[1].hour,18)
        self.assertEqual(winter[0][0].hour,13)
        self.assertEqual(winter[-1][0].hour,14)
        for value in (5,21,True):
            with self.assertRaises(SessionDataError):session_windows(AT,count=value)
        with self.assertRaises(SessionDataError):session_windows(AT,end_date=AT.date())

    def test_complete_session_epoch_formats_identical_duplicates_and_extras(self):
        opened,closed=session_windows(AT,count=6)[0];rows=minute_rows(opened,closed)
        rows[0]['time']=int(opened.timestamp())
        rows[1]['time']=str(int((opened+timedelta(minutes=1)).timestamp()*1000))
        rows+=[dict(rows[2]),{**rows[2],'time':closed.isoformat()}]
        bars,coverage=session_bars('SPY',rows,opened,closed)
        self.assertEqual(len(bars),390)
        self.assertEqual(coverage['identical_duplicates'],1)
        self.assertEqual(coverage['outside_session_rows'],1)
        self.assertEqual(coverage['missing_minutes'],0)

    def test_missing_conflicting_invalid_and_wrong_symbol_never_become_complete(self):
        opened,closed=session_windows(AT,count=6)[0];rows=minute_rows(opened,closed)
        bars,coverage=session_bars('SPY',rows[1:],opened,closed)
        self.assertFalse(bars);self.assertEqual(coverage['missing_minutes'],1)
        cases=([*rows,{**rows[0],'close':'100.6'}],
               [{**rows[0],'symbol':'OTHER'},*rows[1:]],
               [{**rows[0],'time':opened.replace(tzinfo=None).isoformat()},*rows[1:]],
               [{**rows[0],'time':(opened+timedelta(seconds=1)).isoformat()},*rows[1:]],
               [{**rows[0],'high':'90'},*rows[1:]])
        for values in cases:
            with self.subTest(values=values[0]),self.assertRaises(ValueError):
                session_bars('SPY',values,opened,closed)

    def test_archive_uses_history_only_exact_windows_hashes_and_allowlisted_csv(self):
        provider=HistoryOnly()
        with tempfile.TemporaryDirectory() as root,patch('app.strategy_research.session_capture.SYMBOLS',(('SPY','US_ETF'),)):
            directory=Path(root)/'out'
            manifest,archive=capture_sessions(directory,provider,tuple,captured_at=AT,count=6,pause=lambda _:None)
            self.assertEqual(manifest['status'],'COMPLETE')
            self.assertFalse(manifest['execution_authorized']);self.assertEqual(manifest['orders'],0)
            self.assertEqual(len(provider.calls),6)
            self.assertEqual(manifest['datasets'][0]['complete_sessions'],6)
            self.assertEqual(provider.calls[0][0],('SPY','US_ETF','M1'))
            self.assertFalse(provider.calls[0][1]['real_time_required'])
            self.assertEqual(provider.calls[0][1]['count'],'390')
            with ZipFile(archive) as z:
                self.assertEqual(set(z.namelist()),{'manifest.json','SPY-minute.csv'})
                self.assertNotIn(b'SECRET_VALUE',z.read('SPY-minute.csv'))
                self.assertEqual(sha256(z.read('SPY-minute.csv')).hexdigest(),manifest['datasets'][0]['csv_sha256'])
            with self.assertRaises(FileExistsError):
                capture_sessions(directory,provider,tuple,captured_at=AT,count=6,pause=lambda _:None)
            self.assertEqual(len(provider.calls),6)

    def test_missing_sessions_are_reported_and_not_stitched_or_filled(self):
        class Missing(HistoryOnly):
            def get_history_bar(self,*a,**kw):return super().get_history_bar(*a,**kw)[1:]
        with tempfile.TemporaryDirectory() as root,patch('app.strategy_research.session_capture.SYMBOLS',(('SPY','US_ETF'),)):
            manifest,archive=capture_sessions(Path(root)/'out',Missing(),tuple,captured_at=AT,count=6,pause=lambda _:None)
            self.assertEqual(manifest['status'],'INCOMPLETE')
            self.assertFalse(manifest['datasets'])
            self.assertTrue(all(r['missing_minutes']==1 for r in manifest['sessions']))
            with ZipFile(archive) as z:self.assertEqual(z.namelist(),['manifest.json'])

    def test_provider_errors_stop_after_three_and_do_not_export_request_secrets(self):
        class Failing:
            calls=0
            def get_history_bar(self,*a,**kw):
                self.calls+=1
                raise RuntimeError('SECRET_SIGNED_REQUEST')
        provider=Failing()
        with tempfile.TemporaryDirectory() as root:
            manifest,archive=capture_sessions(Path(root)/'out',provider,tuple,captured_at=AT,count=6,pause=lambda _:None)
            self.assertEqual(provider.calls,3)
            self.assertTrue(manifest['aborted_after_consecutive_errors'])
            self.assertEqual(manifest['status'],'INCOMPLETE')
            with ZipFile(archive) as z:
                self.assertNotIn(b'SECRET_SIGNED_REQUEST',z.read('manifest.json'))

    def test_unexpected_local_files_are_excluded(self):
        with tempfile.TemporaryDirectory() as root,patch('app.strategy_research.session_capture.SYMBOLS',(('SPY','US_ETF'),)):
            directory=Path(root)/'out'
            class Extra(HistoryOnly):
                def get_history_bar(self,*a,**kw):
                    (directory/'.env').write_text('SECRET_VALUE')
                    return super().get_history_bar(*a,**kw)
            _,archive=capture_sessions(directory,Extra(),tuple,captured_at=AT,count=6,pause=lambda _:None)
            with ZipFile(archive) as z:self.assertNotIn('.env',z.namelist())


if __name__=='__main__':unittest.main()
