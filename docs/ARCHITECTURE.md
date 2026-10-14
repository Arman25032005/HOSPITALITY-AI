# Architecture

Request flow (target): channel adapter → tenant/identity resolution → conversation service →
intent classification → LangGraph orchestration → retrieval or deterministic business service →
policy validation → verified response. The LLM proposes; application services decide.

## Decisions (Phase 1)

- **Sync SQLAlchemy 2.x + psycopg3** for simplicity and transactional clarity; revisit async if needed.
- **Tenancy:** every business table carries `organization_id` (and `hotel_id` where relevant);
  tenant checks happen in services, not prompts.
- **Inventory:** `room_inventory` (per room type per night) with a CHECK `booked <= total`; booking
  will use row locks (`SELECT ... FOR UPDATE`) in one transaction.
- **Idempotency:** unique `(organization_id, idempotency_key)` on reservations;
  unique `(conversation_id, external_message_id)` on messages for webhook dedupe.
- **Auth:** bcrypt + short-lived JWT carrying org and role; the user row is re-checked each request.
- **Secrets:** env vars only; `.env.example` has no real values.

## Decisions (RAG slice)

- **Location:** RAG code lives in `apps/api/app/rag` for now (not `packages/ai`) to avoid packaging
  overhead until the agent layer needs to share it.
- **Source of truth:** chunks, versions and activity live in Postgres. The vector index is a
  derived structure; retrieval re-verifies every hit (tenant, hotel, active, effective date,
  audience, injection flag) against the database, so a stale or leaky index cannot expose data.
- **Hybrid retrieval:** vector candidates (store) + full-text candidates (Postgres `tsvector` over
  stemmed tokens) fused with reciprocal rank fusion. Added because vector-only retrieval with the
  lexical dev embedder missed exact-word queries; measured in `evals/`.
- **Answers are extractive:** the concierge quotes whole sections with citations and abstains below
  a relevance gate. No generated facts. An LLM composer can wrap this later but must keep the gate.
- **Versioning:** `(hotel, doc_key, version)` unique; the new version is activated and the old
  deactivated in one transaction after indexing succeeds.
- **Pinecone:** one namespace per `(org, hotel)`; metadata filters mirror the SQL filters.
- **Not done:** conflicting-policy detection beyond versioning, OCR for scanned PDFs, raw file
  storage (extracted text is stored), async ingestion worker (ingestion runs inline in the request).

## Decisions (conversations and agents)

- **Graph:** `gate -> (pending confirmation | classify) -> specialist -> verify`, no cycles,
  `AGENT_MAX_STEPS` and `AGENT_MAX_TOOL_CALLS` per turn, node crashes contained to a safe reply.
- **Tools are the only path to services.** Each is arg-validated (Pydantic), actor-checked,
  recorded in `tool_executions` with redacted arguments, and refuses identical repeated failures.
  Tools re-check authorisation themselves (verified stay, conversation scoping) rather than
  trusting the flow; tests call them directly to prove it.
- **Confirmation:** consequential actions (reservation, cancellation, handoff offer) become a
  `PendingAction` with fixed parameters and a TTL; only an explicit short YES executes it, using
  an idempotency key derived from the pending action. Emergencies and "human" always override.
- **Identity:** web guests get an anonymous guest-scoped JWT; stay-specific actions require
  confirmation code + surname (uniform failure, 5 attempts per conversation). Channel adapters
  will map external ids through `guest_channel_identities`.
- **Exactly-once messages:** `(conversation_id, external_message_id)` is unique and the
  conversation row is locked per turn; in-flight duplicates wait for and return the first reply.
- **Privacy:** card numbers are masked before storage; tool args/traces are PII-redacted;
  conversations carry `retention_expires_at` and `purge_expired` deletes them.
- **LLM:** optional and non-authoritative (see README). Circuit breaker on transient failures;
  refusals and invalid output fall back to deterministic handling.

