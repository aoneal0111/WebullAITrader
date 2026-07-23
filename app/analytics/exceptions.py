class AnalyticsError(Exception): pass
class AnalyticsValidationError(AnalyticsError): pass
class AnalyticsDependencyError(AnalyticsError): pass
class AnalyticsEvaluationError(AnalyticsError): pass
class AnalyticsSerializationError(AnalyticsError): pass
