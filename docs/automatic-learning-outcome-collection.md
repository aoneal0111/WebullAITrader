# Automatic observational outcome collection

Atlas can optionally collect saved PAPER outcomes into the learning journal
without repeated manual imports. This is local evidence collection, not model
training, strategy selection, execution authority or paid Grok review.

Configuration (default disabled):

- ATLAS_LEARNING_OUTCOME_COLLECTION_ENABLED=true
- ATLAS_LEARNING_OUTCOME_JOURNAL_PATH=data/research/learning/learning.sqlite3
- ATLAS_LEARNING_OUTCOME_COLLECTION_SECONDS=60 (integer, 60 through 3600)

PAPER environment, PAPER desktop runtime, live trading disabled and a durable
equity paper ledger are required when enabled. Journal/source paths must be
distinct, including symlink/hard-link aliases. Configuration/source paths are
frozen when the collector is constructed. Construction creates no journal or
thread. The GUI launch starts one background worker after showing the window;
the first scan is immediate, followed by an interruptible interval wait after
each scan. Merely constructing a desktop composition does not start collection.
The worker stays active while the GUI is open, independently of market Start/
Stop controls. It does not subscribe to fills or do database work on GUI/fill
callbacks.

Equity uses the actual paper_persistence_path passed to desktop composition.
Crypto/options/futures use their existing sibling ledger paths beside the
configured execution_database_path. Custom equity paths are not guessed from
the generic execution DB name. The equity reader imports only the active paper
campaign, including Warrior/Scalper outcomes. Proxy outcomes remain separate,
with their existing limitations: unknown historical attribution/input provenance,
unknown crypto fees, and no independent legacy futures multiplier reconciliation.
Sources are read-only consistent snapshots using the existing bounded importers.
No source migration, stale-data relaxation, fee inference or account PnL rewrite
is performed. Repeated closed outcomes are idempotent; conflicts refuse that
source batch. Successful sources commit independently and are never pooled into
one alleged complete account or paired experiment.

Missing sources are reported MISSING_SOURCE and are not created. Source errors
are reported READ_FAILED or IMPORT_FAILED with exception class only, logged
without exception text or credentials, and retried on the next cycle. A target
error produces JOURNAL_UNAVAILABLE before any source import. Per-source inserted,
already-present and exclusion counts plus the latest cycle are available from
learning_collector.snapshot(). Status is in-memory and logs; it is not durable
proof of complete coverage and is not automatically added to Grok's context.
The collector never reviews experiments or makes a model/network call. Existing
learning reviews remain frozen; new reviews can consume journaled outcomes using
their existing observational/proxy scopes.

Shutdown requests stop before releasing composed trading resources and waits
at most two seconds. A read/import already in progress is not forcibly killed;
timeout returns false and reports STOPPING, retaining the worker reference and
refusing restart. Stop interrupts cadence waits and checks again after reads
before importing. Already committed imports remain saved. There is no guaranteed
last scan at shutdown: the next launch/manual cycle catches pending outcomes.
Bounded source readers still refuse oversized histories rather than silently
import a partial sample. Neither workers nor CLI enable markets or trading.

Offline verification without starting Atlas (journal is the only write target):

```powershell
& .\.venv\Scripts\python.exe -m app.asset_modules.learning_collector `
    --execution-ledger data/sandbox/paper-execution.sqlite3 `
    --journal data/research/learning/learning.sqlite3
```

The CLI performs exactly one cycle, not an ongoing worker. Its sibling-source
paths follow the supplied equity ledger; desktop composition uses configured
execution siblings as described above. It returns nonzero for source/journal
failures, while reporting the other source results. To enable future automatic
collection from the same PowerShell before manually launching Atlas:

```powershell
$env:ATLAS_LEARNING_OUTCOME_COLLECTION_ENABLED = "true"
$env:ATLAS_LEARNING_OUTCOME_JOURNAL_PATH = "data/research/learning/learning.sqlite3"
```

These process settings are not persisted to .env. Persist those same two keys
in the local .env if subsequent launchers should enable collection. This feature
does not configure a Grok key/model or enable AI entry review.
