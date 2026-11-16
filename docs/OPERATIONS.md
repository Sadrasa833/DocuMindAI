# Operations and deployment

This guide covers operating DocuMind AI outside local development. The included `docker-compose.yml` is a development stack, not a hardened production deployment.

## Production topology

Recommended components:

```text
Internet
   |
   v
TLS reverse proxy / load balancer
   |                             \
   v                              v
Next.js dashboard :3000       (optional direct API access,
   |                           preferably disabled)
   | server-side proxy
   v
FastAPI backend :8000
   |            \
   |             \
PostgreSQL       Redis
+ pgvector       |
                 +---- Celery worker(s)
```

Recommendations:

- Terminate TLS at a trusted reverse proxy.
- Expose only the frontend and, if needed, the API through the proxy. Do not publish PostgreSQL, Redis, or Celery ports.
- Place PostgreSQL and Redis on a private network.
- Set `BACKEND_URL` on the Next.js runtime to the private backend URL.
- Use a shared upload volume or an object-storage adapter for every backend and worker replica.
- Run at least one API replica, one or more workers, and a managed or highly available PostgreSQL/Redis deployment according to availability requirements.
- Set `LOG_JSON=true` and ship logs to a centralized system.
- Use a secret manager rather than committing or baking secrets into images.

## Build and run production images

Backend:

```bash
docker build --target production -t documind-backend:1.0.0 ./backend
docker run --env-file .env.production -p 8000:8000 documind-backend:1.0.0
```

Frontend:

```bash
docker build --target production -t documind-frontend:1.0.0 ./frontend
docker run --env BACKEND_URL=http://backend:8000 -p 3000:3000 documind-frontend:1.0.0
```

The frontend production image uses Next.js standalone output. `BACKEND_URL` is read by the server-side proxy at runtime and is not embedded in the browser bundle.

The backend production image does not enable automatic migrations or broad proxy trust by default. Configure these explicitly:

- Set `RUN_MIGRATIONS=true` for exactly one startup path, then disable it for other replicas.
- If Uvicorn is behind a trusted proxy, run it with `--proxy-headers` and an appropriate `--forwarded-allow-ips` value, or set the equivalent `FORWARDED_ALLOW_IPS` environment variable.
- Never trust arbitrary forwarded headers from the public internet. Ensure the edge proxy overwrites client-supplied forwarding headers.

## Required production settings

```dotenv
ENVIRONMENT=production
DEBUG=false
LOG_JSON=true
SECRET_KEY=<at least 32 random characters>
OPENAI_API_KEY=<secret>
COOKIE_SECURE=true
COOKIE_SAMESITE=lax
CORS_ORIGINS=https://app.example.com
```

Validation currently rejects production startup when:

- `COOKIE_SECURE` is false.
- `DEBUG` is true.
- `SECRET_KEY` contains the development placeholder.
- `CORS_ORIGINS` contains `*`.

Use HTTPS. Secure cookies will not be sent over plain HTTP.

### Cookie domain

Leave `COOKIE_DOMAIN` empty when frontend and API share a hostname through the Next proxy. If you use a shared parent domain, set `COOKIE_DOMAIN=.example.com` deliberately and understand the cross-subdomain exposure.

### Proxy and client IP behavior

Rate limiting uses the client IP seen by FastAPI. In a container topology:

1. The browser connects to the edge proxy.
2. The edge proxy sets a sanitized `X-Forwarded-For`.
3. Next.js or the edge proxy forwards the request.
4. Uvicorn must trust only the immediate known proxy before using forwarded headers.

If forwarded headers are not trusted, all users may share the proxy's IP for rate limiting. If they are trusted too broadly, clients can spoof their IP. Configure this for the actual network topology and test the resulting `request.client.host` behavior.

## Migrations

The initial migration creates the pgvector extension, tables, indexes, and HNSW index.

Run migrations from one controlled job or one backend replica:

```bash
docker compose exec backend alembic upgrade head
```

Or, for a separately deployed backend image:

```bash
docker run --rm --env-file .env.production documind-backend:1.0.0 alembic upgrade head
```

Check the current revision:

```bash
alembic current
```

Before every production migration:

