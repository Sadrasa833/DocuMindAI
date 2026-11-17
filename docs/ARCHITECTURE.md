# Architecture

## System overview

DocuMind AI is split into four runtime components:

1. **Next.js frontend** for authentication, document management, summaries, chat, and conversation history.
2. **FastAPI backend** for API validation, authorization, orchestration, search, RAG, and summaries.
3. **Celery worker** for CPU- and network-heavy PDF processing and embedding generation.
4. **PostgreSQL with pgvector and Redis** for durable state and asynchronous messaging.

```text
                         +----------------------+
                         |      Browser         |
                         +----------+-----------+
                                    |
                         same-origin HTTP, cookies
                                    |
                         +----------v-----------+
                         | Next.js :3000        |
                         | pages + API proxy    |
                         +----------+-----------+
                                    |
                          /api/proxy/v1/* server-side
                                    |
                         +----------v-----------+
                         | FastAPI :8000        |
                         | auth, CRUD, search,  |
                         | RAG, summaries       |
                         +--+--------+--------+-+
                            |        |        |
                 SQLAlchemy |        | Redis  | Celery
                            |        |        |
                 +----------v--+  +--v-----+  +------------------+
                 | PostgreSQL  |  | Redis  |  | Celery worker    |
                 | + pgvector  |  | broker |  | PDF -> chunks -> |
                 +-------------+  +--------+  | embeddings       |
                                             +--------+---------+
                                                      |
                                                      v
                                               +--------------+
                                               | OpenAI API   |
                                               +--------------+
```

## Backend layering

| Layer | Location | Responsibility |
|---|---|---|
| API | `backend/app/api` | Routing, dependencies, cookies, error mapping, middleware |
| Schemas | `backend/app/schemas` | Request validation and response contracts |
| Services | `backend/app/services` | Business workflows and transaction decisions |
| Repositories | `backend/app/repositories` | Reusable, ownership-aware database queries |
| Models | `backend/app/models` | SQLAlchemy ORM entities |
| AI | `backend/app/ai` | Provider abstractions, OpenAI implementation, prompts |
| Workers | `backend/app/workers` | Celery application and document-processing tasks |
| Core | `backend/app/core` | Settings, security, logging, middleware, rate limiting |

Routes are intentionally thin. They validate input, obtain dependencies, call services, and serialize results. Services own transaction boundaries. Repositories do not commit.

## Data model

```text
users
  1 ─── * documents
  1 ─── * conversations
  1 ─── * refresh_tokens

documents
  1 ─── * document_chunks

conversations
  1 ─── * messages
```

Key ownership fields:

- `documents.user_id`
- `document_chunks.user_id` and `document_chunks.document_id`
- `conversations.user_id`
- `messages.conversation_id`

Documents and conversations cascade from users. Chunks cascade from documents. Messages cascade from conversations. The application still checks ownership explicitly instead of relying on cascade behavior.

### Tables

| Table | Purpose |
|---|---|
| `users` | Account identity, Argon2 password hash, active flag |
| `refresh_tokens` | Hashed refresh tokens, expiry, revocation, user agent, IP |
| `documents` | File metadata, processing state, counts, summary cache |
| `document_chunks` | Chunk text, page number, embedding vector |
| `conversations` | Chat session title and timestamps |
| `messages` | User/assistant messages and citation JSON |

`document_chunks.embedding` uses a portable SQLAlchemy type decorator:

- PostgreSQL: native `vector(1536)`.
- SQLite tests: JSON storage with in-process cosine similarity.

The production migration creates an HNSW index using `vector_cosine_ops`.

## Authentication flow

```text
register/login
   -> validate credentials
   -> issue short-lived access JWT
   -> issue opaque-looking JWT refresh token with jti
   -> store only SHA-256(refresh token) in PostgreSQL
   -> set both tokens in HttpOnly cookies
```

Access tokens default to 30 minutes. Refresh tokens default to 7 days.

On refresh:

