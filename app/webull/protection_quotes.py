"""Strict SDK quote normalization; supplies no halt or trading authority."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import re

from app.market_data.models import BookLevel, MarketEvent, MarketEventType, QuotePayload
from app.market_data.validation import validate_event

SOURCE = "webull-protection-quote"
SYMBOL = re.compile(r"[A-Z][A-Z0-9.\-]{0,14}\Z")


class ProtectionQuoteError(ValueError):
    """Fixed diagnostic category only; never provider values or messages."""


def validate_symbols(symbols, maximum=100):
    if (not isinstance(symbols, tuple) or not 1 <= len(symbols) <= maximum
            or any(not isinstance(s, str) or SYMBOL.fullmatch(s) is None for s in symbols)
            or len(set(symbols)) != len(symbols)):
        raise ValueError("Bounded distinct uppercase equity symbols required")


def _number(value, *, positive):
    if (isinstance(value, bool) or not isinstance(value, (str, Decimal, int))
            or not 0 < len(str(value)) <= 64):
        raise ProtectionQuoteError("INVALID_BOOK_NUMBER")
    try:
        result = Decimal(value)
    except (InvalidOperation, ValueError):
        raise ProtectionQuoteError("INVALID_BOOK_NUMBER") from None
    if (not result.is_finite() or not 0 <= result <= Decimal('1e12')
            or not -18 <= result.as_tuple().exponent <= 12 or positive and result == 0):
        raise ProtectionQuoteError("INVALID_BOOK_NUMBER")
    return result


class ProtectionQuoteDecoder:
    """Decode one registered backend generation's already-decoded SDK objects.

    No raw/compressed bytes, timestamp fallback, synthesized size or RESUME.
    Generation identity and source labels are routing checks, not authentication.
    """
    def __init__(self, symbols, generation, *, clock=lambda: datetime.now(timezone.utc)):
        validate_symbols(symbols)
        if type(generation) is not int or generation <= 0:
            raise ValueError("Positive backend generation required")
        self._symbols, self._generation, self._clock = frozenset(symbols), generation, clock
        self._sequence = 0

    def decode(self, envelope):
        topic = getattr(envelope, "topic", None)
        if isinstance(topic, bytes):
            try:
                topic = topic.decode("ascii")
            except UnicodeDecodeError:
                return None
        if not isinstance(topic, str) or topic.strip().lower().rsplit("/", 1)[-1] != "quote":
            return None  # Notices, heartbeats, snapshots and trades are not quotes/status.
        generation = getattr(envelope, "generation", None)
        if type(generation) is not int or generation != self._generation:
            raise ProtectionQuoteError("WRONG_GENERATION")
        now, received = self._clock(), getattr(envelope, "received_timestamp", None)
        for at in (now, received):
            if not isinstance(at, datetime) or at.tzinfo is None or at.utcoffset() is None:
                raise ProtectionQuoteError("MISSING_AWARE_RECEIPT_TIME")
        value = getattr(envelope, "payload", None)
        basic = getattr(value, "basic", None)
        symbol = getattr(basic, "symbol", None)
        if not isinstance(symbol, str) or symbol not in self._symbols:
            raise ProtectionQuoteError("UNREQUESTED_SYMBOL")
        milliseconds = getattr(basic, "timestamp", None)
        if type(milliseconds) is not int or not 0 <= milliseconds <= 32503680000000:
            raise ProtectionQuoteError("INVALID_PROVIDER_TIME")
        at = datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(milliseconds=milliseconds)
        if not 0 <= (now - at).total_seconds() <= 5 or not 0 <= (now - received).total_seconds() <= 5:
            raise ProtectionQuoteError("STALE_OR_FUTURE_QUOTE")
        if at > received:
            raise ProtectionQuoteError("PROVIDER_TIME_AFTER_RECEIPT")
        sides = []
        for name in ("bids", "asks"):
            levels = getattr(value, name, None)
            if not isinstance(levels, (list, tuple)) or not 1 <= len(levels) <= 10:
                raise ProtectionQuoteError("MISSING_OR_EXCESSIVE_BOOK")
            sides.append(tuple(BookLevel(_number(getattr(level, "price", None), positive=True),
                                         _number(getattr(level, "size", None), positive=False))
                               for level in levels))
        bids, asks = sides
        quote = QuotePayload(bids[0].price, asks[0].price, bids[0].size, asks[0].size, bids, asks)
        event = MarketEvent(self._sequence + 1, at, symbol, SOURCE, MarketEventType.QUOTE, quote,
                            received_timestamp=received, dequeued_timestamp=now)
        try:
            validate_event(event)
        except ValueError:
            raise ProtectionQuoteError("INVALID_BOOK_GEOMETRY") from None
        self._sequence += 1
        return event
