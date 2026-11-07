# Historical price coverage audit

Run `tools/audit-equity-price-gaps.ps1` with an exact reviewed revision and the completed study's **results** directory. The audit reuses its read-only SQLite index and verifies the current normalized partitions against the original size and SHA256. It does not scan the 22 GB corpus again. A touched file with identical content is accepted.

The compact summary counts missing minutes between observed bars by Eastern session, gap lengths, and absent exact entry and preceding volume-reference minutes. Selected episode counts overlap strategy families and are not trade counts. At most twelve partition examples are included. No private results belong in the repository.

A missing row alone cannot distinguish an omitted no-trade interval, halt, capture gap, or provider coverage issue. Do not synthesize bars or skip gaps to manufacture a complete performance result. Provider/capture provenance must resolve that distinction before changing replay semantics.

For the next visible desktop run, preserve the installed PAPER baseline and normal background daily recording. Grok may observe separately; do not enable `ATLAS_GROK_PAPER_ENTRIES_ENABLED` as an observer switch. No new strategy qualifies through this audit, and neither live trading nor Grok order dispatch is enabled. A new recording cannot repair past historical data by itself.