1. Verify the JWT signature, issuer, expiry, and token type.
2. Look up the refresh-token hash with a row lock.
3. If the token is unknown, revoke all active tokens for that user.
4. Otherwise revoke the used token and issue a new access/refresh pair.

The reuse rule limits replay of a stolen rotated token. Password changes revoke all active refresh tokens and issue a fresh session.

The browser client treats a 401 on a normal API call as an access-token expiry signal, calls `/auth/refresh`, and retries once. If refresh fails, it calls `/auth/logout` to clear stale cookies before redirecting to login. This prevents a redirect loop when both cookies are stale.

## Document ingestion

```text
POST /documents/upload
   -> validate extension, MIME type, PDF signature, parseable PDF, and size
   -> stream to a temporary file while hashing
   -> lock the user row and enforce the per-user storage quota
   -> optionally run ClamAV INSTREAM scanning
   -> copy into storage key <user_id>/<document_id>.pdf
   -> insert document row
   -> commit row
   -> enqueue Celery task
   -> mark document pending
```

The upload is spooled to disk rather than held entirely in memory. The temporary file is removed in all paths. The storage backend resolves keys with traversal checks. Local storage writes via a temporary file plus atomic replace; the S3-compatible backend streams the file through `boto3` using the same key layout and an optional prefix.

The worker:

1. Atomically claims documents in `uploaded`, `pending`, or `failed` state.
2. Transitions the row to `processing`.
3. Reads the stored PDF.
4. Extracts selectable text with page numbers using pypdf and OCRs pages without selectable text when OCR is enabled.
5. Normalizes text and builds sentence-aware overlapping chunks.
6. Embeds chunks in configured batches.
7. Replaces all chunks for the document in the current transaction.
8. Marks the document `completed` with page/chunk counts and timestamps.

If a task is delivered twice, the status claim prevents a second worker from reprocessing a completed or currently processing document.

### Failure handling

- Unreadable, encrypted, or empty PDFs fail permanently with a user-facing error.
- Pages without selectable text are OCR'd when OCR is enabled; PDFs with no usable text after OCR still fail with a user-facing error.
- Temporary OpenAI, storage, or unexpected failures are retried up to three times with bounded exponential delays.
- Before a retry, the document is returned to `pending` so the next attempt can claim it.
- After retries are exhausted, the document is marked `failed`.
- A queue enqueue failure immediately marks the upload `failed` with a retry-later message.

A worker process that is killed without raising a Python exception can leave a row in `processing`. A periodic Celery Beat task finds rows stale beyond `PROCESSING_STUCK_AFTER_SECONDS` and re-enqueues them. If Beat is not running, operator recovery is still required.

## Search

`SearchService` validates selected document ownership and completion state, embeds the query, performs vector search, and filters by `SEARCH_MIN_SIMILARITY`.

On PostgreSQL:

```sql
SELECT document_chunks.*, documents.original_filename,
       document_chunks.embedding <=> :query_embedding AS distance
FROM document_chunks
JOIN documents ON documents.id = document_chunks.document_id
WHERE document_chunks.user_id = :user_id
  AND documents.status = 'completed'
  AND document_chunks.document_id IN (:allowed_document_ids)
ORDER BY distance
LIMIT :top_k;
```

Similarity is exposed as `1 - cosine_distance`. The HNSW index accelerates this ordering. Search is always scoped to the authenticated user; optional document IDs are validated before querying.

## RAG flow

```text
chat request
   -> resolve or create conversation owned by the current user
   -> load recent messages (RAG_HISTORY_MESSAGES)
   -> embed the question
   -> retrieve top RAG_MAX_CONTEXT_CHUNKS completed chunks
   -> filter by minimum similarity
   -> if no hits: return a deterministic insufficient-context answer
   -> otherwise call the chat model with untrusted delimited sources
   -> validate label existence, then numeric/negation/lexical claim support
   -> optionally verify claim/source similarity with batched embeddings
   -> remove unsupported citations; refuse if no supported citation remains
   -> persist user and assistant messages plus verified citations
   -> return answer, conversation_id, citations
```

