"""Bounded prospective gross-profit signals; no execution or policy authority."""
from decimal import Decimal

D = Decimal
VERSION = "WARRIOR_PROFIT_RETENTION_SHADOW_V1"


class ProfitRetentionShadow:
    activation_r = D(".4")
    retention = D(".5")

    def __init__(self):
        self.bought = self.remaining = self.cost = self.cash = D(0)
        self.stop = None
        self.entry_complete = False
        self.last_at = None
        self.peak = None
        self.armed = self.signalled = False
        self.problem = None
        self.reported_problem = False
        self.fill_ids = set()

    def fill(self, *, identity, side, quantity, price, at, stop=None, complete=False):
        if identity in self.fill_ids:
            return
        if len(self.fill_ids) >= 512:
            self.problem = "FILL_LIMIT"
            return
        self.fill_ids.add(identity)
        if side == "BUY":
            if self.last_at is not None and self.entry_complete:
                self.problem = "ENTRY_CHANGED_AFTER_OBSERVATION"
            if not self.entry_complete:
                self.last_at = None
            self.bought += quantity
            self.remaining += quantity
            self.cost += quantity * price
            self.cash -= quantity * price
            if stop is None or not stop.is_finite() or stop <= 0:
                self.problem = "STRUCTURAL_STOP_UNAVAILABLE"
            elif self.stop is not None and self.stop != stop:
                self.problem = "STRUCTURAL_STOP_CHANGED"
            else:
                self.stop = stop
            self.entry_complete = complete
            self.entry_at = at
        else:
            self.remaining -= quantity
            self.cash += quantity * price
            if self.remaining < 0:
                self.problem = "NEGATIVE_POSITION"

    def observe(self, *, at, source_at, bid, ask, max_age, max_gap):
        """Emit each transition once; never bridge an invalid observation interval."""
        if self.problem is None and self.remaining > 0:
            previous = self.last_at or self.entry_at
            age = D(str((at - source_at).total_seconds()))
            if at <= previous:
                self.problem = "NONINCREASING_OBSERVATION"
            elif D(str((at - previous).total_seconds())) > max_gap:
                self.problem = "QUOTE_GAP"
            elif age < 0:
                self.problem = "FUTURE_QUOTE"
            elif age > max_age:
                self.problem = "STALE_QUOTE"
            elif (bid is None or ask is None or not bid.is_finite() or not ask.is_finite()
                  or bid <= 0 or ask < bid):
                self.problem = "INVALID_QUOTE"
            elif self.stop is None or self.cost - self.bought * self.stop <= 0:
                self.problem = "INVALID_RISK"
            else:
                self.last_at = at
                if not self.entry_complete:
                    return None
                mark = self.cash + self.remaining * bid
                self.peak = mark if self.peak is None else max(self.peak, mark)
                risk = self.cost - self.bought * self.stop
                action = None
                if not self.armed and self.peak >= self.activation_r * risk:
                    self.armed = True
                    action = "PROFIT_SHADOW_ARMED"
                elif self.armed and not self.signalled and mark <= self.peak * self.retention:
                    self.signalled = True
                    action = "PROFIT_SHADOW_SIGNAL"
                if action:
                    return self.payload(action, gross_profit_mark=mark, observed_peak=self.peak,
                        retention_floor=self.peak * self.retention, initial_price_risk=risk,
                        remaining_quantity=self.remaining, bid=bid, quote_timestamp=source_at)
        if self.problem and not self.reported_problem:
            self.reported_problem = True
            return self.payload("PROFIT_SHADOW_UNAVAILABLE", reason=self.problem)
        return None

    def payload(self, action, **values):
        return dict(action=action, policy_version=VERSION, activation_r=self.activation_r,
                    peak_retention=self.retention, evidence="GROSS_BID_MARK_ON_ACTUAL_POSITION_NOT_EXIT_FILL",
                    execution_enabled=False, **values)
