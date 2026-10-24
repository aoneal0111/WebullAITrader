from datetime import date
import json
from unittest.mock import patch

import pytest

from app.equity_overhaul import history as h
from app.equity_overhaul import validation as v


def page(rows=None, token=None):
    return json.dumps({'bars': rows or {}, 'next_page_token': token}).encode()


def run(tmp_path, pages):
    it = iter(pages)
    return h.capture(tmp_path/'capture', v.frozen_plan(), ('LOCAL_KEY', 'LOCAL_SECRET'),
                     fetch=lambda *args: next(it), pause=lambda _: None, progress=lambda *a, **kw: None)


def test_no_credentials_or_changed_plan_cannot_create_output_or_make_request(tmp_path):
    with pytest.raises(h.CaptureError, match='CREDENTIALS_REQUIRED'):
        h.capture(tmp_path/'capture', v.frozen_plan(), ('', ''))
    assert not (tmp_path/'capture').exists()
    with pytest.raises(h.CaptureError, match='MISMATCH'):
        h.capture(tmp_path/'capture', {}, ('key', 'secret'))


def test_pagination_does_not_stop_on_empty_or_short_page_and_is_market_data_only(tmp_path):
    params = []; pages = iter([page(token='next'), page()])
    def fetch(p, credentials):
        params.append(p); return next(pages)
    result = h.capture(tmp_path/'capture', v.frozen_plan(), ('LOCAL_KEY', 'LOCAL_SECRET'),
                       fetch=fetch, pause=lambda _: None, progress=lambda *a, **kw: None)
    assert len(result['responses']) == 2 and len(params) == 2
    assert params[1]['page_token'] == 'next'
    assert all(p['feed'] == 'sip' and p['adjustment'] == 'raw' and p['timeframe'] == '1Min' for p in params)
    assert result['status'] == 'INCOMPLETE_SESSION_COVERAGE'
    assert h.ENDPOINT == 'https://data.alpaca.markets/v2/stocks/bars'
    saved = (tmp_path/'capture'/'manifest.json').read_text()
    assert 'LOCAL_KEY' not in saved and 'LOCAL_SECRET' not in saved
    assert not result['execution_authorized']


@pytest.mark.parametrize('pages, error', [
    ([page(token='same'), page(token='same')], 'REPEATED_PAGE_TOKEN'),
    ([b'x'*(h.MAX_PAGE_BYTES+1)], 'RESPONSE_BOUND_EXCEEDED'),
    ([b'invalid'], 'MALFORMED_PROVIDER_DATA'),
    ([page({'UNEXPECTED': []})], 'UNEXPECTED_SYMBOLS'),
])
def test_failures_preserve_bounded_safe_manifest_without_normalized_success(tmp_path, pages, error):
    result = run(tmp_path, pages)
    assert result['status'] == 'PARTIAL' and error in result['error']
    assert not result['normalized_inputs']
    assert (tmp_path/'capture'/'manifest.json').is_file()


def test_page_limit_and_provider_403_do_not_retry_or_fall_back(tmp_path):
    with patch.object(h, 'MAX_PAGES', 1):
        result = run(tmp_path, [page(token='more')])
    assert result['error'] == 'PAGE_LIMIT_EXCEEDED'
    calls = []
    def denied(params, credentials):
        calls.append(params); raise h.CaptureError('HTTP_403')
    result = h.capture(tmp_path/'denied', v.frozen_plan(), ('key', 'secret'), fetch=denied)
    assert len(calls) == 1 and result['error'] == 'HTTP_403' and result['status'] == 'PARTIAL'


def test_complete_session_roundtrip_and_incomplete_other_symbols_reported(tmp_path):
    opened, closed = v.schedule(date(2026, 7, 1), date(2026, 7, 2))[date(2026, 7, 1)]
    from datetime import timedelta
    rows = [{'t': (opened+timedelta(minutes=i)).isoformat(), 'o': 100, 'h': 101,
             'l': 99, 'c': 100, 'v': 1000} for i in range(390)]
    result = run(tmp_path, [page({'SPY': rows})])
    assert result['status'] == 'INCOMPLETE_SESSION_COVERAGE'
    loaded, _ = v.load_dataset(tmp_path/'capture')
    assert len(loaded['SPY']) == 390 and loaded['SPY'][-1].closed == closed
    assert len(result['normalized_inputs']) == 1


def test_timestamp_and_conflicting_duplicate_are_not_silently_fixed(tmp_path):
    row = {'t': '2026-07-01T13:30:00Z', 'o': 100, 'h': 101, 'l': 99, 'c': 100, 'v': 1000}
    result = run(tmp_path, [page({'SPY': [row, {**row, 'v': 1001}]})])
    assert result['error'] == 'CONFLICTING_DUPLICATE'