1. Take a PostgreSQL backup.
2. Verify the database user can create extensions or confirm pgvector is already installed.
3. Review the migration for locking and index-build cost.
4. Run in staging against a production-like dataset.
5. Apply once, then verify API and worker health.

The HNSW index is built during the initial migration. On large existing datasets, use concurrent index creation and staged migration strategies rather than issuing a blocking index build in a live system.

### Embedding dimension changes

Changing the embedding model or dimensions is a data migration, not only a configuration change. It requires:

1. A migration altering or replacing the vector column and index.
2. Updating `EMBEDDING_DIMENSIONS` consistently.
3. Re-embedding every document.
4. Verifying that query embeddings use the same model and dimensions.

Do not deploy a new dimension setting while old vectors remain in the database.

## Backups and restore

Back up both durable data stores:

- PostgreSQL: users, documents, chunks, vectors, conversations, and refresh-token state.
- Uploaded files: the `uploads_data` volume or configured storage path.

PostgreSQL dump example:

```bash
docker compose exec -T postgres pg_dump -U documind -d documind -Fc > documind.dump
```

Restore example into an empty database:

```bash
cat documind.dump | docker compose exec -T postgres pg_restore -U documind -d documind --clean --if-exists
```

Back up uploads separately. Database and file backups should correspond; a document row without its file cannot be reprocessed. A file without its row is orphaned and should be cleaned up separately.

Redis contains broker messages, rate-limit counters, and transient task state. It does not replace a PostgreSQL backup. A periodic Celery Beat task requeues stale `processing` rows, but interrupted tasks may still need operator review.

## Health and monitoring

| Endpoint | Meaning |
|---|---|
| `/health/live` | Process is running and serving requests. It does not check dependencies. |
| `/health/ready` | Database query succeeds. Returns `503` otherwise. |

Suggested alerts:

- Readiness failure for more than one minute.
- Backend 5xx rate increase.
- Celery queue depth or oldest-message age increase.
- Celery worker healthcheck failures.
- Document failures and processing durations.
- OpenAI error rate, latency, and token usage.
- PostgreSQL connections, disk, and slow queries.
- Redis memory and eviction events.
- Upload volume free space.

Request logs include method, path, status code, duration, and request ID. Correlate support reports with `X-Request-ID`. The API does not currently expose Prometheus metrics; add an instrumentation layer before relying on metric-based alerting.

Flower is available only with the Compose monitoring profile:

```bash
docker compose --profile monitoring up -d flower
```

Protect Flower behind authentication and a private network in any shared environment.

## Scaling

### API

The API is stateless except for local storage and Redis-backed rate limits. Scale it behind a load balancer. All replicas must use the same:

- `SECRET_KEY`
- Database
- Redis
- `BACKEND_URL` target from the frontend
- Upload storage

If local disk storage is used, all backend replicas must mount the same shared volume. Object storage is the cleaner long-term option.

### Workers

Celery concurrency is CPU-, memory-, and network-bound by PDF parsing and embedding batches. Increase worker replicas before increasing concurrency on a single worker if memory is constrained.

Workers must mount the same upload storage as the API. They share PostgreSQL and Redis configuration.

### Database

Tune `DATABASE_POOL_SIZE` and `DATABASE_MAX_OVERFLOW` based on total API and worker concurrency. The default pool of 10 plus overflow of 20 is per process, so many replicas can exhaust PostgreSQL connections. Use PgBouncer or smaller pools when scaling horizontally.

### Redis

Use persistence and monitoring appropriate to the deployment. Redis loss affects rate limiting, queued work, and in-flight task state, but not durable application records.

## Logging

Development defaults to human-readable logs. Set:

```dotenv
LOG_JSON=true
LOG_LEVEL=INFO
```

Use `DEBUG` only temporarily and never in production. Logs must not contain passwords, JWTs, refresh tokens, OpenAI API keys, or full document contents. Treat request IDs as correlation identifiers, not secrets.

## Secret rotation

### JWT secret

Rotating `SECRET_KEY` invalidates all access and refresh tokens. Plan for a global sign-out. Restart every API process with the same new value.

### OpenAI key

Update the secret and restart every backend and worker process that makes AI requests. Verify embeddings and chat with a small smoke test.

### Database credentials

Update the application and PostgreSQL credentials together, then restart API and worker processes. Migrations use the same database URL.

### Refresh tokens

