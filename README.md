# DocuMind AI

DocuMind AI is a production-oriented, multi-user document intelligence application. It ingests PDFs, processes them asynchronously, indexes their contents with pgvector, and provides citation-grounded question answering, semantic search, and document summaries.

The repository contains a FastAPI backend, a Next.js dashboard, a Celery worker, and a Docker Compose development stack.

## Verification status

Verified in this workspace:

- Backend default suite: **160 tests passed, 10 infrastructure tests skipped** on Python 3.14 with SQLite and mocked AI providers.
- Backend: **89% line coverage** across `app/` for the default suite.
- Backend: Ruff lint and formatting checks pass.
- Infrastructure: **10 integration tests passed** against the live Compose stack, covering PostgreSQL 16/pgvector migrations from an empty schema, vector search, HNSW usage, ownership isolation, Redis, Celery processing, Persian OCR, Celery Beat recovery, and streamed chat.
- Docker Compose health checks passed for backend, worker, beat, frontend, PostgreSQL, Redis, and the mock AI service.
- Frontend: TypeScript, ESLint, the Next.js production build, and **2 Playwright browser E2E tests** pass.
- RAG dataset validation passes for 10 documents and 110 questions (100 answerable, 10 unanswerable).
- Docker Compose configuration parses successfully with the example environment.

Not verified in this workspace:

- No live OpenAI request was made; infrastructure tests and browser E2E tests use the deterministic mock AI service.

See [Testing](#testing) and [Known limitations](#known-limitations) for details.

## Features

### Documents

- Multi-user PDF upload with authentication and ownership isolation.
- PDF extension, MIME type, signature, parseability, size, and path-safety validation.
- 20 MB per-file upload limit and 1 GB per-user storage quota by default.
- Local or S3-compatible storage, with optional ClamAV malware scanning.
- Text extraction with page numbers, OCR fallback for scanned pages, and overlapping chunking.
- Asynchronous processing through Celery and Redis.
- Processing status, retry behavior, error reporting, and idempotent task handling.
- Document filtering, search, sorting, pagination, statistics, deletion, and polling.
- Short and detailed summaries generated asynchronously by Celery, with status polling, per-attempt ownership, and stuck-job recovery.
- Map-reduce summarization for long documents.

### Search and AI

- OpenAI embeddings with batched requests and retry/backoff behavior.
- pgvector cosine similarity search with an HNSW index in PostgreSQL.
- Minimum-similarity filtering to avoid answering from irrelevant chunks.
- Optional search scope across selected documents.
- Grounded RAG answers with source citations containing document, page, and chunk identifiers.
- Optional SSE streaming at `POST /api/v1/chat/stream`; the final event always carries the validated answer and citations.
- Claim-level citation validation using numeric, negation, and lexical evidence, with optional batched semantic checking.
- Any unsupported cited claim causes a deterministic insufficient-context refusal instead of returning a partially verified answer.
- Historical citations remain readable after a document is deleted and are marked unavailable in the UI.
- Conversation persistence and recent-message context for follow-up questions.
- Prompt-injection boundaries that mark retrieved content as untrusted data.
- Graceful "insufficient context" response without calling the language model when retrieval is weak.

### Security and operations

- Argon2 password hashing.
- Short-lived JWT access tokens and rotating refresh tokens in `HttpOnly` cookies.
- Refresh-token reuse detection that revokes a user's active sessions.
- CSRF-conscious same-origin proxy for browser mutations.
- Optional email verification and password-reset flows through console or SMTP email backends.
- Password-confirmed account deletion, including stored files and active sessions.
- Redis-backed rate limiting with an in-memory fallback.
- Request IDs, structured logging support, security headers, and consistent error envelopes.
- Liveness and readiness probes.
- Docker health checks and automatic migrations on backend startup.

### Dashboard

- Registration, login, and logout.
- Dashboard statistics and recent documents.
- Upload progress, polling, document filtering, and sorting.
- Document detail, asynchronous summary polling, and unavailable-citation rendering.
- Citation-aware chat with an optional streaming mode and conversation history.
- Profile and password management.

## Architecture

```text
Browser
   |
   | HTTP, HttpOnly auth cookies
   v
Next.js dashboard :3000
   |
   | same-origin server proxy /api/proxy/v1/*
   v
FastAPI API :8000 ---------------------------> Redis :6379
   |                                             |
   | SQLAlchemy async                            | Celery broker + rate limits
   v                                             v
PostgreSQL 16 + pgvector                    Celery worker
   ^                                             |
   |                                             | reads PDFs, extracts text,
   +---------------------------------------------+ embeds chunks, writes vectors
                                                 |
                                                 v
                                            OpenAI API
                              embeddings, RAG answers, summaries
```

The API is ownership-scoped: every document, chunk, and conversation query is constrained by the authenticated user's ID. The Next.js proxy forwards cookies without exposing tokens to client-side JavaScript.

More detail is available in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) and [docs/OPERATIONS.md](docs/OPERATIONS.md).

