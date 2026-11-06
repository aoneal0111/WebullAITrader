# Native NFI crypto PAPER trading

This prepares **NostalgiaForInfinity X8, unmodified at
bbddbd94f97f3c14336387d2958183870891b951**, in its native Freqtrade 2026.9 engine.
It is a separate spot-crypto simulated account running alongside Atlas equity.
It does not replace Warrior, Scalper, or their existing launcher/configuration.
The legacy Atlas Crypto page does not control or display this new engine.
Use the new NFI launch/status scripts and leave the legacy crypto trader off
when evaluating this campaign.

The user's historical reference was 32 wins from 36 trades (88.9%) in January
2024 on Binance USDT history, approximately +245.95 USDT on 10,000 USDT. That is
not a promised win rate. The new forward venue is **Kraken USD**, explicitly
supported by this pinned source. Prices, available pairs, fees and execution
will differ. Do not rename Binance candles as Kraken data or treat the previous
historical result as a Kraken backtest.

## Native behavior and account

- Separate **10,000 USD simulated wallet**, source's six simultaneous positions
  and dynamically allocated `unlimited` stake. "Unlimited" delegates stake
  allocation to the native strategy/wallet; it does not create unlimited cash.
- Native entries, custom stake sizing, position adjustments, partial exits and
  final exits. No replacement cent-scalp or breakout signal, forced buy, Grok
  approval, fixed 10-share override, 20-call budget or eight-hour expiry.
- **NFI can add to losing positions and sets its emergency stop at −99%.** This
  is a material feature of the source, not tight-stop Warrior behavior. Kept
  unchanged for PAPER observation. This integration cannot place real orders.
- The same launcher reuses its SQLite wallet/positions/orders. Preparing a new
  directory starts an independent campaign; do not use that to reset losses.
- Source/strategy/license/config hashes are checked. Only publicly active USD
  spot pairs inside the pinned author's Kraken universe are eligible. The first
  public preflight freezes up to 40 by observed quote volume. Restarts retain
  that selection. The source blacklist is preserved.
- No exchange keys, user account connection, paid Grok calls, proxy override,
  regional access workaround, external Telegram/API server or exposed port.
  Ambient FREQTRADE configuration variables are cleared and dry-run is
  explicitly required in the configuration and native CLI. Underlying CCXT
  real-order/cancel/transfer methods are also disabled for this engine instance.

[Freqtrade dry-run documentation](https://www.freqtrade.io/en/stable/configuration/#using-dry-run-mode)
explains the native simulation. See the author's
[configuration recommendations](https://iterativv.github.io/NostalgiaForInfinity/)
for its source sizing and five-minute timeframe. The recipe uses the author's
actual Kraken USD list even though that source file's name contains `usdt`.

## Preparation and manual operation

Run `tools/prepare-nfi-paper-trading.ps1` with the exact reviewed revision.
It creates a detached source checkout and a fresh AtlasResearch NFI directory,
fetches and hashes the original NFI source, runs the integration tests in the
pinned official Freqtrade container, and validates the native configuration
with network disabled. No forward bot is started by preparation, and no
installed Atlas checkout, `.env`, equity ledger or trial is modified.

The printed directory contains:

| Script | Action |
| --- | --- |
| `Start-NFI-Crypto-Paper.ps1` | Public market preflight, then native paper trading. Keep its terminal open. |
| `Show-NFI-Crypto-Status.ps1` | Read-only native trade counts, closed win rate, closed lifecycle P&L, open holdings and capture/engine status. |
| `Pause-NFI-New-Entries.ps1` | Blocks new positions and additions; native exits continue. |
| `Resume-NFI-New-Entries.ps1` | Clears the operator entry pause; source/data/capture checks still apply. |
| `Stop-NFI-Crypto-Paper.ps1` | Stops only this NFI container. Restart the same launcher to resume management of open simulated holdings. |
| `Export-NFI-Paper-Review.ps1` | Consistent read-only native ledger backup and bounded log/status ZIP, kept locally. |

Initial native warmup fetches five-minute and informative history for the
selected pairs, so it can take several minutes. The terminal reports native
progress and subsequent heartbeat. Runtime status reports actual fresh-ready
pairs rather than calling a merely launched process trade-ready. Entries begin
only after capture has completed a cycle and the candidate's native primary
warmup/current completed candles are present. Informative history retains the
source's own indicator sufficiency rules; no extra two-year age gate is added.
Missing/stale/future data is never relabeled as current. A trading signal is
still required: preparation and data readiness never force a position.

The engine has no scheduled eight-hour stop. It can continue while the terminal,
Docker, market connection and recording remain healthy. A recording has a
bounded 4 GiB / eight-million-record capacity. Reaching that capacity or losing
capture blocks entries/additions and leaves native exits active. It is not an
unlimited-storage service. Pause entries instead of stopping the entire engine
when open simulated positions should continue to be managed.

## Evidence and checks

Each run creates a separate append-only, hash-chained recording directory.
It captures raw CCXT OHLCV replies before normalization, native OHLCV
windows/deltas, completed-candle signal flags,
entry admission, and observed public ticker/order-book responses used by the
native engine. It makes no additional live requests for capture. Unchanged
provider responses are coalesced. Raw provider timestamps are preserved, including
missing timestamps; local receipt time is recorded separately. Native Freqtrade
may normalize missing intervals into zero-volume candles. Normalized windows
are explicitly labeled and remain separate from the raw provider replies. This is not a
claim to have verified exchange quote timestamps.

Native SQLite remains authoritative for orders, fills, adjustments, fees and
closed lifecycle profit. Status separates closed trades from open holdings;
open partial realized profit is reported separately when the native schema
provides it. No marked open position is counted as a winning closed trade.
Raw journals remain local; the review ZIP contains a consistent SQLite backup,
latest status/config provenance and up to 16 MiB of native log tail, not a full
raw journal-chain audit.

Server checks in this session:

- Exact source/hash checks and Freqtrade 2026.9 native strategy/schema loading.
- Current public Kraken metadata/volume selection and ten representative OHLCV
  checks: BTC/USD and ETH/USD on 5m/15m/1h/4h/1d, 719 completed rows each.
  The server used its standard environment egress for this read-only probe;
  the generated client uses its normal Docker network and must pass again there.
- A network-disabled January 2024 native smoke replay on eight pinned Binance
  history pairs: three naturally closed trades, +111.14714524 simulated USDT.
  This is integration evidence, **not** a new reliable 100% win rate or a
  reproduction of the earlier 36-trade universe. Post-period recorded market
  metadata limits that historical replay just as in the earlier research.
- Tests for source/config tampering, credential/real-mode refusal, current
  warmup, entry capture and pause/addition behavior, continued exits, disabled
  real exchange writes, no extra capture requests, duplicate-launch lock,
  read-only status, and persistent native data handling.

Server testing used the native Linux Python 3.13.17 package runtime. Docker is
not available on the server, so the exact official container and Windows
launcher are checked by the preparation script on the operator's workstation
before manual startup. No forward bot or Atlas GUI was started on the server.
The validation summary is in [nfi-forward-validation.json](nfi-forward-validation.json).