### Known limits of this slice

- Wording is templated, so replies are accurate but not conversational. A composer could wrap
  them later if it keeps the verifier and citations.
- Natural-language dates need an LLM; without one, ISO dates and common "3 Nov" forms work.
- Staff are notified in the dashboard (in-app alerts); there is no email/SMS/push to staff.
- The default rate limiter is in-process; set `RATE_LIMIT_BACKEND=redis` for multiple replicas.
- Concurrency control relies on per-turn conversation locking plus idempotency keys, not one
  long transaction, because services commit internally.
- Complaint/emergency detection is keyword-based and will miss unusual phrasing.
- The rule-based intent classifier has only been checked on the sentences in the tests.

## Decisions (channels, worker, web: Phase 3)

- **Channels:** Twilio WhatsApp (HMAC-SHA1 signature over the public URL) and Telegram (secret
  token header). Every inbound delivery is recorded in `channel_events` (unique per provider
  event id) so redelivery cannot repeat actions; the conversation layer is channel-agnostic.
- **Outbound:** replies are queued as `outbound_messages`; the worker sends with exponential
  backoff and gives up after `OUTBOUND_MAX_ATTEMPTS` (status `dead` for retryable errors, `failed` for permanent ones), claiming rows with `FOR UPDATE SKIP LOCKED`.
- **Worker:** no broker. The database is the queue (`apps/worker`): outbound delivery, reaping
  stuck ingestions/channel events, conversation retention purge.
- **Web:** Next.js App Router. The browser never holds the staff JWT: a same-origin proxy
  (`/api/bff`) keeps it in an httpOnly cookie, requires a custom CSRF header, only forwards an
  allow-list of API paths, and refreshes the token as it nears expiry. Guest chat talks to a
  separate proxy with an anonymous guest token held in memory.

## Decisions (production hardening: Phase 4)

- **Observability:** `/metrics` (Prometheus, bearer-token protected, required in production), JSON
  logs with request ids and PII redaction, OpenTelemetry spans that are a no-op unless
  `OTEL_EXPORTER_OTLP_ENDPOINT` is set. Tool arguments in traces are redacted before storage.
- **Session revocation:** JWTs carry `token_version`; disabling a user, changing their role, or any
  password change bumps it, ending every session immediately.
- **Abuse controls:** request size limits (streamed bodies included), security headers, login
  throttling per IP+email, trusted-proxy aware client IP (`TRUST_PROXY_HEADERS`), password policy,
  Redis sliding-window limiter (fails open and counts errors if Redis is down).
- **Cost controls:** per-hotel daily LLM token budget (`LLM_DAILY_TOKEN_BUDGET_PER_HOTEL`), a
  short-TTL knowledge answer cache (keyed on the hotel's active indexed documents and their last
  update time, so uploading, deactivating or re-indexing a document invalidates it), cost estimates from
  configured prices. With `LLM_PROVIDER=none` the cost is zero.
- **Retrieval gating:** coverage is weighted so generic words ("hours", "price", "room") count for
  a quarter of a distinctive term; this stopped "sauna hours" being answered from the spa's hours
  (found by a browser test) at some cost in recall on paraphrases. A hybrid with a real embedder
  and/or reranker is the proper fix and has not been evaluated.
- **Evaluation:** scenario-based agent eval with fault injection and mutation-checked safety
  scenarios; deterministic RAG eval; optional RAGAS adapter (never run). `evals/README.md`.

### Known limits after Phase 4

- Agent behaviour with a real LLM is unmeasured.
- Complaint/emergency detection is keyword-based; the router was tuned on the scenarios in
  `evals/agent`, so those scores overstate generalisation.
- A pending YES/NO for a booking or cancellation is strict; an unanswered *handoff offer* is
  superseded by a new request.

## Roadmap

1-4 done (see above). 5: Docker/CI finalisation (run the compose stack and the GitHub workflow for
real), deployment runbooks.
