# Current crypto entry readings

The post-repair Nov 9 23:51 UTC operator status shows fresh-ready pairs in all
seven workers, 1,800 successful network attempts and 1,109 shared-response cache
hits. It still shows no current source BUY signals. NFI has 24 no-signal pairs and
four pairs above its source empty-candle limit. Those totals establish what stage
is blocking entries, not which numeric threshold should change or a win rate.

`app/strategy_lab/crypto_entry_readings.py` is a standalone, standard-library-only
reader for the existing Windows paper work directories. It does not import a
trading runtime, call APIs, restart workers, alter pause flags or change parameters.
The standalone script can be extracted from a pinned commit without updating the
installed desktop or running containers.

For each worker it binds the latest recording pointer to the configuration hash
and reads at most 64 MiB from the newest journal tail. It checks individual row
hashes and baseline identities, excludes post-checkpoint and partial records, and
retains the latest two recorded signal readings per pair. It reports the E0 and
ROC clauses for which the existing diagnostics provide exact values; NASOS is
explicitly a partial clause view. Missing indicators and a nonconsecutive previous
candle are unknown, never treated as passing. BB, CRSI and Wave retain their
recorded indicator values without a fabricated full-rule explanation.

The export includes current NFI per-pair activity flags and their names. It keeps
up to 720 recorded native candles for six fixed liquid reference pairs and each
recorded timeframe, with coverage counts. Native zero-volume normalization is
retained and labeled. The latest recorded revision wins when assembling this
diagnostic candle view; it is not claimed as the historical data known to each
earlier decision. Full chain verification and causal replay remain separate work.

The JSON readings and compressed candle sample go in a new output directory. The
ZIP must fit within 4 MiB; otherwise the script retains the JSON and refuses the
oversized ZIP. Original journals and source files remain in place. Per-profile
failures appear in the report so a missing worker is not disguised as no signals.

Six disconnected tests cover source/config hash failures, exact entry clauses,
missing conditions, partial tails, checkpoint exclusions, zero-volume retention,
preservation of source files, and a bounded archive with no network access.

After capture, compare modest entry-only candidates against the recorded source
baseline. Use complete retained warmups where available and disclose missing
coverage. A greater number of eligible candles is not a profit result. Existing
exits stay fixed, and recent live observations alone cannot establish a 60% win
rate. Do not remove the NFI empty-candle limit merely to manufacture eligibility.