## Tech stack

| Layer | Technology |
|---|---|
| API | FastAPI, Pydantic v2, Pydantic Settings |
| Database | PostgreSQL 16, pgvector, SQLAlchemy 2 async, Alembic |
| Async jobs | Celery, Redis |
| AI | OpenAI Python SDK, `text-embedding-3-small`, `gpt-4o-mini` by default |
| Authentication | PyJWT, Argon2, rotating refresh-token records |
| Frontend | Next.js 15 App Router, React 19, TypeScript, Tailwind CSS |
| PDF processing | pypdf, reportlab for deterministic test PDFs |
| Testing | pytest, pytest-asyncio, HTTPX, aiosqlite, Ruff |
| Runtime | Python 3.12 in Docker, Node 22 in Docker |

## Quick start with Docker Compose

### Prerequisites

- Docker Engine or Docker Desktop with Compose v2.
- An OpenAI API key.
- Free host ports `3000`, `8000`, `5432`, and `6379`, unless overridden in `.env`.

### 1. Configure the environment

PowerShell:

```powershell
Copy-Item .env.example .env
python -c "import secrets; print(secrets.token_hex(32))"
```

Bash:

```bash
cp .env.example .env
python -c "import secrets; print(secrets.token_hex(32))"
```

Put the generated value into `SECRET_KEY` and set `OPENAI_API_KEY` in `.env`. Do not commit `.env`.

### 2. Start the stack

```bash
docker compose up --build -d
```

The backend entrypoint applies `alembic upgrade head` automatically because Compose sets `RUN_MIGRATIONS=true`. The service waits for PostgreSQL and Redis health checks before starting.

Check service state and logs:

```bash
docker compose ps
docker compose logs -f backend celery-worker
```

### 3. Open the application

- Dashboard: <http://localhost:3000>
- API documentation: <http://localhost:8000/docs>
- Alternative API docs: <http://localhost:8000/redoc>
- API health: <http://localhost:8000/health/ready>
- Flower, when the monitoring profile is enabled: <http://localhost:5555/flower>

Create an account, upload a PDF, wait for processing to complete, and then open Chat.

### 4. Useful commands

```bash
docker compose ps
docker compose logs -f --tail=150
docker compose exec backend alembic current
docker compose exec backend pytest
docker compose --profile monitoring up -d flower
docker compose down
```

`docker compose down` keeps named volumes. `docker compose down -v` also deletes PostgreSQL, Redis, uploaded files, and installed frontend dependencies.

The Compose file is a **development** stack: it mounts source code, enables backend reload, and publishes database and Redis ports. Do not expose it directly to the public internet. See [docs/OPERATIONS.md](docs/OPERATIONS.md) for production guidance.

## Local development

### Prerequisites

- Python 3.12 or newer. Python 3.12 is recommended; the Docker image uses 3.12.
- Node.js 22 and npm.
- PostgreSQL 16 with the pgvector extension.
- Redis 7.
- OpenAI API key.

If Docker is available, PostgreSQL and Redis can be started without running the application containers:

```bash
docker compose up -d postgres redis
```

### Backend

```powershell
Copy-Item .env.example .env
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r backend\requirements-dev.txt
```

Edit `.env` so `DATABASE_URL` and Redis URLs point to `localhost`, then apply migrations and start the API:

