# Default equity entry sessions

Warrior and Quick Scalper now default to PREMARKET and REGULAR entry sessions.
AFTER_HOURS is removed from both default session sets. Premarket participation,
adaptive spread/RVOL behavior and existing risk/tradability controls are retained.
This is a session policy correction; it does not establish a higher win rate.

Quick Scalper's calendar-backed entry window also ends at the actual regular
market close, including early closes. It still reserves the configured full
holding-time budget before each allowed session boundary. Delayed review,
replacement and recovery cannot create new scalp entry authority after the
regular close, even if recovered metadata contains a later deadline.

SELL management is independent of BUY admission. Filled-share protection,
stop amendments, target execution and full exits remain available after entry
admission closes. Existing positions are not forcibly liquidated by this change.
The general extended-hours DAY expiry remains intact to preserve protection.

Warrior's change is its default strategy session set; custom configuration is
not audited or universally overridden by this change. The scalper calendar
boundary independently constrains its canonical entry path. Installed source
and effective runtime configuration still require workstation verification.

## Profit policy scope

Quick Scalper calls the shared `ProfitRetentionPolicy` and sends stop tightening
and max-hold/stall exits through the canonical bridge. Warrior has existing
partial-target and structural-trailing management. Its separate
`ProfitRetentionShadow` experiment records prospective gross BID marks and has
no execution authority. That shadow is not a newly activated profit exit, and
its hypothetical marks cannot be treated as realized gains.

Validation covers calendar boundaries and early closes, stale/recovered entry
authority, default policy sets, after-hours scalp BUY refusal and protective SELL
delegation, as well as existing max-hold, profit retention and bridge behavior.
No Atlas GUI is launched by these tests or read-only collectors.
