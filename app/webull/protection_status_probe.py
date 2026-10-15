"""One bounded, read-only stock-profile observation; never a RESUME adapter."""
from datetime import datetime
import re
from threading import Lock

from app.webull.options_data_probe import Observation, OptionsDataProbe, SENSITIVE

SYMBOL = re.compile(r"[A-Z][A-Z0-9.\-]{0,14}\Z")
PERMISSION_CODES = {"OC": "TRADABLE_PERMISSION", "CO": "LIQUIDATE_ONLY_PERMISSION",
                    "NT": "NON_TRADABLE_PERMISSION"}
STATUS_CODES = {"1": "ACTIVE", "2": "INACTIVE", "3": "HALTED",
                "ACTIVE": "ACTIVE", "INACTIVE": "INACTIVE", "HALTED": "HALTED",
                **PERMISSION_CODES}
TIME_FIELDS = ("timestamp", "status_timestamp", "status_time", "update_time", "updated_at")


def event_protocol_observation(protocol=None):
    """Inspect the installed account-event protobuf, never construct its client.

    A matching schema would still not establish permissions, delivery or halts.
    Broker instrument events and individual order events use different schemas.
    """
    report = {"scope": "OFFLINE_ACCOUNT_EVENT_PROTOBUF_NOT_ENTITLEMENT_CHECK",
              "status_channel_access": "UNVERIFIED", "status_authorization": "NONE"}
    try:
        if protocol is None:
            from webull.trade.events import events_pb2 as protocol
        request = set(protocol.SubscribeRequest.DESCRIPTOR.fields_by_name)
        response = set(protocol.SubscribeResponse.DESCRIPTOR.fields_by_name)
        account = {"subscribeType", "timestamp", "contentType", "payload", "accounts"}
        broker_request = {"events", "timestamp"}
        broker_response = {"type", "id", "eventType", "position", "timestamp", "version", "payload"}
        report.update(state="ACCOUNT_SUBSCRIPTION_SCHEMA" if request == account else "OTHER_SCHEMA",
                      account_subscription_field="accounts" in request,
                      instrument_event_list_field="events" in request,
                      replay_cursor_field="position" in response,
                      broker_instrument_schema_match=(broker_request <= request and broker_response <= response))
    except (ImportError, AttributeError, TypeError):
        report["state"] = "PROTOBUF_SCHEMA_UNAVAILABLE"
    return report


def validate_symbols(symbols):
    if (not isinstance(symbols, tuple) or not 1 <= len(symbols) <= 5
            or any(not isinstance(s, str) or SYMBOL.fullmatch(s) is None for s in symbols)
            or len(set(symbols)) != len(symbols)):
        raise ValueError("One to five unique uppercase equity symbols required")
    return symbols


def _time_shape(value):
    """Retain bounded date/epoch syntax without asserting units or freshness."""
    if type(value) is int and 0 <= value <= 10**19:
        return {"type": "int", "value": value}
    if isinstance(value, str) and len(value) <= 40:
        if value.isascii() and value.isdigit() and 8 <= len(value) <= 19:
            return {"type": "str", "value": value}
        try:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
            return {"type": "str", "value": value}
        except ValueError:
            pass
    return {"type": type(value).__name__}


def inspect_profiles(payload, requested):
    """No arbitrary provider text, identifiers, error bodies or status guesses."""
    validate_symbols(requested)
    rows = payload if isinstance(payload, list) else payload.get("data") if isinstance(payload, dict) else None
    root_shape = "LIST" if isinstance(payload, list) else "DATA_LIST" if isinstance(rows, list) else "UNKNOWN"
    if not isinstance(rows, list):
        return {"root_shape": root_shape, "rows": [], "missing_symbols": list(requested),
                "status_authorization": "NONE"}, ()
    if len(rows) > 100:
        return {"root_shape": root_shape, "row_count": len(rows), "rows": [],
                "state": "TOO_MANY_ROWS", "status_authorization": "NONE"}, ()
    samples, found = [], set()
    unexpected = malformed = 0
    for row in rows:
        if not isinstance(row, dict):
            malformed += 1
            continue
        symbol = row.get("symbol")
        if not isinstance(symbol, str) or symbol not in requested:
            unexpected += 1
            continue
        found.add(symbol)
        if len(samples) >= 10:
            continue
        status = row.get("status")
        code = str(status) if type(status) in (int, str) else None
        recognized = STATUS_CODES.get(code)
        sample = {"symbol": symbol, "status_present": "status" in row,
                  "status_type": type(status).__name__,
                  "status_label": recognized or "UNKNOWN",
                  "status_domain": ("TRADING_PERMISSION" if code in PERMISSION_CODES
                                    else "INSTRUMENT_STATUS" if recognized is not None else "UNKNOWN"),
                  "fields": [k for k in list(row)[:64] if isinstance(k, str)
                             and re.fullmatch(r"[a-z][a-z0-9_]{0,39}", k) and not SENSITIVE.search(k)],
                  "time_fields": {k: _time_shape(row[k]) for k in TIME_FIELDS if k in row}}
        if recognized is not None:
            sample["status_code"] = status
        samples.append(sample)
    return {"root_shape": root_shape, "row_count": len(rows), "rows": samples,
            "missing_symbols": [s for s in requested if s not in found],
            "unexpected_rows": unexpected, "malformed_rows": malformed,
            "pagination_present": isinstance(payload, dict) and bool(payload.get("pagination_key")),
            "status_authorization": "NONE"}, tuple(sorted(found))


class ProtectionStatusProbe:
    """At most one profile SDK invocation per instance, including failures."""
    def __init__(self, factory, *, clock):
        self._lock, self._attempted, self._requested = Lock(), False, ()
        self._reader = OptionsDataProbe(factory, clock=clock,
            sampler=lambda value: inspect_profiles(value, self._requested))

    @property
    def calls(self):
        return self._reader.calls

    def profiles(self, symbols):
        validate_symbols(symbols)
        with self._lock:
            if self._attempted:
                return Observation("STOCK_PROFILE_STATUS", "PROBE_REQUEST_LIMIT", self._reader.clock(),
                                   requested_symbols=symbols)
            self._attempted, self._requested = True, symbols
            # Installed SDK 2.0.14 exposes get_instrument, not list_instruments.
            # Do not filter status: a halted/inactive requested symbol must not disappear.
            return self._reader._read("STOCK_PROFILE_STATUS", lambda sdk: sdk.instrument.get_instrument(
                symbols=list(symbols), category="US_STOCK", page_size=5), requested_symbols=symbols)