```powershell
Set-Location backend
python -m alembic upgrade head
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

The initial migration runs `CREATE EXTENSION IF NOT EXISTS vector` and creates the HNSW index. The database user must be allowed to create the extension. The `pgvector/pgvector:pg16` image and the Compose database user support this.

Start a worker in a second terminal:

```powershell
Set-Location backend
celery -A app.workers.celery_app:celery_app worker --loglevel=INFO
```

On Windows, Celery generally requires the solo pool:

```powershell
celery -A app.workers.celery_app:celery_app worker --loglevel=INFO --pool=solo
```

Without a running worker, uploads remain in `pending` status and never become searchable.

### Frontend

```powershell
Set-Location frontend
npm ci
npm run dev
```

The default `BACKEND_URL` is `http://localhost:8000`, so no extra frontend environment variable is required for local development. Open <http://localhost:3000>.

### Local verification commands

```powershell
Set-Location backend
python -m pytest -q
python -m ruff check app tests alembic
python -m ruff format --check app tests alembic

Set-Location ..\frontend
npm.cmd run typecheck
npm.cmd run lint
npm.cmd run build
```

`npm.cmd` is used explicitly on Windows because PowerShell execution policy may block `npm.ps1`. Use `npm` on macOS and Linux.

## Configuration

All application settings are documented in `.env.example`. The most important values are:

| Variable | Default | Purpose |
|---|---|---|
| `ENVIRONMENT` | `development` | `development`, `test`, or `production`. Production enables stricter validation. |
| `SECRET_KEY` | none | JWT signing key. At least 32 characters. |
| `OPENAI_API_KEY` | none | Required for embeddings, chat, and summaries. |
| `OPENAI_BASE_URL` | OpenAI default | Optional OpenAI-compatible endpoint. |
| `OPENAI_CHAT_MODEL` | `gpt-4o-mini` | Chat and summarization model. |
| `OPENAI_EMBEDDING_MODEL` | `text-embedding-3-small` | Embedding model. |
| `EMBEDDING_DIMENSIONS` | `1536` | Must match the pgvector column and migration chain. |
| `DATABASE_URL` | local PostgreSQL | SQLAlchemy database URL. |
| `REDIS_URL` | `redis://localhost:6379/0` | Rate-limit store. |
| `CELERY_BROKER_URL` | Redis DB 0 | Celery broker. |
| `CELERY_RESULT_BACKEND` | Redis DB 1 | Celery result backend. |
| `CORS_ORIGINS` | `http://localhost:3000` | Comma-separated explicit origins. Wildcards are rejected. |
| `FRONTEND_BASE_URL` | `http://localhost:3000` | Base URL used in verification and password-reset links. |
| `EMAIL_BACKEND` | `console` | `console` or `smtp`; production rejects `console`. |
| `EMAIL_FROM` | `DocuMind AI <no-reply@documind.local>` | Sender for verification and reset messages. |
| `SMTP_HOST` / `SMTP_PORT` | unset / `587` | Required when `EMAIL_BACKEND=smtp`. |
| `SMTP_USERNAME` / `SMTP_PASSWORD` | unset | Set both or neither. |
| `SMTP_STARTTLS` | `true` | Use STARTTLS for SMTP delivery. |
| `EMAIL_VERIFICATION_REQUIRED` | `false` | Require a verified email before login. |
| `EMAIL_VERIFICATION_TOKEN_HOURS` | `24` | Verification-token lifetime. |
| `PASSWORD_RESET_TOKEN_MINUTES` | `60` | Password-reset token lifetime. |
| `TOKEN_CLEANUP_RETENTION_DAYS` | `30` | Retention for used or expired auth tokens before cleanup. |
| `TOKEN_CLEANUP_INTERVAL_SECONDS` | `86400` | Celery Beat interval for auth-token cleanup. |
| `COOKIE_SECURE` | `false` | Must be `true` in production and whenever SameSite is `none`. |
| `REFRESH_REUSE_GRACE_SECONDS` | `30` | Window in which racing refreshes retry instead of revoking the session family. |
| `MAX_UPLOAD_SIZE_MB` | `20` | Per-file upload limit. |
| `USER_STORAGE_QUOTA_MB` | `1024` | Total stored PDF bytes allowed per user. |
| `STORAGE_BACKEND` | `local` | `local` or `s3` for S3-compatible object storage. |
| `S3_BUCKET` / `S3_PREFIX` / `S3_REGION` | unset / empty / `us-east-1` | S3 destination; bucket is required when the backend is `s3`. |
| `S3_ENDPOINT_URL` | unset | Custom endpoint such as MinIO; leave unset for AWS S3. |
| `S3_ACCESS_KEY_ID` / `S3_SECRET_ACCESS_KEY` | unset | Optional static credentials. Set both or neither; otherwise the AWS credential chain is used. |
| `S3_USE_SSL` | `true` | TLS for S3 requests. |
| `MALWARE_SCANNING_ENABLED` | `false` | Enables ClamAV INSTREAM scanning before storage. |
| `CLAMAV_HOST` / `CLAMAV_PORT` | `localhost` / `3310` | ClamAV daemon endpoint. |
| `CLAMAV_TIMEOUT_SECONDS` | `10` | Scanner connection/read timeout; failures fail closed with `503`. |
| `CHUNK_SIZE` / `CHUNK_OVERLAP` | `1000` / `200` | Text chunking behavior. |
| `SEARCH_DEFAULT_TOP_K` | `5` | Default semantic-search result count. |
| `SEARCH_MIN_SIMILARITY` | `0.2` | Minimum cosine similarity accepted for search and RAG. |
| `RAG_MAX_CONTEXT_CHUNKS` | `8` | Maximum retrieved passages sent to the chat model. |
| `RAG_HISTORY_MESSAGES` | `8` | Prior conversation messages included for follow-ups. |
| `RAG_CITATION_VALIDATION_ENABLED` | `true` | Enables claim-level citation support checks. |
| `RAG_CITATION_MIN_SUPPORT` | `0.55` | Minimum claim/source evidence coverage required to keep a citation. |
| `RAG_CITATION_REQUIRE_CITATIONS` | `true` | Returns the insufficient-evidence response when an answer has no supported citation. |
| `RAG_CITATION_SEMANTIC_CHECK` | `false` | Adds a batched claim-embedding semantic check; costs one extra embedding request per answer. |
| `RAG_CITATION_SEMANTIC_THRESHOLD` | `0.45` | Minimum claim/source cosine similarity when semantic checking is enabled. |
| `SUMMARY_MAP_CHUNK_CHARS` | `12000` | Map-phase input grouping. |
| `SUMMARY_REDUCE_CHUNK_CHARS` | `20000` | Reduce-phase grouping. |
| `SUMMARY_STUCK_AFTER_SECONDS` | `1800` | Age after which a pending or processing summary is recovered. |
| `SUMMARY_RECOVERY_INTERVAL_SECONDS` | `300` | Celery Beat interval for summary recovery. |
| `RATE_LIMIT_ENABLED` | `true` | Enables endpoint-specific Redis rate limits. |

