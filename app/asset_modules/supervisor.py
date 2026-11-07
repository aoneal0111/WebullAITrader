"""Bounded AI proposals. Models have no code, shell, broker, or risk-edit tools."""
from datetime import datetime, UTC
from threading import Event, Thread, RLock
from time import monotonic
from uuid import uuid4


class CryptoSupervisor:
    """One off-thread request per minute; protection runs independently of AI."""
    def __init__(self, paper, source, provider=None, *, entry_router=None,
                 maximum_quote_age_seconds=None,execution_limits=None,history_source=None,
                 execution_quote_source=None,evidence_sink=None):
        if maximum_quote_age_seconds not in (None,2):raise ValueError('Explicit two-second crypto trial quote boundary required')
        if entry_router is not None:
            from app.asset_modules.crypto_allocation import CryptoAllocationRouter
            if not isinstance(entry_router, CryptoAllocationRouter) or entry_router.ledger is not paper:
                raise ValueError('Crypto allocation router must own this paper ledger')
            entry_router.set_enabled(False)
        self.paper, self.source, self.provider = paper, source, provider
        self.maximum_quote_age_seconds=maximum_quote_age_seconds
        self.execution_limits=execution_limits
        self.history_source=history_source
        self.execution_quote_source=execution_quote_source
        self.evidence_sink=evidence_sink
        self._last_protection_evidence=None
        self.entry_router = entry_router
        self.gate = RLock()
        self._entries_enabled = False
        self.protection_thread = None
        self.protection_status = "OFF"
        self.status = 'OFF'
        self.stop_event = Event()
        self.thread = None
        self.next_request = 0.0
        self.request_count = 0

    @property
    def entries_enabled(self):
        with self.gate:
            return self._entries_enabled

    @entries_enabled.setter
    def entries_enabled(self, value):
        if type(value) is not bool:
            raise ValueError('Explicit entry activation required')
        with self.gate:
            if self.entry_router is not None:
                self.entry_router.set_enabled(value)
            self._entries_enabled = value

    def _protect(self, quotes, now):
        if self.maximum_quote_age_seconds is not None:
            quotes={k:v for k,v in quotes.items() if 0<=(now-v.timestamp).total_seconds()<=2}
        if self.entry_router is None:
            problems = self.paper.protect(quotes, now=now)
        else:
            result = self.entry_router.protect(quotes, now=now)
            problems = list(result['protection'])
            problems.extend('ALLOCATION: '+state for state in set(result['reconciliation'].values())
                            if state not in {'HELD', 'CLOSED'})
        self.protection_status = '; '.join(problems) or 'MONITORING'
        if self.evidence_sink is not None:
            state=self.paper.snapshot();events=state['events'][:5]
            if events and events[0]!=self._last_protection_evidence:
                self._last_protection_evidence=events[0]
                self.evidence_sink('CRYPTO_PROTECTION_EVIDENCE',dict(at=now,positions=state['positions'],events=events))

    def start(self):
        if self.thread is not None:
            return
        self.stop_event.clear()
        self.thread = Thread(target=self._run, name='atlas-crypto-paper-supervisor', daemon=True)
        self.thread.start()
        self.protection_thread = Thread(target=self._protect_run, name='atlas-crypto-paper-protection', daemon=True)
        self.protection_thread.start()

    def close(self):
        self.entries_enabled = False
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(20)
            if self.thread.is_alive():
                return False
        if self.protection_thread is not None:
            self.protection_thread.join(5)
            if self.protection_thread.is_alive():
                return False
        self.thread = self.protection_thread = None
        self.status = 'OFF'
        return True

    def _protect_run(self):
        while not self.stop_event.is_set():
            try:
                quotes = {row.pair.canonical_symbol: row for row in self.source()}
                if self.execution_quote_source is not None:
                    owned=tuple(p['symbol'] for p in self.paper.snapshot()['positions'])
                    if owned:quotes=self._execution_quotes(owned)
                self._protect(quotes, datetime.now(UTC))
            except Exception as error:
                self.protection_status = f'ERROR: {type(error).__name__}'
            self.stop_event.wait(5 if self.execution_quote_source is not None else 1)

    def _execution_quotes(self,symbols):
        rows=tuple(self.execution_quote_source(tuple(symbols)))
        if self.evidence_sink is not None:
            self.evidence_sink('CRYPTO_EXECUTION_QUOTES',dict(quotes=rows,received_at=datetime.now(UTC)))
        return {row.pair.canonical_symbol:row for row in rows}

    def _run(self):
        while not self.stop_event.is_set():
            try:
                self.tick()
            except Exception as error:
                # Exception class only: provider errors can contain request details.
                self.status = f'ERROR: {type(error).__name__}'
            self.stop_event.wait(1)

    def tick(self):
        quotes = {row.pair.canonical_symbol:row for row in self.source()}
        now = datetime.now(UTC)
        self._protect(quotes, now)
        if not self.entries_enabled:
            self.status = 'MANAGING EXISTING POSITIONS · NEW AI PROPOSALS OFF'
            return
        if self.provider is None:
            self.status = 'AI NOT CONFIGURED'
            return
        if self.request_count >= 120:
            self.status = 'SESSION AI REQUEST LIMIT REACHED'
            return
        if monotonic() < self.next_request:
            return
        fresh = {k:v for k,v in quotes.items() if 0 <= (now-v.timestamp).total_seconds() <= 60}
        if not fresh:
            self.status = 'AWAITING FRESH QUOTES'
            return
        self.next_request = monotonic()+60
        self.request_count += 1
        context = {'asset':'CRYPTO','mode':'PAPER','at':now.isoformat(),
                   'account':{k:v for k,v in self.paper.snapshot().items() if k != 'events'},
                   'markets':[row.to_record() for row in list(fresh.values())[:10]]}
        if self.execution_limits is not None:
            context['execution_limits']=dict(self.execution_limits)
        if self.history_source is not None:
            from app.asset_modules.daily_research_journal import safe
            context['completed_provider_bars']={k:safe(tuple(self.history_source(k,now))) for k in list(fresh)[:2]}
        proposals = self.provider.propose(context)
        if self.stop_event.is_set() or not self.entries_enabled:
            return
        # Re-read quotes after the slow model call; never fill against a stale request snapshot.
        current = {row.pair.canonical_symbol:row for row in self.source()}
        if self.execution_quote_source is not None:
            symbols=tuple(dict.fromkeys(p.get('symbol') for p in proposals
                if p.get('symbol') in fresh and p.get('action') in {'BUY','SELL'}))[:2]
            current=self._execution_quotes(symbols)
        if self.maximum_quote_age_seconds is not None:
            dispatch_now=datetime.now(UTC)
            current={k:v for k,v in current.items() if 0<=(dispatch_now-v.timestamp).total_seconds()<=2}
        outcomes = []
        for proposal in proposals:
            if proposal.get('symbol') not in fresh:
                outcomes.append('REJECTED: unknown pair')
                self.paper.record_decision(proposal, outcomes[-1])
                continue
            proposal = {**proposal, 'id':str(uuid4())}
            try:
                with self.gate:
                    if self.stop_event.is_set() or not self._entries_enabled:
                        return
                    dispatch_at = datetime.now(UTC)
                    if self.entry_router is not None and proposal.get('action') == 'BUY':
                        result = self.entry_router.submit(proposal, current, now=dispatch_at)
                        outcome = 'PAPER FILLED' if result['entry'] == 'OPENED' else result['entry']
                    else:
                        changed = self.paper.apply(proposal, current, now=dispatch_at)
                        outcome = 'PAPER FILLED' if changed else 'HOLD'
                        if self.entry_router is not None:
                            self._protect(current, datetime.now(UTC))
                outcomes.append(outcome)
            except (ValueError, KeyError, ArithmeticError) as error:
                if self.entry_router is None:
                    outcomes.append(f'REJECTED: {str(error)[:160]}')
                else:
                    # A post-claim error may have filled. Never describe it as a refusal.
                    outcomes.append(f'ALLOCATION REVIEW REQUIRED: {type(error).__name__}')
            self.paper.record_decision(proposal, outcomes[-1])
            if self.evidence_sink is not None:
                self.evidence_sink('CRYPTO_GROK_DISPATCH',dict(at=datetime.now(UTC),proposal=proposal,outcome=outcomes[-1]))
        self.status = '; '.join(outcomes) or 'HOLD'


def configured_provider():
    from app.asset_modules.grok import GrokProposalProvider, configured_client
    client = configured_client()
    return GrokProposalProvider(client) if client is not None else None
