# Regenerated SIP detector replay

This separate offline study evaluates the five existing research detector families on the same 600 selected June-August 2026 symbol-days. It uses the completed SIP capture manifest and validates file SHA256 and row counts. Original IEX files and labels are never imported. Empty partitions remain explicit; all cases exclude the entire affected day from headline metrics rather than treating unavailable symbols as zero-profit days.

Before generating signals or price outcomes, the program writes a frozen plan and hashes of the capture manifest, detector implementation/context, replay adapter and exit mechanics. Each detector receives only up to 64 observed completed bars with source-derived session labels. No future bars, old target labels, or guessed prior close enter the context. The same existing DETECTED/TRIGGER_ARMED/STRENGTHENING states are eligible. This does not establish that every armed state is a production-executable entry.

Signals are deduplicated by family and anchor within each symbol-day, independent of multi-family miner primary selection. No claim of exact grouping parity with the original corpus. Context hashes and detector versions accompany every private signal. Existing HOD/opening-level semantics are retained; the 64-bar window is not silently turned into full-session history.

Four fixed exits and two fixed cost assumptions reuse the chronological daily-wallet engine. Exact minute after detection minute is required, risk and size are repriced, prior-minute volume limits size, and unavailable minutes or intrabar ordering remain unresolved. Every day uses a separate $1000 wallet per case, not a continuously compounded account. The output provides monthly metrics, incomplete-day exclusions, and dates common to all eight cases within each family. Comparisons between families on different dates are not fair rankings.

Selection and prior period outcomes were already inspected. This is retrospective development, not an untouched validation set. Missing SIP minutes do not by themselves prove data corruption or permit filling a bar. Current account spreads, quote freshness, halts, latency, and trading costs remain unverified. Nothing qualifies or activates a strategy, sends orders, or calls Grok. Use normal visible Atlas daily recording separately.

Run `tools/replay-regenerated-sip-history.ps1` with the reviewed revision and the exact completed SIP `history` directory. Upload only `sip-regenerated-replay-summary.json`; retain private signal/trade/day files locally.
