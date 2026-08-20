from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime

from app.catalysts.models import CatalystEvidence
from app.momentum_scanner.models import CatalystStatus, CatalystType


class WebullCatalystProvider:
    """Read earnings-calendar and SEC-filing evidence from Webull."""

    name = "WEBULL_EARNINGS_SEC"

    def __init__(self, client: object) -> None:
        self._client = client

    def get_evidence(
        self,
        symbol: str,
        as_of: datetime | None = None,
    ) -> CatalystEvidence:
        normalized = symbol.strip().upper()
        if not normalized:
            raise ValueError("symbol is required")
        now = as_of if as_of is not None else datetime.now(UTC)
        client = (
            self._client.get()
            if callable(getattr(self._client, "get", None))
            else self._client
        )
        fundamentals = getattr(client, "fundamentals")
        unrecognized_evidence = False

        try:
            earnings, earnings_schema_supported = _catalyst_response_rows(
                fundamentals.get_earnings_calendar(normalized, "US_STOCK"),
                containers=("data", "items"),
            )
            recent = _recent_row(earnings, now, days=2)
            if recent is not None:
                return CatalystEvidence(
                    symbol=normalized,
                    catalyst_type=CatalystType.EARNINGS,
                    status=CatalystStatus.TRUE,
                    headline=_headline(recent, "Earnings"),
                    source="WEBULL_EARNINGS",
                    published_at=_row_timestamp(recent),
                    source_url=_row_text(
                        recent,
                        "source_url",
                        "url",
                        "link",
                    ),
                    provider_event_id=_row_text(
                        recent,
                        "event_id",
                        "earnings_id",
                        "id",
                    ),
                )
            unrecognized_evidence = (
                not earnings_schema_supported
                or any(_row_date(row) is None for row in earnings)
            )

            filings, filings_schema_supported = _catalyst_response_rows(
                fundamentals.get_sec_filings(normalized, "US_STOCK"),
                containers=("data", "items", "filings"),
            )
            recent = _recent_row(filings, now, days=3)
            if recent is not None:
                return CatalystEvidence(
                    symbol=normalized,
                    catalyst_type=CatalystType.SEC_FILING,
                    status=CatalystStatus.TRUE,
                    headline=_headline(recent, "SEC filing"),
                    source="WEBULL_SEC_FILINGS",
                    published_at=_row_timestamp(recent),
                    source_url=_row_text(
                        recent,
                        "source_url",
                        "filing_url",
                        "url",
                        "link",
                    ),
                    provider_event_id=_row_text(
                        recent,
                        "accession_number",
                        "filing_id",
                        "event_id",
                        "id",
                    ),
                )
            unrecognized_evidence = unrecognized_evidence or (
                not filings_schema_supported
                or any(_row_date(row) is None for row in filings)
            )
        except Exception:
            return CatalystEvidence(
                symbol=normalized,
                catalyst_type=CatalystType.NONE,
                status=CatalystStatus.UNAVAILABLE,
                source=self.name,
            )

        return CatalystEvidence(
            symbol=normalized,
            catalyst_type=CatalystType.NONE,
            status=(
                CatalystStatus.UNKNOWN
                if unrecognized_evidence
                else CatalystStatus.FALSE
            ),
            source=self.name,
        )


def _catalyst_response_rows(
    response: object,
    *,
    containers: tuple[str, ...],
) -> tuple[tuple[Mapping[str, object], ...], bool]:
    """Return catalyst rows and whether the reachable schema is understood."""

    value = _response_value(response)
    if isinstance(value, Mapping):
        selected = next((key for key in containers if key in value), None)
        if selected is None:
            return (), False
        value = value[selected]
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return (), False
    rows = tuple(row for row in value if isinstance(row, Mapping))
    return rows, len(rows) == len(value)


def _response_value(response: object) -> object:
    status = getattr(response, "status_code", 200)
    if status in (401, 403):
        raise PermissionError("Webull catalyst data is unavailable")
    if isinstance(status, int) and status >= 400:
        raise RuntimeError(f"Webull catalyst request failed: HTTP {status}")
    return response.json() if callable(getattr(response, "json", None)) else response


def _recent_row(
    rows: Sequence[Mapping[str, object]],
    now: datetime,
    *,
    days: int,
) -> Mapping[str, object] | None:
    for row in reversed(rows):
        parsed = _row_date(row)
        if parsed is not None and abs((parsed - now.date()).days) <= days:
            return row
    return None


def _row_date(row: Mapping[str, object]) -> date | None:
    for key in (
        "expected_publish_date",
        "report_date",
        "earnings_date",
        "publish_date",
        "filing_date",
        "filed_date",
        "accepted_time",
        "date",
    ):
        parsed = _date(row.get(key))
        if parsed is not None:
            return parsed
    return None


def _row_timestamp(row: Mapping[str, object]) -> datetime | None:
    for key in (
        "expected_publish_date",
        "report_date",
        "earnings_date",
        "publish_date",
        "filing_date",
        "filed_date",
        "accepted_time",
        "date",
    ):
        parsed = _timestamp(row.get(key))
        if parsed is not None:
            return parsed
    return None


def _timestamp(value: object) -> datetime | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        if len(text) == 10:
            return datetime.combine(date.fromisoformat(text), datetime.min.time(), UTC)
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)
    except ValueError:
        try:
            parsed_date = date.fromisoformat(text[:10])
            return datetime.combine(parsed_date, datetime.min.time(), UTC)
        except ValueError:
            return None


def _date(value: object) -> date | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        if len(text) == 10:
            return date.fromisoformat(text)
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC).date()
    except ValueError:
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            return None


def _headline(row: Mapping[str, object], fallback: str) -> str:
    for key in ("headline", "title", "form_type", "event_type"):
        value = str(row.get(key, "")).strip()
        if value:
            return value
    return fallback


def _row_text(row: Mapping[str, object], *keys: str) -> str | None:
    for key in keys:
        value = row.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


__all__ = ["WebullCatalystProvider"]
