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

## Roadmap

2. Knowledge ingestion + RAG, conversations, reservation & ticket services, first agent workflows
3. WhatsApp/Telegram adapters, staff screens, approvals/handoff
4. Tests, observability, evaluation, hardening
5. Docker/CI finalization, deployment runbooks
