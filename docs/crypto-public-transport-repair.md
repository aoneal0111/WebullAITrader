# Crypto public-data transport repair

The Nov 9 15:43 CST operator capture contains repeated Kraken OHLC
`RequestTimeout` exceptions in BB/NFI/E0, while Docker CPU and memory usage are
low. A container heartbeat was still printed during the failures. This confirms
failed data delivery in that interval; it does not establish a Kraken-wide outage,
rate-limit response, or the cause of every earlier absent BUY signal.

The repair keeps the existing native strategies, exit logic, fees, universes,
wallets, operator pause files and entry freshness requirements. It changes only
the runtime's public-data transport and code mount in seven existing crypto
workers, leaving the desktop and equity services alone.

* Identical unsigned public Kraken OHLC GET requests share a response across
  processes through a common directory. Pair, interval and `since` must match;
  private endpoints, execution quote requests and requests with headers/body
  bypass the response cache. Provider timestamps are not rewritten.
* Responses expire after 15 seconds or at the next candle boundary, whichever
  comes first. Requests spanning a boundary are not cached. Provider error
  responses and exceptions are not cached. Atomic writes, bounded files, expired
  response cleanup and 256 striped locks bound storage and avoid partial reads.
* Two asynchronous HTTP operations per process enter the existing shared
  1.1-second pacing gate. This avoids putting an entire pair/timeframe batch into
  the thread pool and socket layer concurrently; the global pacing is retained.
* Three network failures in a minute trigger a shared 15-second OHLC cooldown.
  Cached valid responses may still be used; execution quote requests are not
  blocked by this circuit. The next successful OHLC response clears it.
* Pinned Freqtrade 2026.9 candle refreshes receive one retry, two attempts total,
  rather than the default four retries. Order/exit retry methods are unchanged.
* A separate `transport-health.json` heartbeat reports actual requests, cache
  hits, network failures, local cooldown skips and time spent waiting. It does
  not update engine/candle timestamps or claim that data is fresh. Entry readiness
  still depends on the original native provider-time checks.

The installer prepares a new read-only source checkout and runs disconnected
tests in the existing pinned image before stopping any workers. It validates
both proposed Compose files, records which crypto containers were running, then
stops only those seven crypto services. It verifies sealed source/config hashes,
backs up each existing SQLite ledger through the backup API, and updates only the
crypto `/check` and shared public-data mounts. A failed multi-file replacement
restores both original Compose files. Originally running workers are recreated;
previously stopped workers remain stopped. Original pause flags remain effective.

The repaired Compose files keep the same work directories and container names;
existing launchers therefore keep using the repair. Backup Compose files and
ledger snapshots remain under the printed repair directory. The new status
launcher reports engine timestamp age separately from transport heartbeat and
fresh-ready pairs. Recreated workers start new capture sessions in the same work
directories; earlier capture directories remain available.

Cloud validation: 40 tests pass, including real CCXT OHLC parsing against recorded
responses, provider timestamp preservation, duplicate coalescing, timeout recovery,
cancellation lock release, TTL/boundary expiration, cooldown behavior, native
candle retry count, wallet isolation, ledger backups and transactional rollback.
No cloud market/broker request was made by these tests. Windows PowerShell and
Docker execution require local validation; the installer includes the disconnected
checks and Compose validation. Tests establish behavior under simulated failures,
not restored connectivity to the operator's actual Kraken route.

After application, use the printed `Show-Crypto-Transport.ps1`. Success requires
advancing engine timestamps and nonzero fresh-ready pairs; `container_running`
or a transport heartbeat alone is insufficient. A continuing `REQUESTS_FAILING`
state requires network/provider investigation. No cache or retry policy can
manufacture valid market data or a profitable entry signal.
