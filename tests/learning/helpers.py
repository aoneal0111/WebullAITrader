from dataclasses import replace
from decimal import Decimal

from app.learning import LearningPolicy, LearningRequest
from app.outcomes import OutcomeRecorder
from tests.outcomes.helpers import outcome_request


def outcome(pnl="10", realized_return="0.01"):
    base = OutcomeRecorder().record(outcome_request())
    return replace(base, realized_pnl=Decimal(pnl), realized_return=Decimal(realized_return))


def learning_request(outcomes=None, policy=None, **changes):
    values = {"outcomes": outcomes if outcomes is not None else
              (outcome(), outcome("-4", "-0.004"), outcome("0", "0")),
              "policy": policy or LearningPolicy()}
    values.update(changes)
    return LearningRequest(**values)