def test_http_errors_never_export_provider_body_message_or_credentials():
    import io
    import urllib.error
    from unittest.mock import MagicMock
    opener = MagicMock()
    opener.open.side_effect = urllib.error.HTTPError('https://example.invalid', 403,
        'PRIVATE_PROVIDER_MESSAGE', {}, io.BytesIO(b'PRIVATE_BODY'))
    with patch.object(h.urllib.request, 'build_opener', return_value=opener):
        with pytest.raises(h.CaptureError) as caught:
            h.request_page({'feed': 'sip'}, ('PRIVATE_KEY', 'PRIVATE_SECRET'))
    assert str(caught.value) == 'HTTP_403'
    assert h.NoRedirect().redirect_request(None, None, None, None, None, None) is None


def test_http_read_is_bounded_and_only_uses_fixed_data_host():
    from unittest.mock import MagicMock
    opener = MagicMock(); response = opener.open.return_value.__enter__.return_value
    response.read.return_value = page()
    with patch.object(h.urllib.request, 'build_opener', return_value=opener):
        assert h.request_page({'feed': 'sip'}, ('key', 'secret')) == page()
    response.read.assert_called_once_with(h.MAX_PAGE_BYTES+1)
    request = opener.open.call_args.args[0]
    assert request.full_url.startswith(h.ENDPOINT+'?') and request.get_method() == 'GET'


def test_request_uses_exchange_dates_so_valid_utc_prior_evening_cannot_abort_capture(tmp_path):
    from datetime import datetime
    # The frozen warmup begins Saturday Nov 23, 2024. UTC midnight includes
    # Friday Nov 22 after-hours bars (19:00-20:00 Eastern), which the old
    # exchange-date validator rejected even when they matched its HTTP query.
    candidates = [
        {'t': '2024-11-23T00:30:00Z', 'o': 100, 'h': 101, 'l': 99, 'c': 100, 'v': 10},
        {'t': '2024-11-25T14:30:00Z', 'o': 100, 'h': 101, 'l': 99, 'c': 100, 'v': 10},
    ]
    seen = []
    def provider(params, credentials):
        seen.append(params)
        lower = datetime.fromisoformat(params['start'].replace('Z', '+00:00'))
        upper = datetime.fromisoformat(params['end'].replace('Z', '+00:00'))
        return page({'SPY': [r for r in candidates if lower <=
                    datetime.fromisoformat(r['t'].replace('Z', '+00:00')) <= upper]})
    result = h.capture(tmp_path/'capture', v.frozen_plan(), ('key', 'secret'),
                       fetch=provider, progress=lambda *a, **kw: None)
    assert result['error'] is None
    assert result['status'] == 'INCOMPLETE_SESSION_COVERAGE'
    assert datetime.fromisoformat(seen[0]['start']).isoformat() == '2024-11-23T05:00:00+00:00'
    assert datetime.fromisoformat(seen[0]['end']).isoformat() == '2026-09-25T03:59:59+00:00'


@pytest.mark.parametrize('stamp', ['2024-11-23T04:59:00Z', '2026-09-25T04:00:00Z'])
def test_true_request_boundary_violations_still_fail_closed_with_safe_diagnostics(tmp_path, stamp):
    row = {'t': stamp, 'o': 100, 'h': 101, 'l': 99, 'c': 100, 'v': 1000}
    result = run(tmp_path, [page({'SPY': [row]})])
    assert result['error'] == 'BAR_OUTSIDE_REQUEST' and result['status'] == 'PARTIAL'
    assert result['normalized_inputs'] == []
    rejection = result['timestamp_rejection']
    assert rejection['observed_at'] == stamp.replace('Z', '+00:00')
    assert rejection['request_start_inclusive'] == '2024-11-23T05:00:00+00:00'
    assert rejection['request_end_exclusive'] == '2026-09-25T04:00:00+00:00'
    assert 'LOCAL_SECRET' not in json.dumps(result)


def test_non_session_rows_inside_request_are_excluded_without_time_shifts(tmp_path):
    # Includes exact start, early-morning UTC prior-calendar-date bars on the
    # last permitted Eastern evening, and the final included aligned minute.
    stamps = ['2024-11-23T05:00:00Z', '2026-09-25T00:30:00Z', '2026-09-25T03:59:00Z']
    rows = [{'t': stamp, 'o': 100, 'h': 101, 'l': 99, 'c': 100, 'v': 1000} for stamp in stamps]
    result = run(tmp_path, [page({'SPY': rows})])
    assert result['error'] is None and result['status'] == 'INCOMPLETE_SESSION_COVERAGE'
    assert result['normalized_inputs'] == []
    assert all(c['status'] == 'EXCLUDED_INCOMPLETE' for c in result['coverage'])
