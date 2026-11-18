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
                 |
                 +---- Celery Beat (one scheduler)
```

Recommendations:

- Terminate TLS at a trusted reverse proxy.
- Expose only the frontend and, if needed, the API through the proxy. Do not publish PostgreSQL, Redis, or Celery ports.
- Place PostgreSQL and Redis on a private network.
- Set `BACKEND_URL` on the Next.js runtime to the private backend URL.
- Use a shared upload volume or configure the S3-compatible backend for every backend and worker replica.
- Run at least one API replica, one or more workers, exactly one Celery Beat scheduler, and a managed or highly available PostgreSQL/Redis deployment according to availability requirements.
- If chat streaming is exposed through a reverse proxy, disable response buffering for the API route. The API already sends `X-Accel-Buffering: no` and `Cache-Control: no-transform`.
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
FRONTEND_BASE_URL=https://app.example.com
EMAIL_BACKEND=smtp
EMAIL_FROM=DocuMind AI <no-reply@example.com>
SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_USERNAME=<secret>
SMTP_PASSWORD=<secret>
COOKIE_SECURE=true
COOKIE_SAMESITE=lax
CORS_ORIGINS=https://app.example.com
```

Validation currently rejects production startup when:

- `COOKIE_SECURE` is false.
- `DEBUG` is true.
- `SECRET_KEY` contains the development placeholder.
- `CORS_ORIGINS` contains `*`.
- `EMAIL_BACKEND` is `console`.
- `SMTP_HOST` is missing for SMTP delivery.
- Only one of `SMTP_USERNAME` and `SMTP_PASSWORD` is set.

Decide whether `EMAIL_VERIFICATION_REQUIRED` should be `true`. When enabled, unverified users cannot sign in until they consume the emailed token. Verification and reset links use `FRONTEND_BASE_URL`, so it must be the public dashboard origin.

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

The migration chain creates the pgvector extension, application tables, indexes, the HNSW index, account-token state, and asynchronous-summary columns. Apply every revision through `head`; do not skip intermediate revisions.

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

- PostgreSQL: users, documents, chunks, vectors, conversations, refresh-token state, account tokens, and summary state.
- Uploaded files: the `uploads_data` volume, configured storage path, or S3-compatible bucket.

PostgreSQL dump example:

```bash
docker compose exec -T postgres pg_dump -U documind -d documind -Fc > documind.dump
```

Restore example into an empty database:

```bash
cat documind.dump | docker compose exec -T postgres pg_restore -U documind -d documind --clean --if-exists
```

Back up uploads separately. Database and file backups should correspond; a document row without its file cannot be reprocessed. A file without its row is orphaned and should be cleaned up separately.

Redis contains broker messages, rate-limit counters, and transient task state. It does not replace a PostgreSQL backup. Periodic Celery Beat tasks requeue stale document/summary rows and delete expired auth tokens, but interrupted tasks may still need operator review. Run Beat as a singleton; duplicate schedulers can enqueue duplicate recovery work.

## Health and monitoring

| Endpoint | Meaning |
|---|---|
| `/health/live` | Process is running and serving requests. It does not check dependencies. |
| `/health/ready` | Database query succeeds. Returns `503` otherwise. |

Suggested alerts:

- Readiness failure for more than one minute.
- Backend 5xx rate increase.
- Celery queue depth or oldest-message age increase.
- Celery worker and Beat healthcheck failures.
- Document failures, processing durations, summary failures, and stale summary attempts.
- Email delivery errors, SMTP authentication failures, and verification/reset volume anomalies.
- Chat SSE error rates and premature stream terminations.
- OpenAI error rate, latency, and token usage.
- PostgreSQL connections, disk, and slow queries.
- Redis memory and eviction events.
- Upload volume free space or S3 bucket capacity, request errors, and lifecycle policy.

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
- Upload storage and `STORAGE_BACKEND`

If local disk storage is used, all backend replicas must mount the same shared volume. Configure the S3-compatible backend for multi-replica deployments. The API and workers must use the same bucket, prefix, and credentials.

### Workers

Celery concurrency is CPU-, memory-, and network-bound by PDF parsing, embedding batches, and summary generation. Increase worker replicas before increasing concurrency on a single worker if memory is constrained. Summaries use the same queue and can be delayed by large ingestion jobs; monitor oldest-message age and add dedicated queues if summary latency matters.

Workers must use the same upload storage as the API. They share PostgreSQL, Redis, S3 credentials, ClamAV configuration, OpenAI configuration, and email settings. Run Celery Beat separately and only once per environment.

The API process serves SSE streams and should be sized for open connections as well as request throughput. Proxies must not buffer `/api/v1/chat/stream` responses.

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

Update the secret and restart every backend and worker process that makes AI requests. Verify embeddings, chat, and summary generation with a small smoke test.

### SMTP credentials