Production validation requires:

- `ENVIRONMENT=production`
- `DEBUG=false`
- `COOKIE_SECURE=true`
- A non-placeholder `SECRET_KEY`
- Explicit `CORS_ORIGINS` without wildcards
- HTTPS at the edge
- `EMAIL_BACKEND=smtp` with a reachable SMTP host and complete credentials
- A non-empty `S3_BUCKET` when `STORAGE_BACKEND=s3`, with matching credentials and optional prefix
- A reachable ClamAV daemon when `MALWARE_SCANNING_ENABLED=true`; scanning fails closed

If the embedding model returns a different vector dimension, update the database column, HNSW index, `EMBEDDING_DIMENSIONS`, and the Alembic migration together, then reprocess every document. Changing `EMBEDDING_DIMENSIONS` alone is not sufficient.

## API overview

All application endpoints are under `/api/v1` except health and documentation routes. Authentication accepts the `documind_access` cookie or an `Authorization: Bearer <access-token>` header.

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/v1/auth/register` | Create an account and issue cookies. |
| `POST` | `/api/v1/auth/login` | Authenticate and issue cookies. |
| `POST` | `/api/v1/auth/refresh` | Rotate refresh token and issue a new access token. |
| `POST` | `/api/v1/auth/logout` | Revoke the current refresh token and clear cookies. |
| `GET` | `/api/v1/auth/me` | Return the current user. |
| `PATCH` | `/api/v1/auth/me` | Update the current user's name. |
| `POST` | `/api/v1/auth/change-password` | Change password and rotate sessions. |
| `POST` | `/api/v1/auth/verify-email` | Consume an email-verification token. |
| `POST` | `/api/v1/auth/resend-verification` | Send a fresh verification message to the current user. |
| `POST` | `/api/v1/auth/password-reset/request` | Send a password-reset message without revealing account existence. |
| `POST` | `/api/v1/auth/password-reset/confirm` | Set a new password from a reset token. |
| `POST` | `/api/v1/auth/delete-account` | Delete the authenticated account after password confirmation. |
| `POST` | `/api/v1/documents/upload` | Upload a PDF as multipart form data. |
| `GET` | `/api/v1/documents` | List documents with filters and pagination. |
| `GET` | `/api/v1/documents/stats` | Return dashboard statistics. |
| `GET` | `/api/v1/documents/{id}` | Return document detail. |
| `GET` | `/api/v1/documents/{id}/status` | Poll processing and summary status. |
| `DELETE` | `/api/v1/documents/{id}` | Delete a document and its chunks. |
| `GET` | `/api/v1/documents/{id}/summary` | Return the cached, pending, or failed summary state. |
| `POST` | `/api/v1/documents/{id}/summarize` | Queue a summary job (`202`) or return a cached summary (`200`). |
| `POST` | `/api/v1/search` | Run semantic search. |
| `POST` | `/api/v1/chat` | Ask a grounded question and persist the conversation. |
| `POST` | `/api/v1/chat/stream` | Stream `meta`/`delta`/`done`/`error` SSE events; `done` contains the validated answer. |
| `POST` | `/api/v1/conversations` | Create an empty conversation. |
| `GET` | `/api/v1/conversations` | List conversations. |
| `GET` | `/api/v1/conversations/{id}` | Return messages and citations. |
| `DELETE` | `/api/v1/conversations/{id}` | Delete a conversation. |
| `GET` | `/health/live` | Liveness probe. |
| `GET` | `/health/ready` | Database readiness probe. |
| `GET` | `/openapi.json` | OpenAPI schema. |

Errors use a consistent envelope:

```json
{
  "error": {
    "code": "not_found",
    "message": "Document not found",
    "request_id": "f4bf7f17-01c5-4a1b-bb11-b0c1a49f3172"
  }
}
```

### API example

```bash
curl -c cookies.txt -H "Content-Type: application/json" \
  -d '{"email":"alex@example.com","password":"SecurePass123","full_name":"Alex Morgan"}' \
  http://localhost:8000/api/v1/auth/register

