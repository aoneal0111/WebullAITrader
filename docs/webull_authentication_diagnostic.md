# Webull authentication body diagnostic

This diagnostic sends only signed `GET /openapi/config` requests through the
official SDK `ApiClient`. It does not construct a `TradeClient`, initialize a
trading session, or expose any order operation. Atlas production does not
import or enable this package.

Compare the original diagnostic identity selection with Atlas's market-data
factory before `DataClient` initialization:

```powershell
.\.venv\Scripts\python.exe -m app.webull_auth_diagnostic.compare_runtime
```

The comparison emits only lengths, salted fingerprints, endpoint metadata,
constructor settings, logger state, user-agent metadata, endpoint registration,
body/timestamp classifications, environment-variable provenance, and cache
state. It never emits credential values or signed headers.

Run the documented ISO 8601 UTC matrix from the repository root:

```powershell
.\.venv\Scripts\python.exe -m app.webull_auth_diagnostic.matrix
```

The runner executes, in order:

1. Python 3.13 with the SDK default body
2. Python 3.13 with an explicit empty-string body
3. Python 3.11 with the SDK default body
4. Python 3.11 with an explicit empty-string body

Set `ATLAS_DIAGNOSTIC_PYTHON313` or `ATLAS_DIAGNOSTIC_PYTHON311` to override
interpreter discovery. Each interpreter must have
`webull-openapi-python-sdk==2.0.14` and `python-dotenv` installed.

An integer epoch-millisecond experiment is available only as an explicit
diagnostic option:

```powershell
.\.venv\Scripts\python.exe -m app.webull_auth_diagnostic.matrix --timestamp epoch-milliseconds
```

Epoch milliseconds are contrary to Webull's currently published requirement
for an ISO 8601 UTC `x-timestamp`. This option must not be copied into or
enabled by the Atlas production runtime.

Every result contains only Python version, SDK version, timestamp format
classification, body type and length, HTTP status, sanitized error code, and
request ID. SDK logging is disabled for the duration of each request so that
credentials, signatures, nonces, and authorization headers cannot be emitted.

The matrix now deliberately selects `WEBULL_MARKET_DATA_*`, matching Atlas.
The earlier implementation preferred `WEBULL_TRADING_*`; an HTTP 200 from that
older selector therefore validated the trading/sandbox identity rather than
the production market-data identity.
