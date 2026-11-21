# Separate consolidated history capture

Research reviewed October 28, 2026. Atlas's original `knowledge/acquisition.py` hard-codes ALPACA/IEX and labels coverage SINGLE_EXCHANGE_FREE_RESEARCH. Its CLI rejects SIP. This explains the collector's limited exchange scope; the actual local manifests remain the authority for a specific historical run.

Alpaca official FAQ states historical SIP can be queried without a subscription when end is at least 15 minutes old: https://docs.alpaca.markets/us/docs/market-data-faq . Historical endpoint pagination and feed parameters: https://docs.alpaca.markets/us/reference/stockbars . The operator previously verified historical SIP access. This does not promise current account entitlement, realtime SIP, or uninterrupted 390-row sessions.

`tools/capture-selected-sip-history.ps1` uses the corrected study's selected index to capture exactly its up-to-600 symbol-days at 1 minute, raw adjustment, `asof=-`, 04:00 through 20:00 Eastern. Up to two pages per partition, 1,200 total attempts, 4 MB per response, 1 GB total responses, 3 GB initial free-space requirement, 30-second request timeout, no retry, no feed fallback. HTTP errors stop the capture. Partial data, page hashes, and sanitized diagnostics persist in a new private directory. Existing `.env`/environment credentials are read locally and are never printed. No credentials are entered or changed by Codex.

No existing file is repaired or overwritten. The saved bars are validated using the existing finite OHLCV/ordered timestamp parser. Missing minutes are preserved. COMPLETE means requests completed, not every minute traded. After download, compare coverage and regenerate signals/features from the SIP history before testing: IEX episode prefixes should not be forced to match different SIP OHLCV. Reusing old labels with replacement prices is not an independently regenerated strategy test.

Other primary sources reviewed:

- https://hfdatalibrary.com/ : IEX-only; gaps preserved, site terms say academic research only, not trading. Not selected as consolidated replacement.
- https://huggingface.co/datasets/mito0o852/OHLCV-1m : file listing inspected through March 2026, before our June-August window. Not selected.
- https://github.com/getdata-finance/msft-1m-ohlcv-stocks-historical-data : evaluation CSV March-September 2026 but README defines volume as tick count, not traded shares. Not suitable for our share-volume liquidity cap; not imported.
- https://github.com/erikmattheis/equity-minute-data : narrow public ticker set; no verified matching 600-partition coverage. Not imported.

Do not claim third-party data verified merely because it is public, mix volume units, purchase a subscription automatically, or activate a strategy through this downloader. Private downloaded history and performance stay outside GitHub.
