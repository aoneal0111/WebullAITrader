# Retained Warrior trigger expiry

Warrior may keep a genuinely triggered structural episode eligible while a
later detector evaluation shows FORMING. Fresh execution quotes still have
to confirm the retained structural trigger and pass the existing execution,
reward, account, and risk checks.

The continuity interval is 120 seconds from the last genuine technical
confirmation. Previously, rebuilding a retained signal refreshed its cached
timestamp. Repeated FORMING evaluations could therefore keep the old trigger
eligible indefinitely. Retained refreshes now preserve that confirmation
timestamp in the cache; the current event's signal and quote checks still use
current timestamps. A genuinely confirmed technical signal may renew the
interval.

This expiry applies to entry authority, not discovery retention. It does not
remove a symbol from the mover radar, close a position, or change protection
for a position already entered.

The October 9 FSLY and JAGX captures show execution bids above earlier retained
triggers while newer detector triggers were not confirmed. Those records
establish which triggers were used, but do not establish that the interval
overrun caused either loss. The regression independently reproduces the
expiry defect and covers genuine confirmation renewal.
