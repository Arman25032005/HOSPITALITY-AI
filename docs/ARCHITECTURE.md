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
- No push notifications to staff yet (tickets/approvals appear in APIs; dashboards come in Phase 3).
- The rate limiter is in-process; use Redis before running multiple API replicas.
- Concurrency control relies on per-turn conversation locking plus idempotency keys, not one
  long transaction, because services commit internally.
- Complaint/emergency detection is keyword-based and will miss unusual phrasing.
- The rule-based intent classifier has only been checked on the sentences in the tests.

## Roadmap

2. Knowledge ingestion + RAG, conversations, reservation & ticket services, first agent workflows
3. WhatsApp/Telegram adapters, staff screens, approvals/handoff
4. Tests, observability, evaluation, hardening
5. Docker/CI finalization, deployment runbooks
