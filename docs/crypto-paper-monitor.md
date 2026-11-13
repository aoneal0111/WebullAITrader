# Crypto paper bot monitor

Atlas’s existing **Crypto** tab displays the actual Docker paper strategy wallets inside the main window. Mission Control shows bot health; Positions shows holdings; Orders shows working orders; Activity shows recent completed trades. Each view also provides tabs for the other data. Scanner retains the existing research-only scanner and does not represent a trading engine. The previous separate Crypto bots button has been removed. Selecting Crypto never activates an asset module or starts equity, Docker, orders or Grok.

The optional diagnostic command `python -m app.gui.crypto_monitor` still opens a standalone view, but normal monitoring uses the existing Atlas Crypto tab.

The standalone window is appropriate when crypto continues overnight and Atlas equity remains closed. Prepared Open-Crypto-Monitor.ps1 and Show-Crypto-Monitor.ps1 launchers open the view or print its JSON. The integrated view replaces the unrelated native simulator display; the independent Docker strategy wallets retain separate labels and balances.

The monitor refreshes every five seconds using a background thread. It discovers the known NFI, Bollinger, five-candidate fleet and ROC experiment directories under LOCALAPPDATA/AtlasResearch, with a maximum of 32 wallets. An explicit `--base` supports another research root. It reads bounded status JSON and SQLite in read-only transactions, including committed WAL data. Docker inspection requests only each known container's name, running flag and state, with a three-second timeout. Missing Docker access means process state is unverified. A recent status file alone never proves that a process is alive; files older than two minutes appear stale. One unreadable wallet does not hide the others.

Views include bot health, open holdings, the latest 20 closed trades per wallet, and up to 50 working orders per wallet. Closed net and win rate derive from closed database lifecycles; open partial realized P/L is separate in the JSON. Current unrealized P/L is unavailable. Strategy wallets are independent, so their capital and returns are not combined. This interface does not qualify a strategy, guarantee an entry or repeat a historical win rate.

For a read-only JSON check without Qt, run `python -m app.gui.crypto_monitor --check`. Add `--no-docker` for a filesystem-only check. Credentials, full Docker metadata and config contents are never displayed.

`tools/prepare-crypto-monitor.ps1` tests an isolated exact-revision checkout before updating the repository and preparing launchers. Close Atlas before installing; running crypto containers keep their source mounts and wallets. No bots are restarted and trial expiries are not renewed. The old equity trial must be checked before the next equity session.

`tools/prepare-atlas-crypto-tab.ps1` runs GUI and equity recovery regressions, installs this view, and prepares a new explicit 24-hour GROK_WARRIOR_ACCOUNT_V6 equity paper trial. Its clock starts at manual launch. It retains all previous trials, paper ledgers, account risk policy and paid-call limits; crypto containers need no restart. The installer does not launch Atlas. Reconnect and SDK failures can still occur externally: regression tests validate the repaired paths, not uninterrupted future connectivity or profitable entries.
