# Production deployment

Use Docker Compose with an unprivileged UID. Supply every LIVE variable through a secret-aware deployment environment. Database paths must point into `/var/lib/webull-trader`; backups belong in `/var/backups/webull-trader`. Never bake `.env.production` into an image. Startup remains stopped until configuration, stores, signing, broker authentication, reconciliation, and market freshness pass.
