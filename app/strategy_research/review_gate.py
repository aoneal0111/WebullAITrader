"""Frozen research review criteria. Passing allows review, never execution.

These are Atlas engineering targets, not guarantees or statistical proof.
Evidence must be independently reconciled; supplied statistics are not audited
by this module. Win rate and confidence alone cannot authorize a strategy.
"""
from dataclasses import dataclass
from decimal import Decimal as D
from math import sqrt

from .multi_asset import PROFILES


@dataclass(frozen=True)
class ReviewEvidence:
    candidate: str
    closed: int
    wins: int
    expectancy_r: D
    stress_expectancy_r: D
    profit_factor_r: D
    drawdown_r: D
    independent_date_windows: int
    source_verified: bool
    costs_verified: bool
    canonical_replay_passed: bool
    paper_forward_passed: bool
    asset_constraints_verified: bool

    def __post_init__(self):
        if self.candidate not in {p.candidate for p in PROFILES}:
            raise ValueError('Frozen candidate required')
        if (any(type(v) is not int for v in (self.closed, self.wins, self.independent_date_windows))
                or not 0 <= self.wins <= self.closed <= 1_000_000
                or not 0 <= self.independent_date_windows <= 1000):
            raise ValueError('Valid integer evidence counts required')
        values = (self.expectancy_r, self.stress_expectancy_r, self.profit_factor_r, self.drawdown_r)
        if any(not isinstance(v, D) or not v.is_finite() for v in values) or min(values[2:]) < 0:
            raise ValueError('Finite Decimal statistics required')
        flags = (self.source_verified, self.costs_verified, self.canonical_replay_passed,
                 self.paper_forward_passed, self.asset_constraints_verified)
        if any(type(v) is not bool for v in flags):
            raise ValueError('Explicit evidence verification flags required')


def review(evidence):
    if not isinstance(evidence, ReviewEvidence):
        raise ValueError('Typed reconciled review evidence required')
    e = evidence
    reasons = []
    if e.closed < 100:
        reasons.append('FEWER_THAN_100_CLOSED_TRADES')
    rate = e.wins/e.closed if e.closed else None
    lower = None
    if e.closed:
        z, n, p = 1.96, e.closed, rate
        lower = (p+z*z/(2*n)-z*sqrt(p*(1-p)/n+z*z/(4*n*n)))/(1+z*z/n)
    if rate is None or rate < .60:
        reasons.append('WIN_RATE_BELOW_60_PERCENT_TARGET')
    if lower is None or lower <= .50:
        reasons.append('DESCRIPTIVE_WIN_RATE_LOWER_BOUND_NOT_ABOVE_HALF')
    if e.expectancy_r <= 0 or e.stress_expectancy_r <= 0:
        reasons.append('NONPOSITIVE_NET_OR_STRESS_EXPECTANCY')
    if e.profit_factor_r < D('1.20'):
        reasons.append('PROFIT_FACTOR_BELOW_1_20')
    if e.drawdown_r > 10:
        reasons.append('CLOSED_TRADE_DRAWDOWN_ABOVE_10R')
    if e.independent_date_windows < 3:
        reasons.append('FEWER_THAN_THREE_UNSEEN_DATE_WINDOWS')
    for name in ('source_verified', 'costs_verified', 'canonical_replay_passed',
                 'paper_forward_passed', 'asset_constraints_verified'):
        if not getattr(e, name):
            reasons.append(name.upper()+'_MISSING')
    return {'candidate': e.candidate, 'scope': 'RESEARCH_REVIEW_NOT_EXECUTION_AUTHORIZATION',
            'decision': 'ELIGIBLE_FOR_MANUAL_REVIEW' if not reasons else 'INSUFFICIENT_EVIDENCE',
            'reasons': reasons, 'win_rate': rate, 'descriptive_wilson_lower': lower,
            'execution_authorized': False,
            'limitations': ['Trade independence is unproven; Wilson bound is descriptive.',
                           'Window count and verification flags require external audit.',
                           'Open risk, portfolio correlation and tail scenarios require separate review.']}