curl -b cookies.txt -c cookies.txt \
  -F "file=@./document.pdf" \
  http://localhost:8000/api/v1/documents/upload

curl -b cookies.txt -H "Content-Type: application/json" \
  -d '{"question":"What are the payment terms?","document_ids":["DOCUMENT_UUID"]}' \
  http://localhost:8000/api/v1/chat
```

Replace `DOCUMENT_UUID` with an ID returned by the upload or list endpoint. By default, documents require the Celery worker and OpenAI access to become searchable.

## Processing pipeline

```text
upload
  -> validate PDF and spool to a temporary file
  -> store under a user/document-specific key
  -> create document row (uploaded)
  -> enqueue Celery task
  -> document marked pending
  -> worker claims the document atomically
  -> extract text page by page
  -> chunk with overlap while retaining page numbers
  -> embed chunks in batches
  -> replace document chunks transactionally
  -> document completed
```

Retryable failures include AI provider outages and temporary storage failures. Non-retryable cases include unreadable or textless PDFs. Celery retries up to three times with exponential backoff. Duplicate task delivery is guarded by the document status claim.

## Testing

Backend tests use an isolated SQLite database and local test uploads. AI providers and the task dispatcher are replaced with deterministic fakes, so tests do not require OpenAI, Redis, PostgreSQL, or a Celery worker.

```powershell
Set-Location backend
python -m pytest -q
python -m pytest --cov=app --cov-report=term-missing
python -m ruff check app tests alembic
python -m ruff format --check app tests alembic
```

Covered areas include:

- Registration/login, cookie issuance, refresh rotation, concurrent-refresh grace, reuse detection, logout, profile, and password changes.
- Upload validation, traversal rejection, size and per-user quota limits, local/S3 storage behavior, optional malware scanning, ownership, filtering, pagination, sorting, stats, and deletion.
- PDF extraction, OCR fallback, chunking, embedding, idempotency, failure handling, stuck-document recovery, and document status transitions.
- Semantic search, similarity thresholds, filters, and cross-user isolation.
- RAG citations, claim-level source validation, unsupported-claim refusal, historical citation availability, conversation persistence, history handling, weak-context shortcuts, and prompt-injection boundaries.
- Chat streaming events, final validated answers, cancellation, provider failures, and persistence.
- Asynchronous summary dispatch, attempt ownership, status polling, failure handling, stuck-job recovery, map-reduce behavior, and long-summary reduction progress.
- Email verification, password reset, account deletion, auth-token cleanup, and secure email logging.
- Health checks, error envelopes, request IDs, CORS, rate limiting, and OpenAPI route registration.
- Configuration validation for CORS wildcards and production security settings.
- RAG evaluation metrics, including retrieval hit rate, MRR, citation precision/recall, unsupported-citation rate, claim support through API validation metadata, and refusal accuracy.

### RAG benchmark

`backend/evals/dataset.json` contains 110 questions across 10 synthetic policy documents. Every answerable question has a reference answer, expected document/page, and keywords verified against that source page; 10 additional questions are deliberately unanswerable.

Validate the dataset without starting the API:

```powershell
Set-Location backend
python -m evals.rag_eval --validate-only
```

Run retrieval against the integration stack with deterministic mock embeddings:

```powershell
python -m evals.rag_eval --base-url http://localhost:8001 --retrieval-only --top-k 8 --output evals/latest_retrieval_mock.json
```

The checked-in mock report shows 96.0% hit@8 and 0.804 MRR over 100 answerable questions. This exercises the real upload, PostgreSQL/pgvector, chunking, and retrieval path, but the mock embeddings are not a production quality measurement. A full answer evaluation requires a real chat model:

```powershell
python -m evals.rag_eval --base-url http://localhost:8001 --top-k 8
```

Frontend verification:

```powershell
Set-Location frontend
npm.cmd run typecheck
npm.cmd run lint
npm.cmd run build
npx.cmd playwright test
```

The default test suite intentionally uses SQLite for speed and portability. The integration suites below execute the PostgreSQL/pgvector query path, migrations, Redis, Celery, and OCR against real containers. The Playwright command requires the running stack and a compatible browser; set `PLAYWRIGHT_CHANNEL=chrome` to use a locally installed Chrome.

### Infrastructure integration tests

Prerequisites: Docker Engine/Desktop is running, `.env` is present, and the configured host ports are free. The integration overlay uses the deterministic mock AI service, disables rate limits, and makes no paid OpenAI requests.

```bash
docker compose -f docker-compose.yml -f docker-compose.integration.yml up -d --build postgres redis mock-ai backend celery-worker celery-beat
docker compose -f docker-compose.yml -f docker-compose.integration.yml exec -T backend python tests/integration/run_suite.py
```

The runner creates a `<database>_test` database, drops and recreates only that test schema, applies every Alembic migration from empty, and runs the PostgreSQL/pgvector and Docker/Celery suites. It never migrates or truncates the application database. Use `--suite postgres` or `--suite docker` to run one suite.

To verify worker restart and recovery, restart the worker and rerun the Docker/Celery suite:

```bash
docker compose -f docker-compose.yml -f docker-compose.integration.yml restart celery-worker
docker compose -f docker-compose.yml -f docker-compose.integration.yml exec -T backend python tests/integration/run_suite.py --suite docker
```

Stop the stack with `docker compose -f docker-compose.yml -f docker-compose.integration.yml down`. Add `-v` only when you also want to delete PostgreSQL data, Redis data, and uploads.

### PostgreSQL smoke test

After starting the development stack:

```bash
docker compose exec backend alembic current
docker compose exec postgres psql -U documind -d documind -c '\dx vector'
docker compose exec postgres psql -U documind -d documind -c '\d document_chunks'
docker compose exec backend python -c "import urllib.request; print(urllib.request.urlopen('http://localhost:8000/health/ready').read().decode())"
```

Then use the dashboard to upload a PDF, wait for `completed`, run search, ask a question, and generate both summary styles.

## Security notes

- Auth tokens are stored in `HttpOnly` cookies, not browser local storage.
- Browser state-changing requests go through a same-origin Next.js proxy that rejects cross-origin mutations and forwards auth cookies.
- Access tokens are short-lived by default; refresh tokens rotate on every use.
- Refresh-token values are stored as SHA-256 hashes, never plaintext.
- Document and conversation queries are always scoped to the authenticated owner.
- Prompt retrieval is treated as untrusted content and delimited as such for the model.
- Rate limits are applied to auth, upload, search, chat, and summarization paths.
- The API sets `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, and `Permissions-Policy`.
- Production requires secure cookies and explicit CORS origins.

