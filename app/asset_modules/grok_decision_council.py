"""Bounded seven-role decision planning. Decisions are not execution grants.

Engine advisers own immutable native candidates; Master selects among those
recommendations. Learning may nominate tested policies. No controller, broker,
position mutation, code writer or desktop activation exists in this module.
"""
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import islice
import json
from threading import Lock, Event

from .engine_catalog import EngineId
from .grok_coordinator import Candidate, GrokSuite, IntentKind
from .grok_learning import LearningJournal


@dataclass(frozen=True)
class CouncilDecision:
    cycle_id: str
    state: str
    roles: tuple[tuple[str, str], ...]
    selected: tuple[Candidate, ...] = ()
    execution_authorized: bool = False
    promotion: str = 'NONE'


class GrokDecisionCouncil:
    """One immutable cycle at a time; fail closed after any review failure.

    A native dispatcher must separately bind each selected candidate to its
    original command, owner/revision and current quote/risk/admission checks.
    Neither Master nor Learning may edit candidate prices, size or protections.
    """
    def __init__(self, suite, *, journal=None, clock=lambda: datetime.now(UTC)):
        if not isinstance(suite, GrokSuite):
            raise TypeError('Shared GrokSuite required')
        if journal is not None and not isinstance(journal, LearningJournal):
            raise TypeError('Explicit learning journal required')
        self.suite, self.journal, self.clock = suite, journal, clock
        self._lock = Lock()
        self._stop = Event()
        self._seen = set()

    def stop(self):
        # Independent of a model call: revoke completion without waiting on it.
        self._stop.set()

    def review(self, cycle_id, candidates, *, engine_evidence, account_evidence,
               review_learning=False):
        if not isinstance(cycle_id, str) or not cycle_id or len(cycle_id)>80:
            raise ValueError('Bounded cycle identity required')
        if not self._lock.acquire(blocking=False):
            return CouncilDecision(cycle_id,'BUSY',())
        roles=[]
        def result(state, selected=()):
            return CouncilDecision(cycle_id,state,tuple(roles),selected)
        try:
            if self._stop.is_set():return result('STOPPED')
            if cycle_id in self._seen:return result('DUPLICATE_CYCLE')
            if len(self._seen)>=100:return result('SESSION_CYCLE_LIMIT')
            batch=tuple(islice(candidates,33))
            if len(batch)>32 or any(not isinstance(c,Candidate) for c in batch):
                raise ValueError('At most 32 immutable native candidates required')
            if len({c.candidate_id for c in batch})!=len(batch):
                raise ValueError('Globally unique candidate identities required')
            now=self.clock()
            if now.utcoffset() is None or any(not 0<(c.valid_until-now).total_seconds()<=60 for c in batch):
                raise ValueError('Current candidates with at most 60-second validity required')
            # Copy all caller evidence before the first external request.
            raw=json.dumps(dict(engine=engine_evidence,account=account_evidence),allow_nan=False)
            if len(raw.encode())>65536:raise ValueError('Evidence exceeds bound')
            data=json.loads(raw)
            if not isinstance(data['engine'],dict) or not isinstance(data['account'],dict):
                raise ValueError('Structured native evidence required')
            if set(data['engine'])-{e.value for e in EngineId}:
                raise ValueError('Unknown engine evidence')
            self._seen.add(cycle_id)
            # Explicit empty reviews clear every absent engine's previous advice.
            for engine in EngineId:
                if self._stop.is_set():return result('STOPPED')
                owned=tuple(c for c in batch if c.engine is engine)
                try:
                    advice=self.suite.review(engine,owned,evidence=data['engine'].get(engine.value,{}))
                except Exception:
                    roles.append((engine.value,'REVIEW_UNAVAILABLE'))
                    return result('REVIEW_UNAVAILABLE')
                roles.append((engine.value,'SELECTED' if advice.selected else 'HOLD' if owned else 'NO_NATIVE_CANDIDATES'))
            if self._stop.is_set():return result('STOPPED')
            try:master=self.suite.coordinate(account_evidence=data['account'])
            except Exception:
                roles.append(('MASTER','REVIEW_UNAVAILABLE'))
                return result('REVIEW_UNAVAILABLE')
            bound={c.candidate_id:c for c in batch}
            at=self.clock()
            if (self._stop.is_set() or master.role!='MASTER'
                    or master.session!=self.suite.master.session
                    or not 0<=(at-master.observed_at).total_seconds()<=2
                    or any(bound.get(c.candidate_id) is not c or c.valid_until<=at for c in master.selected)):
                return result('REVOKED_OR_EXPIRED')
            roles.append(('MASTER','SELECTED' if master.selected else 'HOLD'))
            if review_learning and self.journal is not None:
                # Learning receives paired research evidence from its journal;
                # its selections cannot replace the engine/master command plan.
                try:
                    learned=self.journal.review_next(client=self.suite.client)
                    roles.append(('LEARNING','NO_EXPERIMENT' if learned is None else learned['state']))
                except Exception:roles.append(('LEARNING','REVIEW_UNAVAILABLE'))
            else:roles.append(('LEARNING','NOT_REQUESTED'))
            # A learning call may consume time; revoke stale engine selections.
            at=self.clock()
            if (self._stop.is_set() or not 0<=(at-master.observed_at).total_seconds()<=2
                    or any(c.valid_until<=at for c in master.selected)):
                return result('REVOKED_OR_EXPIRED')
            return result('DECIDED' if master.selected else 'HOLD',master.selected)
        finally:self._lock.release()


def role_readiness():
    return dict(scope='GROK_SEVEN_ROLE_READINESS',execution_authorized=False,
        roles={**{e.value:dict(adviser_available=True,native_commands_required=True)
                     for e in EngineId},
               'MASTER':dict(ranks_native_candidates_only=True),
               'LEARNING':dict(paired_experiments_required=True,promotion='NONE')},
        supported_intents=[i.value for i in IntentKind],
        desktop_management_dispatch=False,options_futures_desktop_feed=False)


if __name__=='__main__':print(json.dumps(role_readiness(),indent=2))
