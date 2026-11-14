"""Native experiment validity checks; no order or network entry point."""
from copy import deepcopy
import logging


def isolate_native_parameters(strategy):
    """Prevent subclass hyperparameters mutating another experiment's defaults."""
    from freqtrade.strategy.parameters import BaseParameter
    for name in dir(strategy):
        value = getattr(strategy, name)
        if isinstance(value, BaseParameter):
            setattr(strategy, name, deepcopy(value))
    strategy.ft_load_hyper_params(False)


class CallbackAudit(logging.Handler):
    """Freqtrade can suppress callback exceptions; such results are invalid."""
    def __init__(self):
        super().__init__(logging.WARNING)
        self.count = 0
        self.examples = []

    def emit(self, record):
        self.count += 1
        if len(self.examples) < 10:
            self.examples.append(record.getMessage()[:500])

    def report(self):
        return dict(callback_failures=self.count, examples=self.examples,
                    result_valid=self.count == 0)
