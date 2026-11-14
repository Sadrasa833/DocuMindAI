# Contributing to DocuMind AI

## Development setup

Follow the local development section in [README.md](README.md). The minimum toolchain is:

- Python 3.12+
- Node.js 22+
- PostgreSQL 16 with pgvector
- Redis 7

Install backend dependencies:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r backend/requirements-dev.txt
```

PowerShell activation:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r backend\requirements-dev.txt
```

Install frontend dependencies:

```bash
cd frontend
npm ci
```

Use `npm.cmd` instead of `npm` on Windows if PowerShell blocks `npm.ps1`.

## Required checks

Run these before opening a pull request.

Backend:

```bash
cd backend
python -m pytest -q
python -m ruff check app tests alembic
python -m ruff format --check app tests alembic
```

Frontend:

```bash
cd frontend
npm run typecheck
npm run lint
npm run build
```

The backend suite uses SQLite and fake AI providers by default. If a change touches PostgreSQL-specific SQL, pgvector behavior, Alembic, Redis, or Celery, also run the Docker smoke tests in the README or document why they could not be run.

## Backend conventions

- Keep HTTP concerns in `app/api`, business logic in `app/services`, and persistence in `app/repositories`.
- Do not run database queries directly from route handlers when a service or repository is appropriate.
- Every user-owned query must filter by the authenticated user ID.
- Use the existing `AppError` hierarchy for expected API failures.
- Raise `AIProviderError` from providers so the API returns a structured `503`.
- Use async SQLAlchemy APIs throughout request and worker code.
- Return Pydantic schemas from endpoints; do not expose ORM objects directly.
- Add type hints and preserve `from __future__ import annotations`.
- Add concise comments only where the reason behind non-obvious logic is not apparent.
- Do not log tokens, passwords, raw uploaded content, or API keys.

### Tests

- Add or update tests for behavior changes.
- Prefer deterministic fakes from `tests/fakes.py`; do not call OpenAI in tests.
- Test ownership isolation for any new user-scoped resource.
- For processing changes, cover both success and failure/retry paths.
- Keep the test suite independent of Redis and PostgreSQL unless a test is explicitly marked for integration.
- New integration-only tests must be skipped by default when the required service is absent.

Example backend test command:

```bash
cd backend
python -m pytest tests/test_search_rag.py -q
```

### Database migrations

The project has an initial migration in `backend/alembic/versions/`. Generate future migrations against a running PostgreSQL/pgvector database:

```bash
cd backend
python -m alembic revision --autogenerate -m "describe the change"
python -m alembic upgrade head
```

Review generated migrations manually. In particular:

- Check pgvector column dimensions and HNSW indexes.
- Confirm foreign-key `ondelete` behavior matches relationships.
- Do not silently change enum values without checking existing rows.
- Keep migration downgrade behavior coherent.

Changing `EMBEDDING_DIMENSIONS` requires a schema migration, index rebuild, and complete re-embedding of existing documents. Do not change the setting alone.

## Frontend conventions

- Use the typed client in `lib/api.ts` instead of calling the backend directly.
- Browser requests must go through `/api/proxy/v1`; do not expose tokens to local storage.
- Keep pages as client components only when interactivity requires it.
- Preserve the existing visual language, spacing, and component patterns.
- Handle loading, empty, error, and success states for data-fetching screens.
- Use accessible labels and semantic controls.
- Do not add dependencies without a clear need and lockfile update.
- `next lint` is deprecated in Next.js 15 but remains the configured lint command in this project.

## API changes

When adding or changing an endpoint:

- Include it under `/api/v1` unless it is an infrastructure probe.
- Define request and response schemas in `app/schemas`.
- Verify CSRF/cookie behavior for browser mutations.
- Add rate limiting for expensive or abuse-prone operations.
- Update the endpoint table in the README.
- Add tests for authentication, ownership, validation, and error behavior.
- Confirm the OpenAPI schema is generated without warnings.

## Security expectations

- Never commit `.env`, credentials, private keys, tokens, uploaded files, or database dumps.
- Use dummy values in tests and examples.
- Keep cookies `HttpOnly`; production must use `COOKIE_SECURE=true`.
- Do not weaken origin checks, CORS restrictions, or ownership filters for convenience.
- Treat PDF content and model output as untrusted input.
- If adding a new external service, include timeouts, bounded retries, and failure handling.
- Report suspected vulnerabilities privately rather than opening a public exploit report.

## Pull request checklist

- [ ] Tests added or updated for changed behavior.
- [ ] Backend tests pass.
- [ ] Ruff lint and format checks pass.
- [ ] Frontend typecheck, lint, and build pass.
- [ ] Database migration included and reviewed, if schema changed.
- [ ] README or docs updated for user-visible or operational changes.
- [ ] No secrets, generated files, caches, or local environment files included.
- [ ] Docker/pgvector limitations are stated when relevant verification could not be run.

## Commit guidance

Use a concise, imperative commit message, for example:

```text
Add conversation history to RAG prompts
Fix refresh failure cookie cleanup
Document PostgreSQL migration workflow
```

Keep unrelated changes in separate commits where practical.
