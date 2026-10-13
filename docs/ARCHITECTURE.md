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

## Roadmap

2. Knowledge ingestion + RAG, conversations, reservation & ticket services, first agent workflows
3. WhatsApp/Telegram adapters, staff screens, approvals/handoff
4. Tests, observability, evaluation, hardening
5. Docker/CI finalization, deployment runbooks
