# DocuMind AI

DocuMind AI is a production-oriented, multi-user document intelligence application. It ingests PDFs, processes them asynchronously, indexes their contents with pgvector, and provides citation-grounded question answering, semantic search, and document summaries.

The repository contains a FastAPI backend, a Next.js dashboard, a Celery worker, and a Docker Compose development stack.

## Verification status

Verified in this workspace:

- Backend: **86 tests passed and 9 integration tests were skipped** on Python 3.14 with SQLite and mocked AI providers.
- Backend: **90% line coverage** across `app/`.
- Backend: Ruff lint and formatting checks pass.
- Frontend: TypeScript, ESLint, and the Next.js production build pass.
- Docker Compose configuration parses successfully with the example environment.

Not verified in this workspace:

- The Docker Compose stack was not started because the Docker daemon was unavailable.
- PostgreSQL/pgvector, Redis, Celery, Alembic, and container health checks were not exercised against running services; the 9 integration tests requiring them were skipped.
- No live OpenAI request was made.
- Playwright browser E2E tests were not executed because the running stack and bundled browser were unavailable.
- Real Tesseract OCR was not exercised outside the container; OCR unit tests use a fake engine.

See [Testing](#testing) and [Known limitations](#known-limitations) for details.

## Features

### Documents

- Multi-user PDF upload with authentication and ownership isolation.
- PDF signature, extension, MIME type, size, and path-safety validation.
- 20 MB upload limit by default; configurable up to 100 MB.
- Text extraction with page numbers, OCR fallback for scanned pages, and overlapping chunking.
- Asynchronous processing through Celery and Redis.
- Processing status, retry behavior, error reporting, and idempotent task handling.
- Document filtering, search, sorting, pagination, statistics, deletion, and polling.
- Short and detailed summaries, including map-reduce summarization for long documents.

### Search and AI

- OpenAI embeddings with batched requests and retry/backoff behavior.
- pgvector cosine similarity search with an HNSW index in PostgreSQL.
- Minimum-similarity filtering to avoid answering from irrelevant chunks.
- Optional search scope across selected documents.
- Grounded RAG answers with source citations containing document, page, and chunk identifiers.
- Conversation persistence and recent-message context for follow-up questions.
- Prompt-injection boundaries that mark retrieved content as untrusted data.
- Graceful "insufficient context" response without calling the language model when retrieval is weak.

### Security and operations

- Argon2 password hashing.
- Short-lived JWT access tokens and rotating refresh tokens in `HttpOnly` cookies.
- Refresh-token reuse detection that revokes a user's active sessions.
- CSRF-conscious same-origin proxy for browser mutations.
- Redis-backed rate limiting with an in-memory fallback.
- Request IDs, structured logging support, security headers, and consistent error envelopes.
- Liveness and readiness probes.
- Docker health checks and automatic migrations on backend startup.

### Dashboard

- Registration, login, and logout.
- Dashboard statistics and recent documents.
- Upload progress, polling, document filtering, and sorting.
- Document detail, summarized content, and status handling.
- Citation-aware chat and conversation history.
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
| `EMBEDDING_DIMENSIONS` | `1536` | Must match the pgvector column and initial migration. |
| `DATABASE_URL` | local PostgreSQL | SQLAlchemy database URL. |
| `REDIS_URL` | `redis://localhost:6379/0` | Rate-limit store. |
| `CELERY_BROKER_URL` | Redis DB 0 | Celery broker. |
| `CELERY_RESULT_BACKEND` | Redis DB 1 | Celery result backend. |
| `CORS_ORIGINS` | `http://localhost:3000` | Comma-separated explicit origins. Wildcards are rejected. |
| `COOKIE_SECURE` | `false` | Must be `true` in production and whenever SameSite is `none`. |
| `REFRESH_REUSE_GRACE_SECONDS` | `30` | Window in which racing refreshes retry instead of revoking the session family. |
| `MAX_UPLOAD_SIZE_MB` | `20` | Upload limit. |
| `CHUNK_SIZE` / `CHUNK_OVERLAP` | `1000` / `200` | Text chunking behavior. |
| `SEARCH_DEFAULT_TOP_K` | `5` | Default semantic-search result count. |
| `SEARCH_MIN_SIMILARITY` | `0.2` | Minimum cosine similarity accepted for search and RAG. |
| `RAG_MAX_CONTEXT_CHUNKS` | `8` | Maximum retrieved passages sent to the chat model. |
| `RAG_HISTORY_MESSAGES` | `8` | Prior conversation messages included for follow-ups. |
| `SUMMARY_MAP_CHUNK_CHARS` | `12000` | Map-phase input grouping. |
| `SUMMARY_REDUCE_CHUNK_CHARS` | `20000` | Reduce-phase grouping. |
| `RATE_LIMIT_ENABLED` | `true` | Enables endpoint-specific Redis rate limits. |

Production validation requires:

- `ENVIRONMENT=production`
- `DEBUG=false`
- `COOKIE_SECURE=true`
- A non-placeholder `SECRET_KEY`
- Explicit `CORS_ORIGINS` without wildcards
- HTTPS at the edge

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
| `POST` | `/api/v1/documents/upload` | Upload a PDF as multipart form data. |
| `GET` | `/api/v1/documents` | List documents with filters and pagination. |
| `GET` | `/api/v1/documents/stats` | Return dashboard statistics. |
| `GET` | `/api/v1/documents/{id}` | Return document detail. |
| `GET` | `/api/v1/documents/{id}/status` | Poll processing status. |
| `DELETE` | `/api/v1/documents/{id}` | Delete a document and its chunks. |
| `POST` | `/api/v1/documents/{id}/summarize` | Generate or retrieve a summary. |
| `POST` | `/api/v1/search` | Run semantic search. |
| `POST` | `/api/v1/chat` | Ask a grounded question and persist the conversation. |
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
- Upload validation, traversal rejection, size limits, ownership, filtering, pagination, sorting, stats, and deletion.
- PDF extraction, OCR fallback, chunking, embedding, idempotency, failure handling, stuck-document recovery, and document status transitions.
- Semantic search, similarity thresholds, filters, and cross-user isolation.
- RAG citations, conversation persistence, history handling, weak-context shortcuts, and prompt-injection boundaries.
- Summary caching, force regeneration, map-reduce behavior, and long-summary reduction progress.
- Health checks, error envelopes, request IDs, CORS, rate limiting, and OpenAPI route registration.
- Configuration validation for CORS wildcards and production security settings.
- RAG evaluation metrics, including retrieval hit rate and MRR.

Frontend verification:

```powershell
Set-Location frontend
npm.cmd run typecheck
npm.cmd run lint
npm.cmd run build
npx.cmd playwright test
```

The test suite intentionally uses SQLite for speed and portability. It does not execute the PostgreSQL/pgvector query path or migrations. Run the Compose smoke test below before a production release. The Playwright command requires the stack to be running and a compatible browser; set `PLAYWRIGHT_CHANNEL=chrome` to use a locally installed Chrome.

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

For internet-facing deployment, also use TLS, a reverse proxy, private database/Redis networks, secret management, malware scanning for uploads, and centralized log monitoring.

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
│   │   ├── services/        # Auth, uploads, processing, search, RAG, summaries
│   │   ├── utils/           # PDF text/chunking and upload helpers
│   │   └── workers/         # Celery application and processing tasks
│   ├── alembic/             # Initial pgvector migration
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

- Local filesystem storage is used by default. Horizontal backend scaling requires shared storage or an S3-compatible adapter.
- The application only accepts PDFs. Scanned pages are processed with Tesseract when OCR is enabled and the configured language packs are installed; OCR quality depends on scan quality, and the configured page/time limits still apply.
- Chat responses are not streamed; the request waits for the full model response.
- Document summaries are generated synchronously during the HTTP request and can be slow for large documents.
- There is no email verification, password-reset email flow, account deletion, or storage quota enforcement.
- Uploaded files are not virus-scanned.
- A worker process killed without raising a Python exception can leave a document in `processing`; a periodic Celery Beat task recovers stale rows, but operator intervention is still required if Beat is not running.
- Deleting a document removes its file, chunks, and searchability, but citations in historical chat messages are retained as a record and can point to the deleted document.
- Rate limits are per client IP and depend on correct trusted-proxy configuration when deployed behind a gateway.
- Refresh-token history has no scheduled retention cleanup.
- The automated suite uses SQLite; PostgreSQL/pgvector, Redis, Celery, Alembic, and Docker were not executed in this workspace.
- Playwright browser E2E tests are included but were not executed in this workspace because they require the running Compose stack and an installed browser.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Run the backend tests and frontend verification commands before submitting changes. Never commit `.env`, API keys, uploaded documents, or database files.
