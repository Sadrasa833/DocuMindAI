# Architecture

## System overview

DocuMind AI is split into four runtime components:

1. **Next.js frontend** for authentication, document management, summaries, chat, and conversation history.
2. **FastAPI backend** for API validation, authorization, orchestration, search, RAG, and streamed chat.
3. **Celery worker and Beat** for PDF processing, embeddings, summary jobs, stale-job recovery, and auth-token cleanup.
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
  1 ─── * account_tokens

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
| `users` | Account identity, Argon2 password hash, active flag, email-verification timestamp |
| `refresh_tokens` | Hashed refresh tokens, expiry, revocation, user agent, IP |
| `account_tokens` | Hashed single-use email-verification and password-reset tokens |
| `documents` | File metadata, processing state, counts, summary cache/status, active summary attempt |
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

Email verification and password reset use single-use tokens stored only as SHA-256 hashes in `account_tokens`. The raw token is delivered by the configured console or SMTP email backend and is consumed atomically with a row lock. Verification is optional by default. Password reset requests return the same `202` response whether or not the address exists, and a successful reset revokes active refresh tokens. Password-confirmed account deletion removes the user row, cascade-owned data, and stored files, then revokes sessions. A periodic Celery Beat task deletes expired and used tokens after `TOKEN_CLEANUP_RETENTION_DAYS`.

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
   -> remove unsupported citations; refuse the answer if any cited claim
      is unsupported or if citations are required but none remain
   -> persist user and assistant messages plus verified citations
   -> return answer, conversation_id, citations, and validation counts
```

Streaming uses the same validation path. `POST /chat/stream` emits `meta`,
`delta`, `done`, and `error` SSE events. The `done` event always carries the
final validated answer and citations, so a client replaces streamed text when
validation rewrites or refuses it. Conversation persistence happens only after
the final answer validates and uses a dedicated database session inside the
streaming generator. Disconnects stop generation before additional events are
sent.

Prompt design:

- The system prompt instructs the model to answer only from supplied sources.
- Retrieved content is explicitly marked as untrusted data and separated from instructions.
- Every source has a stable label, document name, page, and content.
- Citations map source labels to chunk IDs, document IDs, document names, and page numbers.
- Fabricated or out-of-range labels are removed before claim validation.
- Each cited claim is checked against its cited chunks with number, negation, and token-coverage checks. Negation is scoped to clauses so an unrelated negated sentence does not invalidate a supported claim, and negated claims require negated evidence.
- When `RAG_CITATION_SEMANTIC_CHECK=true`, claim embeddings add a batched semantic-similarity check. This is off by default to avoid extra API calls.
- Multiple sources can jointly support a multi-part claim, while citations that do not add evidence are dropped.
- Any unsupported cited claim makes the answer refuse. If citations are required but none remain, the deterministic insufficient-evidence response is also returned.
- Historical conversation reads resolve each citation's document availability dynamically. Stored citation metadata remains intact after deletion, and the UI renders unavailable citations without a link.
- Chat responses include aggregate supported/unsupported claim counts for evaluation and observability; streamed `done` events include the same metadata.
- Conversation history is used to resolve references such as "it" or "that section"; history is not treated as evidence.
- Model temperature is `0.0`.
- Weak retrieval returns a fixed response without calling the model.

## Summaries

Summarization runs as a Celery job rather than blocking the HTTP request.

1. `POST /documents/{id}/summarize` validates ownership and the completed document state, creates a unique attempt ID, marks the summary `pending`, and enqueues the job. It returns `202` with the current state.
2. If a completed summary already exists for the requested style and `force=false`, the endpoint returns `200` with the cached summary instead.
3. A worker atomically claims the attempt, marks it `processing`, loads chunks, and generates the summary.
4. The final summary is stored only if the worker still owns the active attempt, preventing a stale duplicate delivery from overwriting newer output.
5. `GET /documents/{id}/summary` returns `pending`, `processing`, `completed`, or `failed` with the summary and error message. Both document list/detail and status responses expose summary state for polling.

Chunks are grouped under `SUMMARY_MAP_CHUNK_CHARS`. A single group uses a direct summary prompt. Multiple groups use map-reduce: summarize each group, then reduce partial summaries. If no reduction batch can shrink a list, summaries are paired forcibly so the loop always makes progress.

A Celery Beat task finds summary attempts stale beyond `SUMMARY_STUCK_AFTER_SECONDS` and re-enqueues them. Queue failures mark the attempt failed with a retry-later message. The dashboard polls the summary endpoint and stops polling when the attempt completes or fails.

## Frontend architecture

- Next.js App Router renders public auth pages and protected dashboard pages.
- Middleware only checks for cookie presence and redirects unauthenticated users away from protected routes. It does not decode JWTs.
- The server proxy at `/app/api/proxy/[...path]/route.ts` forwards only the API prefix, cookies, selected headers, and response `Set-Cookie` headers.
- The proxy rejects unsafe cross-origin browser mutations using `Origin` and `Sec-Fetch-Site`.
- The client API wrapper centralizes error parsing, one-time refresh/retry, login redirection, and SSE parsing.
- Chat streaming uses `fetch` with a request body and reads the response as a `ReadableStream`, because `EventSource` cannot send POST bodies or custom headers. An `AbortController` cancels in-flight generation when the user starts another request. The streaming toggle is stored in local storage under `documind.chat.streaming`.
- The `done` stream event replaces any optimistic or partially streamed content with the final validated answer and citations. Stream errors roll back the optimistic assistant message.
- The document detail page polls `GET /documents/{id}/summary` until the summary job completes or fails. Poll generations are guarded so a stale poll cannot overwrite newer state.
- Unavailable citations render as non-link cards with the original document name and page.
- Uploads use XMLHttpRequest to expose progress while retaining cookie credentials.
- No access or refresh token is stored in local storage or exposed to React state.

## Design decisions

### Async processing

PDF parsing, embeddings, and summaries can take seconds to minutes. Moving this work to Celery keeps API latency bounded and allows independent worker scaling. Summary attempt IDs and conditional updates make duplicate or stale task delivery harmless. A single Beat scheduler handles stale document/summary recovery and auth-token cleanup.

### Optional streamed chat

Streaming improves perceived latency without changing the trust boundary: the same validation runs on the complete model response before anything is persisted, and the final `done` event carries the authoritative answer. Clients must still replace streamed text with that final value.

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
- Add a formal NLI/entailment model for citation verification.
- Add organization/workspace tenancy above user ownership.
- Add a cross-encoder reranker before RAG prompt construction.
- Add detailed usage accounting and plan-based quota tiers.
