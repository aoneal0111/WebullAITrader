# QuickScalper PAPER symbol authorization

QuickScalper is disabled by default. Enable it explicitly with
`QUICK_SCALPER_ENABLED=true`, `WEBULL_TRADING_ENVIRONMENT=PAPER`, and
`LIVE_TRADING_ENABLED=false`.

`PAPER_SYMBOL_AUTHORIZATION_MODE` selects the internal strategy scope:

| Mode | Warrior | QuickScalper |
| --- | --- | --- |
| `STATIC_ALLOWLIST` (default) | Requires `ALLOWED_SYMBOLS` membership | Requires `ALLOWED_SYMBOLS` membership |
| `DYNAMIC_WARRIOR` | Dynamically authorizes internally assessed Warrior signals | Requires `ALLOWED_SYMBOLS` membership |
| `DYNAMIC_WARRIOR_AND_QUICK_SCALPER` | Same Warrior authorization | Dynamically authorizes internally assessed, enabled PAPER scalp opportunities |

The combined mode is an explicit opt-in. Existing configurations retain their
authorization scope. It is rejected for live trading and LIVE/PRODUCTION trading
environments. QuickScalper still requires PAPER at runtime even when configuration
loading accepts TEST/SANDBOX for other paths.

Dynamic symbol authorization does not bypass canonical quote confirmation,
provider freshness, structural-stop validity, sizing, buying power, exposure,
broker restrictions, risk-engine approval, symbol ownership, duplicate protection,
the PAPER order gateway, or protective-order management. Scanner, research, and
GUI observations do not independently grant execution authority.

The October 8 observation found 66 QuickScalper symbol-authorization rejections
with `ALLOWED_SYMBOLS=AAPL` and `DYNAMIC_WARRIOR`. The new combined mode resolves
that configuration gap without expanding the existing Warrior-only mode or
changing the static allowlist.
