# Atlas scanner symbol flow

Atlas treats Webull display symbols and OpenAPI request symbols as distinct
fields even when Webull currently returns the same text for both.

1. The official `gainers-losers` and `top-active` screeners return
   `instrument_id`, `symbol`, `exchange_code`, currency, name, and ranking
   facts. They do not return a security-type or an authoritative bars-support
   flag.
2. `WebullScannerUniverseProvider` retains the complete raw screener row and
   enriches its immutable `UniverseSymbol` from the official stock instrument
   lookup. The lookup supplies `symbol`, `instrument_id`, `exchange_code`,
   `category`, `status`, and trading facts such as `fractionable`, `marginable`,
   `shortable`, and `easy_to_borrow`. No region or market suffix is returned.
3. `UniverseSymbol.display_symbol` is the operator-facing ticker.
   `UniverseSymbol.api_symbol` is the Webull `symbol` used by stock bars and
   streaming. `instrument_id`, category, exchange, status, asset class,
   security type, tradability, region, and source remain attached through
   universe filtering.
4. Universe filters act on the canonical instrument without changing its
   identity.
5. Reference warmup passes the canonical instrument to
   `WebullScannerReferenceProvider`. Support is checked against stock bars with
   `api_symbol` and category in the selected environment. Support results are
   TTL-cached by environment, category, and API symbol.
6. Only a supported instrument receives the 30-day bars request and produces a
   `ReferenceRecord`. Unsupported, temporary, and missing-data outcomes remain
   separate in `ReferenceWarmupResult`.
7. `RealtimeScannerEngine.active_symbols` contains only successfully warmed
   display symbols. The live coordinator subscribes only that set. The official
   streaming mapper sends those canonical Webull symbols with `US_STOCK`.

Installed SDK 2.0.14 defines stock bars as
`get_history_bar(symbol, category, timespan, ...)` and copies `symbol` directly
to the request. It documents a plain security code such as `AAPL`; stock bars
do not accept `instrument_id`. Atlas therefore does not manufacture exchange
suffixes or prefixes.

The sandbox screener and instrument catalog can return a valid listing that
the sandbox stock-bars service rejects with `UNSUPPORTED_SYMBOL`. This is an
environment coverage mismatch, not by itself an identifier-format error.
Atlas quarantines that candidate for the environment cache period, continues
warming the remaining universe, and emits one `symbol_rejected` record.
