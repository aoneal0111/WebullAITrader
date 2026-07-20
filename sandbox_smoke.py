from __future__ import annotations

import os
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from app.operations.credentials import EnvironmentCredentialProvider
from app.webull.configuration import RetryPolicy
from app.webull.http_client import UrllibHttpBackend, WebullHttpClient
from app.webull.logging import StructuredLogger
from app.webull.rate_limits import DeterministicRateLimiter, RateLimit
from app.webull.signing import WebullRequestSigner


class ConsoleSink:
    def emit(self, record):
        print(
            f"[{record.get('operation')}] "
            f"{record.get('status')} "
            f"path={record.get('path', '')}"
        )


credentials = EnvironmentCredentialProvider(os.environ)
endpoint = os.environ["WEBULL_API_BASE_URL"].rstrip("/")
configured_account = credentials.get_account_id()

signer = WebullRequestSigner(
    credentials=credentials,
    host=endpoint,
    clock=lambda: datetime.now(timezone.utc),
    nonce_provider=lambda: uuid.uuid4().hex,
)

limiter = DeterministicRateLimiter(
    RateLimit(
        requests=10,
        window_seconds=Decimal("60"),
    ),
    clock=lambda: Decimal(str(time.monotonic())),
    sleeper=lambda seconds: time.sleep(float(seconds)),
)

client = WebullHttpClient(
    endpoint=endpoint,
    timeout=Decimal("10"),
    retry_policy=RetryPolicy(),
    backend=UrllibHttpBackend(),
    auth=signer,
    limiter=limiter,
    sleeper=lambda seconds: time.sleep(float(seconds)),
    logger=StructuredLogger(ConsoleSink()),
)

print("Endpoint:", endpoint)
print("Requesting sandbox account list...")

response = client.get("/openapi/account/list")

if isinstance(response, list):
    accounts = response
elif isinstance(response, dict):
    accounts = next(
        (
            response[key]
            for key in ("data", "items", "accounts", "result")
            if isinstance(response.get(key), list)
        ),
        [],
    )
else:
    accounts = []

print("Configured account:", configured_account)
print("Returned accounts:")

for account in accounts:
    print(
        " -",
        account.get("account_id"),
        "type=" + str(
            account.get(
                "account_type",
                account.get("account_class", "UNKNOWN"),
            )
        ),
    )

matching = [
    account
    for account in accounts
    if str(account.get("account_id", "")).strip()
    == configured_account.strip()
]

print("Accounts returned:", len(accounts))
print("Configured account found:", bool(matching))
print("Sandbox authentication succeeded.")
def connect(self):
    try:
        values = _items(self.http.get("/openapi/account/list"))

        account_found = any(
            str(item.get("account_id", "")).strip()
            == self.configuration.account_id.strip()
            for item in values
        )

        if not account_found:
            raise ValueError("configured account not found")

        self.health = update_health(
            self.health,
            connected=True,
            authenticated=True,
        )
        self.logger.log("connect", "succeeded")

    except Exception as exc:
        self.logger.log("connect", "failed")
        raise map_error(exc)