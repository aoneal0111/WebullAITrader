from dataclasses import dataclass
from decimal import Decimal

from app.committee.models import JSONValue, freeze_json_mapping, thaw_json_value


@dataclass(frozen=True, slots=True)
class HTTPXRequestArguments:
    method: str
    url: str
    headers: tuple[tuple[str, str], ...]
    query_parameters: tuple[tuple[str, str], ...]
    body: JSONValue
    has_body: bool
    timeout_seconds: Decimal
    follow_redirects: bool

    def __post_init__(self):
        if not isinstance(self.method, str) or not self.method:
            raise ValueError("method must be non-empty")
        if not isinstance(self.url, str) or not self.url:
            raise ValueError("url must be non-empty")
        if not isinstance(self.headers, tuple) or not isinstance(self.query_parameters, tuple):
            raise ValueError("headers and query parameters must be tuples")
        object.__setattr__(self, "body", freeze_json_mapping("body", {"value": self.body})["value"])
        if not isinstance(self.has_body, bool) or not isinstance(self.follow_redirects, bool):
            raise ValueError("request flags must be boolean")
        if not isinstance(self.timeout_seconds, Decimal) or not self.timeout_seconds.is_finite() or self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be a finite positive Decimal")

    def body_value(self):
        return thaw_json_value(self.body)
