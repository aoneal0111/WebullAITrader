# Crypto worker controls and native display telemetry

The prior Crypto workspace was a read-only projection. Its disabled START/STOP
buttons did not control Docker workers. Equity emergency stop controls equity;
it does not stop independent crypto containers.

The Crypto dashboard now offers:

- START WALLET: starts the selected existing verified PAPER container, resumes
  entries, and retains the same database. It never creates a new wallet.
- STOP WALLET: pauses entries, stops that container gracefully, and verifies
  Docker reports it stopped. Command failures remain visible.
- STOP ALL CRYPTO: pauses and stops every discovered managed crypto PAPER worker,
  with a shorter Docker grace period. Each result is checked independently.

These stops retain holdings. They do not liquidate positions. Stopped workers
cannot manage exits. Equity controls remain separate. GUI commands run in a
background thread; duplicate clicks are disabled until completion. Selection
changes cannot redirect a command already in progress. Reads started before a
completed command are discarded rather than restoring stale runtime state.

Native workers publish a bounded atomic display snapshot after each cycle,
including native wallet balances and available stake, per-pair source signals,
indicator inputs, and held-position valuations using native fee-inclusive profit
calculations. The snapshot makes no market requests, wallet refreshes, strategy
changes, or order calls. Display failures do not change trading evidence health.

Observed native bid prices are used for at most 60 seconds. When unavailable,
ready analyzed-candle closes are explicitly labeled CANDLE CLOSE ESTIMATE.
Identical cached quotes do not get a new observation time. A candle estimate is
not a live executable quote. Portfolio equity is USD cash plus marked inventory;
unrealized P/L uses native fee-inclusive calculations. Missing or mismatched
position marks suppress aggregate valuation. Snapshots expire after 90 seconds
and must match the current recording. Stopped workers do not supply current values.

The upgrade prepares file-bind overlays over each container's existing read-only
source. Only display observation and cycle-publish hooks are added to those exact
runtime files, plus the telemetry helper. Unrecognized hooks are refused. Existing
strategy modules, configs, wallet mounts, image, entry command, and ledger are
preserved. Applying overlays stops and recreates workers individually, preserving
their previous running state and entry-pause markers. Keep the repair directory:
Docker bind mounts reference its generated display files. No automatic strategy tuning
or liquidation occurs. An apply failure is reported; preserve its output and
inspect worker states before proceeding.

Validation covers offline command targeting, mount identity, failure reporting,
worker-state preservation, telemetry staleness, selected-wallet GUI routing,
font/layout checks, and the existing reader/runtime regression suites. Actual
Docker connectivity on the operator's Windows workstation is verified at install
and command execution; it cannot be established by offline fixtures.