For internet-facing deployment, also use TLS, a reverse proxy, private database/Redis networks, secret management, S3-compatible storage or a shared upload volume, malware scanning for uploads, and centralized log monitoring.

## Project structure

```text
.
├── backend/
│   ├── app/
│   │   ├── ai/              # Provider interfaces, OpenAI provider, prompts
│   │   ├── api/             # Dependencies, cookies, errors, v1 endpoints
│   │   ├── core/            # Settings, security, logging, middleware, rate limits
│   │   ├── db/              # SQLAlchemy base, engine, portable vector type
│   │   ├── models/          # Users, documents, chunks, conversations, tokens
│   │   ├── repositories/    # Ownership-aware persistence queries
│   │   ├── schemas/         # Pydantic request/response models
│   │   ├── services/        # Auth, accounts, uploads, processing, search, RAG, summaries
│   │   ├── utils/           # PDF text/chunking and upload helpers
│   │   └── workers/         # Celery application, processing, summary, and cleanup tasks
│   ├── alembic/             # pgvector, account-token, and async-summary migrations
│   ├── evals/               # Verified RAG benchmark dataset, harness, and report
│   ├── tests/               # Backend test suite and fakes
│   ├── Dockerfile
│   ├── docker-entrypoint.sh
│   └── requirements*.txt
├── frontend/
│   ├── app/                 # App Router pages, layouts, and API proxy
│   ├── components/          # Shared UI components
│   ├── lib/                 # Typed API client, types, formatting utilities
│   ├── middleware.ts        # Cookie-presence route protection
│   └── Dockerfile
├── docs/
│   ├── ARCHITECTURE.md
│   └── OPERATIONS.md
├── docker-compose.yml
├── .env.example
├── Makefile
├── CONTRIBUTING.md
└── README.md
```

