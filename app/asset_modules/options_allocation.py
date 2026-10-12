"""Offline two-ledger options handoff to shared PAPER allocation; no broker."""
from datetime import timedelta
from decimal import Decimal as D, localcontext
from hashlib import sha256

from app.asset_modules.admission_controller import OptionsEntryReservation, PaperAdmissionController, _text
from app.asset_modules.contract_serialization import encode
from app.asset_modules.engine_catalog import EngineId
from app.asset_modules.options_paper import OptionsPaper
from app.asset_modules.options_signals import OptionsProposal


class OptionsAllocationRouter:
    """One explicitly bound options simulator per shared account allocation.

    Cross-database dispatch is not atomic. Unknown dispatches hold allocation
    until durable matching evidence exists; absence is never proof of refusal.
    """
    def __init__(self, controller, ledger, *, account_id):
        if not isinstance(controller,PaperAdmissionController) or not isinstance(ledger,OptionsPaper):
            raise ValueError('Shared PAPER controller and options simulator required')
        _text(account_id,'account_id')
        controller.allocation_balance(account_id)  # No implicit account/budget creation.
        self.controller,self.ledger,self.account_id = controller,ledger,account_id
        for position in ledger.snapshot()['positions']:
            identity = position['intent_id']
            row = controller.command(account_id,identity)
            evidence = ledger.intent_evidence(identity)
            if (row is None or row['state'] not in {'SUBMITTING','ACKNOWLEDGED'}
                    or row['request'].get('ledger_id')!=ledger.ledger_id
                    or row['request'].get('plan_fingerprint')!=evidence['fingerprint']):
                raise ValueError('Reconcile existing options exposure before binding')
        self._token = ledger.bind_allocation(controller.identity,account_id)
        controller.bind_options_ledger(account_id,ledger.ledger_id)

    def _reference(self, identity):
        return 'options-paper:'+self.ledger.ledger_id+':'+identity

    def reservation(self, proposal, quote):
        if not isinstance(proposal,OptionsProposal): raise ValueError('Typed options proposal required')
        p,c = proposal.plan,proposal.plan.contract
        with localcontext() as ctx:
            ctx.prec = 100
            qty = D(p.quantity)
            costs = (2*self.ledger.slippage*c.multiplier+2*p.fee_per_side)*qty
            capital = p.entry*c.multiplier*qty+costs
            risk = abs(p.entry-p.stop)*qty*c.multiplier+costs
        return OptionsEntryReservation(
            command_id=proposal.intent_id,account_id=self.account_id,engine=EngineId.OPTIONS,
            lifecycle_id=proposal.intent_id,instrument=c.symbol,policy_version=p.policy,
            side='BUY' if p.side=='LONG' else 'SELL',quantity=qty,entry_limit=p.entry,
            structural_stop=p.stop,multiplier=c.multiplier,capital=capital,loss_budget=risk,
            quote_at=quote.source_at,expires_at=min(p.created_at+timedelta(seconds=2),p.deadline),
            execution_cost_budget=costs,ledger_id=self.ledger.ledger_id,
            plan_fingerprint=sha256(encode(p).encode()).hexdigest())

    def reconcile(self):
        results = {}
        with self.ledger.lock:
            self.ledger.bind_allocation(self.controller.identity,self.account_id)
            rows,after = [],''
            while True:
                page = self.controller.recovery_commands(self.account_id,after_command_id=after,limit=1000)
                rows.extend(page)
                if len(page)<1000: break
                after = page[-1]['request']['command_id']
            for row in rows:
                p = row['request']
                if p['engine']!=EngineId.OPTIONS.value: continue
                identity = p['command_id']
                if (p.get('capital_model')!='PAID_OPTION_PREMIUM'
                        or p.get('ledger_id')!=self.ledger.ledger_id):
                    results[identity] = 'IDENTITY_CONFLICT'
                    continue
                evidence = self.ledger.intent_evidence(identity)
                if row['state']=='RESERVED':
                    results[identity] = 'UNSUBMITTED' if evidence is None else 'IDENTITY_CONFLICT'
                    continue
                if evidence is None:
                    results[identity] = 'UNCERTAIN'
                    continue
                reference = self._reference(identity)
                if (evidence['fingerprint']!=p['plan_fingerprint']
                        or row['broker_order_id'] not in {None,reference}):
                    results[identity] = 'IDENTITY_CONFLICT'
                    continue
                if evidence['state']=='OPEN':
                    if row['state']=='SUBMITTING':
                        self.controller.acknowledge(identity,EngineId.OPTIONS,reference)
                    results[identity] = 'HELD'
                elif evidence['net_pnl'] is None:
                    results[identity] = 'MISSING_SETTLEMENT'
                else:
                    self.controller.settle_options_flat(identity,ledger_id=self.ledger.ledger_id,
                        plan_fingerprint=p['plan_fingerprint'],net_pnl=evidence['net_pnl'],
                        settled_at=evidence['settled_at'],reference=reference)
                    results[identity] = 'CLOSED'
            # Local positions created outside this router cannot evade shared exposure.
            for position in self.ledger.snapshot()['positions']:
                identity = position['intent_id']
                row = self.controller.command(self.account_id,identity)
                if row is None or row['state']!='ACKNOWLEDGED' or results.get(identity)!='HELD':
                    results[identity] = 'UNMANAGED_OPTIONS_EXPOSURE'
        return results

    def submit(self, proposal, quote, *, now):
        with self.ledger.lock:
            reconciliation = self.reconcile()
            if any(state not in {'HELD','CLOSED'} for state in reconciliation.values()):
                return 'ALLOCATION_RECONCILIATION_REQUIRED'
            if self.ledger.intent_state(proposal.intent_id) is not None:
                return 'DUPLICATE_SIGNAL'
            if self.controller.command(self.account_id,proposal.intent_id) is not None:
                return 'ALLOCATION_COMMAND_EXISTS'
            if not self.ledger.enabled: return 'ENTRIES_DISABLED'
            r = self.reservation(proposal,quote)
            admission = self.controller.reserve(r,now=now)
            if admission.state!='RESERVED': return admission.reason or admission.state
            if not self.controller.claim_dispatch(r.command_id,EngineId.OPTIONS,now=now):
                return 'ALLOCATION_DISPATCH_NOT_CLAIMED'
            # Any exception from this point leaves a durable uncertain reservation.
            result = self.ledger.enter(proposal.intent_id,proposal.plan,quote,now=now,allocation_token=self._token)
            if result=='OPENED':
                self.controller.acknowledge(r.command_id,EngineId.OPTIONS,self._reference(r.command_id))
            elif self.ledger.intent_state(r.command_id) is None:
                self.controller.refuse_options_dispatch(r.command_id,reference='options-paper-refusal:'+result)
            else:
                return 'ALLOCATION_RECONCILIATION_REQUIRED'
            return result
