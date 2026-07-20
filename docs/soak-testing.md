# Soak testing

Run the credential-free synthetic harness first, then PAPER/SANDBOX with explicit credentials. Inject disconnects, reconnects, delayed acknowledgments, duplicates, conflicts, restarts, gaps, rate limits, and database contention. A run fails for duplicate mutation, authorization replay, automatic unresolved replay, undetected divergence, excessive memory growth, or readiness remaining true after dependency failure. Multi-day evidence must be retained before controlled live validation.
