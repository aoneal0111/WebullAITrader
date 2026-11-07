from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
import json

import pytest

from app.asset_modules import grok_news_scout as m
from app.asset_modules.grok import GrokUnavailable

NOW = datetime(2026, 11, 2, 12, tzinfo=UTC)
SYMBOLS = ('BTC/USD', 'ETH/USD')
URL = 'https://example.org/official-announcement'


def collected(*args):
    return {'status': 'CITED_DIGEST', 'digest': 'Unverified social discussion, no executable quote.',
            'citations': [URL], 'usage': {'input_tokens': 4, 'output_tokens': 5}}


class Client:
    def __init__(self, answer=None):
        self.calls = []
        self.answer = answer

    def request(self, role, instruction, context, schema):
        self.calls.append((role, context))
        return self.answer if self.answer is not None else {'watchlist': [
            {'symbol': 'BTC/USD', 'source_urls': [URL], 'reason': 'Research only'}], 'reason': 'Watch'}


def test_news_to_scout_citations_and_durable_accounting(tmp_path):
    c = Client()
    r = m.cycle(tmp_path, c, SYMBOLS, collector=collected, clock=lambda: NOW)
    assert r['status'] == 'WATCHLIST_ONLY' and not r['execution_authorized'] and r['orders'] == 0
    assert c.calls[0][0] == 'SCOUT' and c.calls[0][1]['mode'] == 'PAPER'
    assert len(r['news_sha256']) == 64 and r['news_request_id']
    assert m.cached_watchlist(tmp_path, NOW)
    assert not m.cached_watchlist(tmp_path, NOW + timedelta(minutes=15))
    with m.connect(tmp_path) as db:
        assert db.execute('SELECT count(*) FROM attempts').fetchone()[0] == 2
    # Restart cannot reset the budget; old watchlist is invalidated first.
    with pytest.raises(GrokUnavailable, match='CAP'):
        m.cycle(tmp_path, c, SYMBOLS, collector=collected, clock=lambda: NOW, cap=2)
    assert json.loads((tmp_path / 'scout-latest.json').read_text())['watchlist'] == []


def test_concurrent_budget_reservations_are_atomic(tmp_path):
    # Initialize schema first; eight competing connections share only two slots.
    m.connect(tmp_path).close()
    def attempt(_):
        try:
            return m.reserve(tmp_path, 'NEWS', NOW.timestamp(), 2)
        except GrokUnavailable:
            return None
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(attempt, range(8)))
    assert sum(v is not None for v in results) == 2


def test_news_failure_does_not_call_scout_or_retain_old_watchlist(tmp_path):
    c = Client()
    def fail(*args):
        raise RuntimeError('secret provider body must not leak')
    r = m.cycle(tmp_path, c, SYMBOLS, collector=fail, clock=lambda: NOW)
    assert r['status'] == 'UNAVAILABLE' and not c.calls
    assert 'secret' not in (tmp_path / 'news-latest.json').read_text()
    assert not json.loads((tmp_path / 'scout-latest.json').read_text())['watchlist']


@pytest.mark.parametrize('answer', [
    {'watchlist': [{'symbol': 'DOGE/USD', 'source_urls': [URL], 'reason': 'unknown'}], 'reason': ''},
    {'watchlist': [{'symbol': 'BTC/USD', 'source_urls': ['https://invented.org'], 'reason': ''}], 'reason': ''},
    {'watchlist': [], 'reason': '', 'order': 'BUY'},
    {'watchlist': [{'symbol': 'BTC/USD', 'source_urls': [], 'reason': ''}], 'reason': ''},
])
def test_unknown_symbols_invented_citations_and_orders_rejected(tmp_path, answer):
    r = m.cycle(tmp_path, Client(answer), SYMBOLS, collector=collected, clock=lambda: NOW)
    assert r['status'] == 'UNAVAILABLE' and not r['watchlist']


def test_expired_news_never_reaches_scout(tmp_path):
    news = {**collected(), 'observed_at': NOW.isoformat(),
            'expires_at': (NOW + timedelta(minutes=15)).isoformat(), 'execution_authorized': False}
    with pytest.raises(ValueError, match='FRESH'):
        m.scout(Client(), news, SYMBOLS, NOW + timedelta(minutes=15))
    assert not m.valid_news({**news, 'observed_at': (NOW + timedelta(seconds=1)).isoformat()}, NOW)


def test_real_search_payload_tools_and_usage_are_retained():
    calls = []
    def transport(req, timeout):
        calls.append(json.loads(req.data))
        assert timeout == 30
        return json.dumps({'status': 'completed', 'usage': {'server_side_tool_usage_details':
            {'x_posts_fetched': 12}}, 'output': [{'type': 'message', 'role': 'assistant',
            'content': [{'type': 'output_text', 'text': 'Cited claim', 'annotations': [
                {'type': 'url_citation', 'url': URL}]}]}]}).encode()
    r = m.search_news('fake-key', 'grok-4.7', SYMBOLS, NOW, transport=transport)
    assert r['status'] == 'CITED_DIGEST' and r['citations'] == [URL]
    assert r['usage']['server_side_tool_usage_details']['x_posts_fetched'] == 12
    assert {t['type'] for t in calls[0]['tools']} == {'web_search', 'x_search'}
    assert calls[0]['max_turns'] == 2 and not calls[0]['store']
    assert calls[0]['reasoning'] == {'effort': 'low'}
    assert 'fake-key' not in m.encode(r)


def test_uncited_answer_is_not_news():
    transport = lambda *a, **k: json.dumps({'status': 'completed', 'output': []}).encode()
    assert m.search_news('fake', 'grok-4.7', SYMBOLS, NOW, transport=transport)['status'] == 'NO_VALID_CITED_DIGEST'