Sessions can be invalidated by changing the JWT secret or by deleting/revoking rows in `refresh_tokens`. Changing a user's password already revokes that user's active refresh tokens.

## Upgrade procedure

1. Review release notes and migrations.
2. Back up PostgreSQL and uploads.
3. Build and scan new images.
4. Run tests in staging.
5. Apply migrations from one controlled job.
6. Deploy workers and API, then the frontend.
7. Verify `/health/ready`, login, upload, processing, search, chat, and summaries.
8. Watch error and queue metrics.

Rollback application images only after checking schema compatibility. Do not assume an older image can run against a newer schema.

## Troubleshooting

| Symptom | Likely cause | Action |
|---|---|---|
| Upload returns 503 or document becomes `failed` with queue message | Redis or Celery unavailable | Check Redis, `celery-worker` logs, broker URL, and worker health. |
| Document remains `pending` | Worker not running or broker unreachable | Start/inspect the worker and Celery queue. |
| Document remains `processing` indefinitely | Worker crashed during processing or Celery Beat is not running | Inspect worker and Beat logs; the recovery task requeues stale rows automatically. If Beat is disabled, reset the row to `pending` and enqueue again after confirming no worker is active. |
| Document fails with an OCR message | OCR is disabled, Tesseract or a language pack is missing, or page/time limits were exceeded | Check `OCR_ENABLED`, `OCR_LANGUAGES`, `OCR_MAX_PAGES`, `OCR_TIMEOUT_SECONDS`, and the worker image's Tesseract packages. |
| Search returns no results | No completed chunks, weak query, or similarity below `SEARCH_MIN_SIMILARITY` | Check document status, lower the threshold cautiously, or query more specific terms. |
| Chat says there is insufficient context | Retrieval returned no chunks above the threshold | Verify chunks exist, selected document IDs are completed, and embeddings match the query model. |
| 502 from `/api/proxy/*` | Frontend cannot reach `BACKEND_URL` | Check DNS/service name, network, backend health, and container logs. |
| 401 loop or immediate login redirect | Stale cookies or clock/signature mismatch | Clear site cookies, verify `SECRET_KEY` consistency, and inspect refresh/logout behavior. |
| CORS error | Origin missing from `CORS_ORIGINS` or wildcard rejected | Add the exact scheme/host/port and restart the backend. |
| Cookies not sent in production | `COOKIE_SECURE=true` without HTTPS, wrong domain, or SameSite conflict | Verify TLS and cookie attributes in the browser. |
| 429 responses | Endpoint rate limit reached or proxy IP aggregation | Check limits and trusted-proxy configuration. |
| 503 AI service unavailable | OpenAI error, quota, timeout, or invalid model/key | Check backend/worker logs, provider status, key balance, and model names. |
| pgvector extension error | Database lacks pgvector or extension privileges | Use the pgvector image or install the extension and grant privileges. |
| Vector dimension error | `EMBEDDING_DIMENSIONS` differs from column/migration or provider output | Align model, setting, migration, and re-embedded data. |
| Alembic migration blocked | Database unavailable, wrong URL, or lock | Check connectivity and active locks; restore backup if a migration partially failed. |
| High memory in worker | Large PDFs, high embedding batch size, or too much concurrency | Lower concurrency/batch size and inspect per-task memory. |

## Pre-release checklist

- [ ] Strong `SECRET_KEY` and valid `OPENAI_API_KEY` supplied from secret management.
- [ ] `ENVIRONMENT=production`, `DEBUG=false`, `COOKIE_SECURE=true`.
- [ ] HTTPS and trusted proxy headers configured correctly.
- [ ] Exact CORS origins configured.
- [ ] PostgreSQL 16 with pgvector and a verified HNSW index.
- [ ] Migration applied once and backups verified.
- [ ] Redis private and healthy.
- [ ] Celery worker connected and task processing verified.
- [ ] Upload volume shared or object storage configured.
- [ ] Rate limits and timeouts tuned for expected traffic.
- [ ] Logs centralized and secrets excluded.
- [ ] Monitoring and alerting configured for API, queue, database, Redis, and AI failures.
- [ ] Smoke test covers registration, login, upload, processing, search, chat, citations, and summaries.
- [ ] Known limitations reviewed with the product owner.
