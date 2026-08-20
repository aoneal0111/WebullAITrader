# Yahoo Finance catalyst provider

The Yahoo Finance integration is a headline/news evidence source only. Webull
remains authoritative for quotes, bars, bid/ask, volume, and every price used by
scanner or execution calculations. The Yahoo provider has no market-price
output surface.

Yahoo does not currently document a supported public, no-credential news API.
The implementation therefore reads the structured JSON returned by
`https://query1.finance.yahoo.com/v1/finance/search`; it does not download or
scrape rendered HTML. All endpoint-specific behavior is isolated behind
`YahooFinanceNewsTransport`, so the transport can be replaced without changing
classification, caching, or aggregation.

The provider is disabled by default. Enable it explicitly with
`YAHOO_FINANCE_NEWS_ENABLED=true`. The defaults are a 24-hour headline freshness
window, a five-second request timeout, a five-minute per-symbol LRU cache, a
512-symbol bound, and a one-minute provider outage cooldown.

Only fresh headlines carrying a direct Yahoo `relatedTickers` association pass
ticker matching. A conservative gate rejects market recaps, price-move recaps,
technical analysis, listicles, opinion pieces, previews, rumors, and vague
headlines. Accepted headlines are mapped to existing catalyst types; strong
specific events outside those mappings use `OTHER`. Malformed schemas produce
`UNKNOWN`, while HTTP/network/provider failures produce `UNAVAILABLE`.

Canonical event IDs use an SEC accession when one is present in a filing URL or
headline. Otherwise they use a shared normalized symbol, catalyst type,
publication date, and exact syndicated headline digest. This supports
deterministic cross-provider grouping when another structured news or
press-release provider publishes the same title, without risky fuzzy matching.
