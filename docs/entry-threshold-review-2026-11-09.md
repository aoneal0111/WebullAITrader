# Entry thresholds and inactive equity desktop

The uploaded Nov 9 status shows an expired, closed desktop Atlas session, live
crypto workers without source BUY signals, and a separate regular-session daily
equity proxy. XLE already passed the original equity entry signal but was waiting
for the regular session. A stopped desktop cannot be repaired with RSI changes.
No downstream entry submission occurred in the crypto samples, so the status
does not prove that every subsequent native execution gate would approve a BUY.

This review varies entries only: equity RSI2 thresholds and the existing hourly
ROC pullback threshold. Exits remain unchanged. It covers 24 daily equity screens,
48 hourly crypto screens, and 12 disconnected native crypto backtests. The
previously examined periods are exploratory evidence, not untouched holdouts.

## Native crypto result

Recent Kraken USD hourly candles, Oct 19–Nov 8, $10,000 independent wallets,
six native slots, identical existing ATR stop/SMA5/time exits:

| Required 3-hour decline | Closed trades | Win rate at 0.4% / 0.8% per side | Native terminal PnL at 0.4% / 0.8% |
|---|---:|---:|---:|
| 8% original | 0 | undefined | $0 / $0 |
| 6% | 0 | undefined | $0 / $0 |
| 4% | 0 | undefined | $0 / $0 |
| 3% | 2 | 0% / 0% | −$75.24 / −$101.33 |
| 2% | 8 | 12.5% / 0% | −$337.93 / −$439.21 |
| 1% | 52 | 19.2% / 5.8% | −$1,343.32 / −$1,922.36 |

Native backtests have no explicit slippage; the separate screening model includes
5 bps adverse slippage per side and also loses. Native replay assumes market
fills, whereas the existing forward worker uses native paper limit entries.
Native callback audits recorded zero errors. No losing crypto relaxation is
installed. These results concern the ROC addon, not a new test of every NFI,
E0, NASOS, BB, CRSI or Wave parameter.

## Equity comparison selected for paper observation

Connors RSI2 below 12.5 versus the original below 10, preserving the SMA200 trend
condition and SMA5 exit. Twelve fixed ETFs, $10,000 wallet, $1,000 stakes,
5 bps commission plus 5 bps adverse slippage per side, next-session open model:

| Period | Original closed / win rate / marked net | RSI2 12.5 closed / win rate / marked net |
|---|---|---|
| 2022–2023 | 137 / 52.6% / −$309.23 | 158 / 50.6% / −$442.17 |
| 2024 | 120 / 65.0% / +$279.77 | 143 / 66.4% / +$333.72 |
| Jan–Apr 2025 | 30 / 73.3% / +$149.17 | 35 / 74.3% / +$196.44 |

This is a modest activity improvement in two periods, with a worse earlier loss.
It is an experimental forward comparison, not a proven profitable replacement.
Historical equity inputs end April 30, 2025: missing later evaluation periods are
excluded, never counted as zero-trade strategy outcomes. Separate public Yahoo
readings captured Nov 9 show XLE RSI2 approximately 2.41, above its SMA200, so
loosening RSI was not required for that symbol's original signal. At capture the
public quote was still Friday's close and the regular session had not opened.

`prepare-entry-threshold-test.ps1` creates an isolated read-only source checkout
and manual daily equity comparison with three separate wallets: both original
daily controls and Connors RSI2 12.5. This is a public-price proxy; it does not
attach the daily strategy to Warrior or Scalper. New diagnostics expose each
equity condition and crypto indicator/parameter values without changing signals.
Existing workers do not receive the new diagnostics until their source is updated.

With `-PrepareAtlas`, the script also prepares a fresh eight-hour native-account
PAPER desktop trial using the already installed timestamp-repaired desktop code.
It preserves old trial records and paper ledgers. Grok controls Warrior, Scalper
stays native, and the existing 20-call Warrior budget still applies. Preparation
starts no workers, GUI, paid model calls or trades. Start the printed manual
launchers and click START in Atlas. Never run an old Grok observer concurrently.

Cloud verification: 40 tests plus 18 native subtests pass, including an actual
extra paper BUY from the relaxed threshold, baseline wallet isolation, restart
deduplication, source signal prefix invariance and sealed native paper profiles.
PowerShell and Docker are unavailable in this cloud; the installer validates the
local pinned image and Compose file before handing over the manual launchers.
