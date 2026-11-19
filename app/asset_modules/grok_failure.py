"""Allowlisted provider failure categories; never retain exception text or bodies."""
from urllib.error import HTTPError, URLError
from .grok import GrokUnavailable


def failure_diagnostic(exc):
    if isinstance(exc, HTTPError):
        return {'failure_category': 'HTTP_ERROR', 'http_status': int(exc.code)}
    if isinstance(exc, TimeoutError) or (isinstance(exc, URLError) and isinstance(exc.reason, TimeoutError)):
        return {'failure_category': 'TIMEOUT'}
    if isinstance(exc, URLError):
        return {'failure_category': 'NETWORK_ERROR'}
    if isinstance(exc, GrokUnavailable):
        if exc.failure_category in {'RATE_LIMITED','PAYMENT_REQUIRED'}:
            return {'failure_category':exc.failure_category,'http_status':exc.http_status,'retry_after_seconds':exc.retry_after_seconds}
        return {'failure_category': 'PROVIDER_UNAVAILABLE'}
    if isinstance(exc, (ValueError, TypeError, KeyError)):
        return {'failure_category': 'INVALID_RESPONSE_OR_CONFIGURATION'}
    return {'failure_category': 'INTERNAL_ERROR'}
