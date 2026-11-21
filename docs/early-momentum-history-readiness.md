# Early Momentum history readiness

`app/equity_overhaul/early_history_readiness.py` is an offline, standard-library
coverage audit for a ZIP of normalized JSONL symbol-day files. It opens no
installed database, changes no trading configuration, and never authorizes
entries. It can run directly without importing the scientific/desktop runtime:

```powershell
& .\.venv\Scripts\python.exe app\equity_overhaul\early_history_readiness.py "C:\path\bars.zip"
```

The audit separates regular-session coverage, unavailable/inconsistent previous
closes, extended-hours exclusions, malformed rows, duplicate minutes and input
ordering. It checks finite OHLCV and positive consistent previous close, requires
an aware exact minute timestamp, and reports up to five missing-minute examples.
A missing previous close does not erase otherwise valid bar coverage. Correctly
labeled extended-hours rows are excluded rather than called malformed.

Data readiness conservatively requires all 390 regular-session minutes plus
consistent symbol/date/feed/previous-close identity and no invalid, duplicate or
out-of-order regular rows. Half-days are excluded by this full-day grid policy;
that exclusion is not evidence of a provider fault. No missing candles are
interpolated. Archive size, expanded bytes, member count and line size are
bounded; members are streamed without extracting paths. Duplicate member names
are refused. Original bytes are read only; the input SHA-256 binds the report.

`observed_ten_percent_watch` describes whether the available regular bars reach
10 percent above the file's declared prior close. It is not an entry signal or
a future-high filter for trades. The prior close's internal consistency does not
independently verify its corporate-action convention or timestamp. A positive
coverage result does not establish market cap, point-in-time universe membership,
quote freshness, liquidity, executable fills, or profitability.

Do not treat a large aggregated episode report as complete replay history.
An earlier-entry/partial-exit comparison needs the actual causal bars and a
known prior close for each included session. Retain all exclusions. Do not rank
strategies using only the survivors of missing-bar exit paths, and do not claim
an outcome for a position whose stop, target or session end cannot be resolved.
No promotion, strategy replacement, or change to the prepared observer baseline
is performed by this checker. User research results remain local.

Validation: ten standalone unittest cases cover complete and missing grids,
duplicates, ordering, conflicting/missing previous closes, invalid OHLC/NaN and
naive timestamps, half-days, extended-hours exclusion and a line-size bound.
