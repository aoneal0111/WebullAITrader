"""Offline spot-crypto handoff to shared PAPER capital, with durable recovery."""
from datetime import timedelta
from decimal import Decimal as D, localcontext, ROUND_CEILING
from hashlib import sha256

from app.asset_modules.admission_controller import CryptoEntryReservation, PaperAdmissionController, _text
from app.asset_modules.crypto_paper import CryptoPaper, CryptoEntryRefused
from app.asset_modules.engine_catalog import EngineId

POLICY='CRYPTO_SPOT_SNAPSHOT_PAPER_V1'


class CryptoAllocationRouter:
    def __init__(self,controller,ledger,*,account_id,authorize):
        if not isinstance(controller,PaperAdmissionController) or not isinstance(ledger,CryptoPaper):
            raise ValueError('Shared PAPER controller and crypto ledger required')
        if not callable(authorize): raise ValueError('Trusted authorization callback required')
        _text(account_id,'account_id');controller.allocation_balance(account_id)
        self.controller,self.ledger,self.account_id,self.authorize=controller,ledger,account_id,authorize
        self.enabled=False
        for position in ledger.snapshot()['positions']:
            identity=ledger.active_entry_id(position['symbol'])
            evidence=None if identity is None else ledger.intent_evidence(identity)
            row=None if identity is None else controller.command(account_id,self.command_id(identity))
            if (not evidence or evidence['state']!='OPEN' or row is None
                    or row['state'] not in {'SUBMITTING','ACKNOWLEDGED'}
                    or row['request'].get('ledger_id')!=ledger.ledger_id
                    or row['request'].get('plan_fingerprint')!=evidence['fingerprint']):
                raise ValueError('Reconcile existing crypto exposure before binding')
        self._token=ledger.bind_allocation(controller.identity,account_id)
        controller.bind_crypto_ledger(account_id,ledger.ledger_id)

    def set_enabled(self,value):
        if type(value) is not bool: raise ValueError('Explicit entry activation required')
        with self.ledger.lock: self.enabled=value

    def command_id(self,identity):
        return 'CRYPTO|'+sha256((self.ledger.ledger_id+'|'+identity).encode()).hexdigest()

    def _reference(self,command):
        return 'crypto-paper:'+self.ledger.ledger_id+':'+command

    def reservation(self,proposal,quote,*,now):
        identity=_text(proposal.get('id'),'local intent')
        if len(identity)>200: raise ValueError('Bounded local intent required')
        prepared=self.ledger.prepare_entry(proposal,quote,now=now)
        _,ask,source_at,price,stop,_,_,qty,cost,_,fingerprint=prepared
        with localcontext() as ctx:
            ctx.prec=100
            costs=((price-ask)*qty+price*qty*self.ledger.fee
                   +ask*qty*(self.ledger.slippage+(1-self.ledger.slippage)*self.ledger.fee))
            costs=costs.quantize(D('.00000001'),rounding=ROUND_CEILING)
            capital=(ask*qty+costs).quantize(D('.00000001'),rounding=ROUND_CEILING)
            risk=((ask-stop)*qty+costs).quantize(D('.00000001'),rounding=ROUND_CEILING)
        r=CryptoEntryReservation(self.command_id(identity),self.account_id,EngineId.CRYPTO,
            'CRYPTO|'+identity,proposal['symbol'],POLICY,'BUY',qty,ask,stop,D(1),capital,risk,
            source_at,now+timedelta(seconds=2),costs,self.ledger.ledger_id,fingerprint,identity)
        return r,prepared

    def reconcile(self):
        with self.ledger.lock:
            self.ledger.bind_allocation(self.controller.identity,self.account_id)
            results={};after=''
            while True:
                page=self.controller.recovery_commands(self.account_id,after_command_id=after,limit=1000)
                for row in page:
                    p=row['request']
                    if p['engine']!=EngineId.CRYPTO.value: continue
                    command=p['command_id'];identity=p.get('local_intent_id')
                    if (p.get('capital_model')!='PAID_CRYPTO_SPOT' or p.get('ledger_id')!=self.ledger.ledger_id
                            or not isinstance(identity,str) or self.command_id(identity)!=command):
                        results[command]='IDENTITY_CONFLICT';continue
                    e=self.ledger.intent_evidence(identity)
                    if row['state']=='RESERVED':
                        results[command]='UNSUBMITTED' if e is None else 'IDENTITY_CONFLICT';continue
                    if e is None: results[command]='UNCERTAIN';continue
                    reference=self._reference(command)
                    if (e['state'] not in {'OPEN','CLOSED'} or e['fingerprint']!=p['plan_fingerprint']
                            or e['symbol']!=p['instrument'] or e['bought_quantity']!=D(p['quantity'])
                            or e['entry_cost']>D(p['capital']) or row['broker_order_id'] not in {None,reference}):
                        results[command]='IDENTITY_CONFLICT';continue
                    if e['state']=='OPEN':
                        if row['state']=='SUBMITTING': self.controller.acknowledge(command,EngineId.CRYPTO,reference)
                        results[command]='HELD'
                    elif e['settled_at'] is None or e['remaining_quantity']!=0:
                        results[command]='MISSING_SETTLEMENT'
                    else:
                        self.controller.settle_crypto_flat(command,ledger_id=self.ledger.ledger_id,
                            plan_fingerprint=p['plan_fingerprint'],net_pnl=e['realized_pnl'],
                            settled_at=e['settled_at'],reference=reference)
                        results[command]='CLOSED'
                if len(page)<1000: break
                after=page[-1]['request']['command_id']
            for position in self.ledger.snapshot()['positions']:
                identity=self.ledger.active_entry_id(position['symbol'])
                command=position['symbol'] if identity is None else self.command_id(identity)
                row=None if identity is None else self.controller.command(self.account_id,command)
                if row is None or row['state']!='ACKNOWLEDGED' or results.get(command)!='HELD':
                    results[command]='UNMANAGED_CRYPTO_EXPOSURE'
            return results

    def protect(self,quotes,*,now):
        problems=self.ledger.protect(quotes,now=now)
        reconciliation=self.reconcile()
        return {'protection':problems,'reconciliation':reconciliation}

    def submit(self,proposal,quotes,*,now):
        with self.ledger.lock:
            outcome=self.protect(quotes,now=now)
            if any(s not in {'HELD','CLOSED'} for s in outcome['reconciliation'].values()):
                result='ALLOCATION_RECONCILIATION_REQUIRED'
            elif not self.enabled: result='ENTRIES_DISABLED'
            else:
                proposal=dict(proposal)
                identity=proposal.get('id')
                r,prepared=self.reservation(proposal,quotes.get(proposal.get('symbol')),now=now)
                if self.ledger.intent_evidence(identity) is not None: result='DUPLICATE_SIGNAL'
                elif self.controller.command(self.account_id,r.command_id) is not None: result='ALLOCATION_COMMAND_EXISTS'
                elif self.authorize(r,now) is not True: result='ENTRY_NOT_AUTHORIZED'
                else:
                    admission=self.controller.reserve(r,now=now)
                    if admission.state!='RESERVED': result=admission.reason or admission.state
                    elif not self.controller.claim_dispatch(r.command_id,EngineId.CRYPTO,now=now): result='ALLOCATION_DISPATCH_NOT_CLAIMED'
                    else:
                        # Unknown exceptions leave SUBMITTING and never auto-retry.
                        try:
                            filled=self.ledger.apply(proposal,quotes,now=now,allocation_token=self._token,expected_entry=prepared)
                        except CryptoEntryRefused:
                            if self.ledger.intent_evidence(identity) is not None: raise
                            self.controller.refuse_crypto_dispatch(r.command_id,reference='crypto-paper-native-refusal')
                            result='LOCAL_ENTRY_REFUSED'
                        else:
                            if not filled: result='ALLOCATION_RECONCILIATION_REQUIRED'
                            else:
                                self.controller.acknowledge(r.command_id,EngineId.CRYPTO,self._reference(r.command_id))
                                result='OPENED'
            return {'entry':result,**outcome}
