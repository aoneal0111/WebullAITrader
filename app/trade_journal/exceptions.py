class TradeJournalError(Exception): pass
class TradeJournalValidationError(TradeJournalError): pass
class TradeJournalDependencyError(TradeJournalError): pass
class TradeJournalEvaluationError(TradeJournalError): pass
class TradeJournalSerializationError(TradeJournalError): pass
