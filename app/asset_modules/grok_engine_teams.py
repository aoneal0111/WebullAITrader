"""Engine-separated Grok collaboration research; no execution interfaces.

Durable 60-attempt cap: 20 Momentum, 20 Scalper, 20 NEWS/SCOUT calls.
The news desk never blocks a native or observer engine. All proposals remain
unfilled opinions, including replies arriving inside an engine's timing budget.
"""
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from threading import Event, Lock, Thread
from time import monotonic
from uuid import uuid4

from .grok import configured_client, GrokUnavailable
from .grok_failure import failure_diagnostic
from .grok_day_proposals import review
from .grok_news_scout import search_news, scout, valid_news
from .grok_research_transport import compact_transport

ENGINES = ('WARRIOR_MOMENTUM_V1', 'QUICK_SCALPER')
BUDGET_SECONDS = {'WARRIOR_MOMENTUM_V1': 10, 'QUICK_SCALPER': 2}


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


class TeamBudget:
    def __init__(self, root, *, clock=lambda: datetime.now(UTC)):
        self.root, self.clock = Path(root), clock
        self.path = self.root / 'grok-team-budget.sqlite3'
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS samples (engine TEXT, slot INTEGER, event TEXT, PRIMARY KEY(engine,slot))')
            db.execute('CREATE TABLE IF NOT EXISTS calls '
                '(id TEXT PRIMARY KEY, engine TEXT, stage TEXT, started TEXT, context TEXT, result TEXT, event_key TEXT UNIQUE)')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=2)
        try:
            with db:
                yield db
        finally:
            db.close()

    def sample_slot(self, engine):
        from app.equity_overhaul.paper_experiment import load
        _, spec = load(self.root / 'experiment.json', clock=self.clock)
        elapsed = (self.clock() - datetime.fromisoformat(spec['created_at'])).total_seconds()
        return int((elapsed + (300 if engine == 'NEWS_DESK' else 0)) // 3600)

    def claim_sample(self, engine, event):
        if engine not in (*ENGINES, 'NEWS_DESK'):
            raise ValueError('EXPLICIT_ENGINE_TEAM_REQUIRED')
        slot = self.sample_slot(engine)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT 1 FROM samples WHERE engine=? AND slot=?',(engine,slot)).fetchone():
                return False
            db.execute('INSERT INTO samples VALUES (?,?,?)',(engine,slot,event))
        return True

    def next_desk_wait(self):
        from app.equity_overhaul.paper_experiment import load
        _, spec = load(self.root / 'experiment.json', clock=self.clock)
        elapsed = (self.clock() - datetime.fromisoformat(spec['created_at'])).total_seconds()
        next_boundary = (self.sample_slot('NEWS_DESK') + 1) * 3600 - 300
        return max(1, min(60, next_boundary - elapsed))

    def reserve(self, engine, stage, context, *, maximum_stage=None):
        from app.equity_overhaul.paper_experiment import load
        load(self.root / 'experiment.json', clock=self.clock)
        if (self.root / 'STOP_GROK').exists():
            raise GrokUnavailable('TEAM_OBSERVER_STOPPED')
        if not ((engine in ENGINES and stage in ('ANALYST', 'COORDINATOR'))
                or (engine == 'WARRIOR_MOMENTUM_V1' and stage in ('DIRECTOR','NATIVE_REVIEW'))
                or (engine == 'NEWS_DESK' and stage in ('NEWS', 'SCOUT'))):
            raise ValueError('EXPLICIT_ENGINE_TEAM_ROLE_REQUIRED')
        if maximum_stage is not None and (type(maximum_stage) is not int or not 1<=maximum_stage<=20):
            raise ValueError("BOUNDED_STAGE_CAP_REQUIRED")
        raw = encode(context)
        if len(raw.encode()) > 65536:
            raise ValueError('TEAM_CONTEXT_BOUND')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            total = db.execute('SELECT count(*) FROM calls').fetchone()[0]
            desk = db.execute("SELECT count(*) FROM calls WHERE engine='NEWS_DESK'").fetchone()[0]
            own = db.execute('SELECT count(*) FROM calls WHERE engine=?',(engine,)).fetchone()[0]
            stage_count=db.execute('SELECT count(*) FROM calls WHERE engine=? AND stage=?',(engine,stage)).fetchone()[0]
            if maximum_stage is not None and stage_count>=maximum_stage:
                raise GrokUnavailable('DURABLE_STAGE_REQUEST_CAP')
            if own >= 20 or total >= 60:
                raise GrokUnavailable('DURABLE_TEAM_REQUEST_CAP')
            identifier = uuid4().hex
            event_key = engine + ':' + stage + ':' + str(context['event_id']) if 'event_id' in context else identifier
            if db.execute('SELECT 1 FROM calls WHERE event_key=?', (event_key,)).fetchone():
                raise GrokUnavailable('DUPLICATE_TEAM_EVENT')
            db.execute('INSERT INTO calls VALUES (?,?,?,?,?,NULL,?)',
                (identifier, engine, stage, self.clock().isoformat(), raw, event_key))
        return identifier

    def finish(self, identifier, result):
        with self.connect() as db:
            db.execute('UPDATE calls SET result=? WHERE id=?', (encode(result), identifier))

    def report(self):
        with self.connect() as db:
            rows = db.execute('SELECT engine,stage,count(*),sum(result IS NULL) FROM calls GROUP BY engine,stage').fetchall()
        return {'reserved_attempts': sum(r[2] for r in rows), 'maximum_attempts': 60, 'maximum_per_team': 20,
            'unfinalized_attempts': sum(r[3] for r in rows),
            'roles': [{'engine': r[0], 'stage': r[1], 'attempts': r[2]} for r in rows]}


class EngineTeams:
    def __init__(self, environment, trial, *, clock=lambda: datetime.now(UTC), factory=configured_client):
        self.clock, self.environment = clock, dict(environment)
        self.budget = TeamBudget(trial, clock=clock)
        # A MASTER cooldown or failed recommendation in one team cannot revoke
        # the other team. Clients share only the durable attempt ledger.
        self.clients = {engine: factory(environment, transport=compact_transport) for engine in (*ENGINES, 'NEWS_DESK')}
        if any(c is None for c in self.clients.values()):
            raise ValueError('LOCAL_GROK_CONFIGURATION_REQUIRED')
        self._lock, self._stop = Lock(), Event()
        self._symbols, self._news = set(), None
        self._thread = None

    @property
    def requests(self):
        return self.budget.report()['reserved_attempts']

    def usage(self):
        return {'by_engine': {k: c.usage() for k, c in self.clients.items()},
            'news_tool_usage': 'See private NEWS records in shared request ledger'}

    def diagnostics(self):
        return {'by_engine': {k: c.diagnostics() for k, c in self.clients.items()},
            'budget': self.budget.report(), 'news_worker_running': bool(self._thread and self._thread.is_alive())}

    def request(self, engine, stage, role, instruction, context, schema):
        identifier = self.budget.reserve(engine, stage, context)
        began = monotonic()
        result = {'status': 'UNAVAILABLE', 'execution_authorized': False, 'orders': 0}
        try:
            answer = self.clients[engine].request(role, instruction, context, schema)
            result.update(status='RESPONSE_RECEIVED', answer=answer)
            return answer
        finally:
            result['latency_ms'] = round((monotonic() - began) * 1000)
            self.budget.finish(identifier, result)

    def offer_symbol(self, symbol):
        self.offer_symbols((symbol,))

    def offer_symbols(self, symbols):
        import re
        valid=[s for s in symbols if isinstance(s,str) and re.fullmatch(r'[A-Z][A-Z0-9.\-]{0,11}',s)]
        with self._lock:
            for symbol in valid:
                if len(self._symbols)<24:self._symbols.add(symbol)
            if self._symbols and self._thread is None:
                self._thread=Thread(target=self._news_worker,name='grok-independent-news-desk',daemon=True)
                self._thread.start()

    def shared_evidence(self, captured_at):
        with self._lock:
            record = self._news
            if not record or not valid_news(record['news'], captured_at):
                return {'status': 'NO_PRIOR_FRESH_CITED_NEWS', 'execution_authorized': False}
            if datetime.fromisoformat(record['completed_at']) > captured_at:
                return {'status': 'NEWS_NOT_YET_AVAILABLE_AT_EVENT', 'execution_authorized': False}
            return json.loads(encode(record))

    def _news_worker(self):
        # Durable hourly slots survive restarts. NEWS runs five minutes before
        # later nominal engine slots, up to nine cycles during the parent trial.
        while not self._stop.is_set():
            try:
                if not self.budget.claim_sample('NEWS_DESK', 'desk'):
                    if self._stop.wait(self.budget.next_desk_wait()):
                        return
                    continue
            except (ValueError, GrokUnavailable):
                return
            with self._lock:
                symbols = tuple(sorted(self._symbols))
                self._news = None
            now = self.clock()
            identifier = None
            began = monotonic()
            news = {'status': 'UNAVAILABLE', 'execution_authorized': False, 'orders': 0}
            try:
                identifier = self.budget.reserve('NEWS_DESK', 'NEWS', {'symbols': symbols, 'as_of': now.isoformat()})
                news.update(search_news(self.environment['XAI_API_KEY'], self.environment['ATLAS_GROK_MODEL'], symbols, now))
                news.update(observed_at=now.isoformat(), expires_at=(now + timedelta(minutes=15)).isoformat(),
                    execution_authorized=False, orders=0)
            except Exception as exc:
                news.update(failure_diagnostic(exc))
            finally:
                news['latency_ms'] = round((monotonic() - began) * 1000)
                if identifier:
                    self.budget.finish(identifier, news)
            if self._stop.is_set() or not valid_news(news, self.clock()):
                continue
            parent = self
            class ScoutClient:
                def request(self, role, instruction, context, schema):
                    return parent.request('NEWS_DESK', 'SCOUT', role, instruction, context, schema)
            try:
                ranked = scout(ScoutClient(), news, symbols, self.clock())
                shared = {'news': news, 'scout': ranked, 'completed_at': self.clock().isoformat(),
                    'execution_authorized': False, 'orders': 0}
                with self._lock:
                    self._news = shared
            except Exception:
                pass

    def close(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=31)


def review_team(teams, snapshot, context, clock=lambda: datetime.now(UTC)):
    engine = snapshot.engine.value
    if engine not in ENGINES:
        raise ValueError('SEPARATE_EQUITY_TEAM_REQUIRED')
    try:
        claimed=teams.budget.claim_sample(engine,snapshot.event_id)
    except (ValueError,GrokUnavailable):
        return dict(engine=engine,event_id=snapshot.event_id,symbol=snapshot.symbol,
            status='TEAM_WINDOW_CLOSED',execution_authorized=False,orders=0,simulated_fills=0)
    if not claimed:
        return dict(engine=engine,event_id=snapshot.event_id,symbol=snapshot.symbol,
            status='HOURLY_SLOT_ALREADY_SAMPLED',received_at=clock().isoformat(),
            execution_authorized=False,orders=0,simulated_fills=0)
    teams.offer_symbol(snapshot.symbol)
    shared = teams.shared_evidence(snapshot.captured_at)
    began = monotonic()
    parent = teams
    class StageClient:
        def __init__(self, stage): self.stage = stage
        def request(self, role, instruction, evidence, schema):
            stage = self.stage
            evidence['team_engine'] = engine
            evidence['collaboration_stage'] = stage
            return parent.request(engine, stage, engine if stage == 'ANALYST' else 'MASTER',
                ('You serve ONLY the ' + engine + ' team. ' +
                 ('Independently assess this setup.' if stage == 'ANALYST' else
                  'Critique the supplied analyst opinion against the original evidence; decide independently.') +
                 ' Other teams cannot supply or receive entry authority. ' + instruction), evidence, schema)
    class SharedContext:
        def __init__(self, analyst=None): self.analyst = analyst
        def payload(self, s):
            result = context.payload(s)
            result.update(shared_news_and_scout=shared)
            if self.analyst is not None:
                result['same_engine_analyst'] = self.analyst
            return result
    analyst = review(StageClient('ANALYST'), snapshot, SharedContext(), clock=clock)
    if analyst['status'] == 'PROVIDER_OR_PROPOSAL_UNAVAILABLE':
        coordinator = dict(analyst)
    else:
        coordinator = review(StageClient('COORDINATOR'), snapshot, SharedContext(analyst), clock=clock)
    elapsed = round((monotonic() - began) * 1000)
    age = (clock() - snapshot.captured_at).total_seconds()
    coordinator.update(scope='ENGINE_LOCAL_COLLABORATION_RESEARCH_V1',
        analyst=analyst, shared_evidence_sha256=sha256(encode(shared).encode()).hexdigest(),
        shared_news_status=shared.get('status', 'FRESH_PRIOR_CITED_RESEARCH'),
        collaboration_latency_ms=elapsed, engine_budget_seconds=BUDGET_SECONDS[engine],
        within_engine_timing_budget=0 <= age <= BUDGET_SECONDS[engine],
        native_path_waits_for_grok=False, execution_authorized=False, orders=0, simulated_fills=0)
    return coordinator