## Known limitations

- Local filesystem storage is used by default. Horizontal backend scaling requires shared local storage or `STORAGE_BACKEND=s3`.
- Storage quotas are enforced per user across all stored PDFs; plan-based quota tiers are not implemented.
- The application only accepts PDFs. Scanned pages are processed with Tesseract when OCR is enabled and the configured language packs are installed; OCR quality depends on scan quality, and the configured page/time limits still apply.
- Citation validation is deterministic by default. Heavily paraphrased claims may be refused unless `RAG_CITATION_SEMANTIC_CHECK=true`; it is not a formal entailment proof. Any unsupported cited claim refuses the whole answer rather than returning partial support.
- Chat streaming uses a POST fetch with a `ReadableStream` client rather than `EventSource`. Reverse proxies and gateways must not buffer SSE responses; the API sends `Cache-Control: no-cache, no-transform` and `X-Accel-Buffering: no`.
- Document summaries are Celery jobs. `POST /documents/{id}/summarize` returns `202` when queued (or `200` for a cached summary), and clients poll `GET /documents/{id}/summary`. Summaries require a running worker, and stuck-job recovery requires Celery Beat.
- Email verification is optional by default (`EMAIL_VERIFICATION_REQUIRED=false`). Production requires SMTP; the dashboard does not yet expose dedicated verification, password-reset, or account-deletion screens, so those backend endpoints are API-only.
- Auth-token cleanup depends on the periodic Celery Beat cleanup task.
- Malware scanning is optional and disabled by default. When enabled, it requires a reachable ClamAV daemon and fails closed.
- A worker process killed without raising a Python exception can leave a document in `processing`; a periodic Celery Beat task recovers stale rows, but operator intervention is still required if Beat is not running.
- Deleting a document removes its file, chunks, and searchability, but citations in historical chat messages are retained as a record and can point to the deleted document.
- Rate limits are per client IP and depend on correct trusted-proxy configuration when deployed behind a gateway.
- Refresh-token history has no scheduled retention cleanup.
- The default suite uses SQLite; PostgreSQL/pgvector, Redis, Celery, and Alembic are covered by opt-in integration tests that require the Compose stack.
- Playwright browser E2E tests require the running Compose stack and an installed Chromium (`npx playwright install chromium`).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Run the backend tests and frontend verification commands before submitting changes. Never commit `.env`, API keys, uploaded documents, or database files.
