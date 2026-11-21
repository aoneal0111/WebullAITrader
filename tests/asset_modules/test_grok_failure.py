from urllib.error import HTTPError, URLError
from app.asset_modules.grok_failure import failure_diagnostic


def test_allowlist_never_exports_exception_secrets():
    secret='Bearer fixture-secret private provider body'
    cases=[(HTTPError('https://example.invalid',401,secret,{},None),'HTTP_ERROR'),
           (TimeoutError(secret),'TIMEOUT'), (URLError(TimeoutError(secret)),'TIMEOUT'),
           (URLError(secret),'NETWORK_ERROR'),(ValueError(secret),'INVALID_RESPONSE_OR_CONFIGURATION'),
           (RuntimeError(secret),'INTERNAL_ERROR')]
    for exc,expected in cases:
        result=failure_diagnostic(exc)
        assert result['failure_category']==expected
        assert secret not in str(result)
    assert failure_diagnostic(cases[0][0])['http_status']==401
