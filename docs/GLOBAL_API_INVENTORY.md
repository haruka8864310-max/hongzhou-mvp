# Page and API inventory (phase 1)

Existing APIs are preserved. Legacy operations are hidden from primary navigation. See GLOBAL_PHASE1.md for source and model limitations.

| Method | Path | Implementation module |
|---|---|---|
|GET,HEAD|`/openapi.json`|fastapi.applications|
|GET,HEAD|`/docs`|fastapi.applications|
|GET,HEAD|`/docs/oauth2-redirect`|fastapi.applications|
|GET,HEAD|`/redoc`|fastapi.applications|
|GET|`/api/health`|app|
|GET|`/index.html`|app|
|GET|`/market.html`|app|
|GET|`/`|app|
|GET|`/legacy`|app|
|GET|`/market-guide`|app|
|GET|`/api/prices`|app|
|POST|`/api/prices`|app|
|GET|`/api/customers`|app|
|POST|`/api/customers`|app|
|GET|`/api/trades`|app|
|POST|`/api/trades`|app|
|GET|`/api/summary`|app|
|POST|`/api/backup`|app|
|POST|`/api/restore`|app|
|GET|`/api/export/{table}`|app|
|GET|`/api/portable-backup`|app|