Prompt design:

- The system prompt instructs the model to answer only from supplied sources.
- Retrieved content is explicitly marked as untrusted data and separated from instructions.
- Every source has a stable label, document name, page, and content.
- Citations map source labels to chunk IDs, document IDs, document names, and page numbers.
- Fabricated or out-of-range labels are removed before claim validation.
- Each cited claim is checked against its cited chunks with number, negation, and token-coverage checks; unrelated citations are removed.
- When `RAG_CITATION_SEMANTIC_CHECK=true`, claim embeddings add a batched semantic-similarity check. This is off by default to avoid extra API calls.
- Multiple sources can jointly support a multi-part claim, while citations that do not add evidence are dropped.
- If no supported citation remains and citations are required, the deterministic insufficient-evidence response is returned.
- Conversation history is used to resolve references such as "it" or "that section"; history is not treated as evidence.
- Model temperature is `0.0`.
- Weak retrieval returns a fixed response without calling the model.

## Summaries

Summarization is synchronous within the API request.

- A summary is cached by document and style.
- `force=true` regenerates it.
- Chunks are grouped under `SUMMARY_MAP_CHUNK_CHARS`.
- A single group uses a direct summary prompt.
- Multiple groups use map-reduce: summarize each group, then reduce partial summaries.
- If no reduction batch can shrink a list, summaries are paired forcibly so the loop always makes progress.
- The final summary is stored on the document with its style and timestamp.

## Frontend architecture

- Next.js App Router renders public auth pages and protected dashboard pages.
- Middleware only checks for cookie presence and redirects unauthenticated users away from protected routes. It does not decode JWTs.
- The server proxy at `/app/api/proxy/[...path]/route.ts` forwards only the API prefix, cookies, selected headers, and response `Set-Cookie` headers.
- The proxy rejects unsafe cross-origin browser mutations using `Origin` and `Sec-Fetch-Site`.
- The client API wrapper centralizes error parsing, one-time refresh/retry, and login redirection.
- Uploads use XMLHttpRequest to expose progress while retaining cookie credentials.
- No access or refresh token is stored in local storage or exposed to React state.

## Design decisions

### Async processing

PDF parsing and embeddings can take seconds to minutes. Moving this work to Celery keeps API latency bounded and allows independent worker scaling.

### Cookie authentication

Cookies avoid token access from JavaScript and fit a first-party dashboard. SameSite cookies plus same-origin proxying reduce CSRF exposure. The proxy origin check provides defense in depth.

### PostgreSQL + pgvector

A single durable database holds relational ownership data and vector search state. This avoids a separate vector database for the current scale while retaining indexed cosine search.

### Fake-first testing

External AI services are nondeterministic, slow, and billed per request. Provider interfaces and deterministic fakes make the suite fast and reliable while exercising all application logic around the providers.

### Storage and upload security

The storage protocol isolates upload persistence behind local and S3-compatible implementations. Local disk remains the development default; S3 mode supports AWS S3, MinIO, and compatible services, and uses the same `<user_id>/<document_id>.pdf` key layout. Per-user quotas are enforced while holding a row lock on the user, so concurrent uploads cannot both consume the last remaining bytes. Quota checks use the sum of stored document sizes.

PDF uploads are validated by extension, declared MIME type, `%PDF-` signature, and an actual pypdf parse before persistence. Filenames are never used as storage paths. ClamAV scanning is optional and fail-closed: when enabled, an unreachable or malformed scanner response rejects the upload.

## Extension points

- Add workspace/organization-scoped storage quotas.
- Move summaries to Celery jobs with progress polling.
- Add SSE or WebSocket streaming for chat responses.
- Add organization/workspace tenancy above user ownership.
- Add a cross-encoder reranker before RAG prompt construction.
- Add detailed usage accounting and plan-based quota tiers.