Update `SMTP_USERNAME` and `SMTP_PASSWORD` together, restart the API, and send test verification and password-reset messages. Verify that messages arrive and that links use the public `FRONTEND_BASE_URL`.

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
7. Verify `/health/ready`, login, email verification/reset delivery, upload, processing, search, chat, streamed chat through the edge proxy, and summary polling.
8. Confirm Celery Beat is running the recovery and token-cleanup schedules.
9. Watch error and queue metrics.

Rollback application images only after checking schema compatibility. Do not assume an older image can run against a newer schema.

## Troubleshooting

| Symptom | Likely cause | Action |
|---|---|---|
| Upload returns 413 with `storage_quota_exceeded` | The user's stored PDFs reached `USER_STORAGE_QUOTA_MB` | Delete unneeded documents or raise the quota. |
| Upload returns 503 with `malware_scanner_unavailable` | ClamAV is enabled but unreachable or returned an invalid response | Check `CLAMAV_HOST`, `CLAMAV_PORT`, daemon health, and firewall rules; disable scanning only with an explicit security decision. |
| Upload returns 400 with `malware_detected` | ClamAV identified the file | Treat the upload as hostile and review client/user activity. |
| S3 upload or processing fails | Bucket, endpoint, credentials, prefix, or TLS misconfigured | Verify `S3_*` settings, bucket existence, IAM permissions, and connectivity from API and worker containers. |
| Upload returns 503 or document becomes `failed` with queue message | Redis or Celery unavailable | Check Redis, `celery-worker` logs, broker URL, and worker health. |
| Document remains `pending` | Worker not running or broker unreachable | Start/inspect the worker and Celery queue. |
| Document remains `processing` indefinitely | Worker crashed during processing or Celery Beat is not running | Inspect worker and Beat logs; the recovery task requeues stale rows automatically. If Beat is disabled, reset the row to `pending` and enqueue again after confirming no worker is active. |
| Document fails with an OCR message | OCR is disabled, Tesseract or a language pack is missing, or page/time limits were exceeded | Check `OCR_ENABLED`, `OCR_LANGUAGES`, `OCR_MAX_PAGES`, `OCR_TIMEOUT_SECONDS`, and the worker image's Tesseract packages. |
| Search returns no results | No completed chunks, weak query, or similarity below `SEARCH_MIN_SIMILARITY` | Check document status, lower the threshold cautiously, or query more specific terms. |
| Chat says there is insufficient context | Retrieval returned no chunks above the threshold, or any cited claim failed validation | Verify chunks exist, selected document IDs are completed, and embeddings match the query model. If retrieval succeeds, inspect discarded-citation logs; note that a single unsupported cited claim now refuses the whole answer. Consider `RAG_CITATION_SEMANTIC_CHECK=true` or a lower `RAG_CITATION_MIN_SUPPORT` only after manual review. |
| Summary stays `pending` or `processing` | Worker, broker, or Celery Beat is unavailable, or a summary attempt was interrupted | Check worker logs and queue depth. Beat requeues stale attempts after `SUMMARY_STUCK_AFTER_SECONDS`; if Beat is disabled, re-request the summary after confirming no worker owns the attempt. |
| Summary returns `failed` | AI provider, queue, or summary processing failure | Inspect worker logs and `summary_error`, verify OpenAI access, then request generation again (use `force=true` if needed). |
| Verification or reset email is missing | SMTP misconfiguration, rejected credentials, or `EMAIL_BACKEND=console` outside development | Check SMTP settings and worker/API email logs. Production startup rejects the console backend. Confirm `FRONTEND_BASE_URL` is the public origin used in links. |
| Streamed chat arrives all at once | A proxy or gateway buffers the SSE response | Disable proxy buffering for `/api/v1/chat/stream`, honor `X-Accel-Buffering`, and verify timeouts allow long-lived responses. |
| Stream ends without `done` | Client disconnected, provider failed after streaming began, or validation/persistence failed | Check backend logs for the stream error path. The API does not persist partial answers; retry the request. |
| Account deletion leaves files behind | Storage deletion failed after the database commit | Inspect warnings and remove orphaned objects by the original `<user_id>/<document_id>.pdf` key; database rows are already gone. |
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
- [ ] Celery worker connected and document processing verified.
- [ ] Exactly one Celery Beat scheduler running; stale document/summary recovery and token cleanup verified.
- [ ] SMTP delivery configured and tested; `EMAIL_VERIFICATION_REQUIRED` and token lifetimes reviewed.
- [ ] `FRONTEND_BASE_URL` points at the public dashboard origin.
- [ ] Upload volume shared or S3-compatible object storage configured and tested.
- [ ] Per-user quota and optional ClamAV scanning settings reviewed.
- [ ] Rate limits, SSE proxy buffering, and timeouts tuned for expected traffic.
- [ ] Logs centralized and secrets, email bodies, and tokens excluded.
- [ ] Monitoring and alerting configured for API, streaming, queue, database, Redis, email, and AI failures.
- [ ] Smoke test covers registration, login, verification/reset delivery, upload, processing, search, chat, streamed chat, citations, and summary polling.
- [ ] Known limitations reviewed with the product owner.
