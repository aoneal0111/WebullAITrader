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

The upgrade copies each worker's existing `app/strategy_lab` directory and adds
only the two display hooks and telemetry helper to that copy. It mounts the
whole directory at the existing `/check/app/strategy_lab` directory, read-only.
It does not create individual file mountpoints inside the read-only parent.
All other copied module bytes are checked for preservation. Strategy modules in
`/work`, configurations, wallet mounts, image, entry command and ledger remain
unchanged. Existing nested module overrides are incorporated into the copy.

The generated Compose override uses `volumes: !override` to remove the previous
individual file mounts instead of merging them back in. Docker Compose 2.24.4
or later is required; actual Compose rendering is checked before any worker is
stopped. See [Docker's merge reference](https://docs.docker.com/reference/compose-file/merge/).
Rendering must preserve everything outside the module-directory mount, including
wallet and shared-data mounts. Bundle file hashes are checked again at apply.

With `-RecoverPlan`, the installer first tries to recover a previously running,
currently stopped worker using the saved plan's original Compose files. It then
prepares the new directory mounts and restores the plan's previous running
states. Configuration hashes and container-to-wallet identities must match.
A failed display-update start triggers an attempt to restore the original
Compose configuration and running state, reports the result, and still fails
the update visibly. No positions are liquidated. Retain both the saved recovery
plan and repair directories because Docker bind mounts reference their files.

Validation covers offline command targeting, mount identity, failure reporting,
worker-state preservation, telemetry staleness, selected-wallet GUI routing,
font/layout checks, and the existing reader/runtime regression suites. Actual
Docker connectivity on the operator's Windows workstation is verified at install
and command execution; it cannot be established by offline fixtures.

## Windows installer dependency isolation

The preflight unit tests inject an exchange factory and block network sockets
and the `ccxt` import. They exercise the full preflight checks without needing
CCXT in the GUI environment. The production preflight still imports and uses
CCXT by default inside the native crypto runtime. A separate fake-module test
covers that default factory path. No dependency installation or test skipping
is needed to address the Windows `ModuleNotFoundError: ccxt` installer failure.


## Status-file permission contention

The NASOS, CRSI and ROC traceback exports confirmed the same failure in the
native worker loop: replacing `/work/engine-status.json.tmp` with
`/work/engine-status.json` raised `PermissionError` and terminated the worker.
The logs identify the failed rename; they do not identify which process or host
component held the conflicting file access.

Status publication now uses a unique temporary file and up to five short
permission-error retry delays (0.385 seconds total). It never falls back to
truncating the visible JSON. Exhausted I/O failures preserve the last complete
status and timestamp, log the failure, and set a reporting error in Evidence.
The existing Evidence entry gate then pauses new entries while native exit
cycles continue. A successful publication clears only this reporting error;
other capture failures remain in force. Native trade operations are not retried
or altered by the helper. Atomic rename recovery does not establish profitability.

The directory overlay also copies this status helper and patches the exact
existing native status-publication sites. Runtime thresholds, wallet configs,
image, exchange source and ledgers are retained. Applying without a recovery
plan preserves current stopped states; use the Crypto START WALLET control to
resume selected wallets explicitly after installation.
