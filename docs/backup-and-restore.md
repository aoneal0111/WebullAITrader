# Backup and restore

Use SQLite online backup while the service is stopped or through `backup_sqlite`. Verify `PRAGMA integrity_check` for authorization, execution, market-event, and emergency-stop databases. Restore into isolated SANDBOX paths, validate schema versions, and prove consumed authorizations, acknowledged mutations, unresolved mutations, and emergency-stop state are unchanged before deployment.
