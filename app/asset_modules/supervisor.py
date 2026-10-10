"""Bounded AI proposals. Models have no code, shell, broker, or risk-edit tools."""
from datetime import datetime, UTC
from threading import Event, Thread, RLock
from time import monotonic
from uuid import uuid4


class CryptoSupervisor:
    """One off-thread request per minute; protection runs independently of AI."""
    def __init__(self, paper, source, provider=None):
        self.paper, self.source, self.provider = paper, source, provider
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
        with self.gate:
            self._entries_enabled = bool(value)

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
                problems = self.paper.protect(quotes)
                self.protection_status = '; '.join(problems) or 'MONITORING'
            except Exception as error:
                self.protection_status = f'ERROR: {type(error).__name__}'
            self.stop_event.wait(1)

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
        self.paper.protect(quotes, now)
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
        proposals = self.provider.propose(context)
        if self.stop_event.is_set() or not self.entries_enabled:
            return
        # Re-read quotes after the slow model call; never fill against a stale request snapshot.
        current = {row.pair.canonical_symbol:row for row in self.source()}
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
                    changed = self.paper.apply(proposal, current)
                outcomes.append('PAPER FILLED' if changed else 'HOLD')
            except (ValueError, KeyError, ArithmeticError) as error:
                outcomes.append(f'REJECTED: {str(error)[:160]}')
            self.paper.record_decision(proposal, outcomes[-1])
        self.status = '; '.join(outcomes) or 'HOLD'


def configured_provider():
    from app.asset_modules.grok import GrokProposalProvider, configured_client
    client = configured_client()
    return GrokProposalProvider(client) if client is not None else None
