# HospitalityOS AI

Multi-tenant, agentic AI hospitality platform (guest concierge + hotel operations copilot).

**Status: Phase 2 complete (backend); Phases 3-5 not started.** Working and tested:
tenant-aware data model and migrations, JWT auth with roles, transactional reservations
(row-locked inventory, idempotency, check-in), service tickets with workflow and RBAC, knowledge
ingestion + hybrid retrieval + grounded answers, persistent conversations, and a LangGraph agent
that runs the seven workflows below through guest chat (`/api/v1/chat/*`) and staff APIs.

**Not implemented yet:** WhatsApp/Telegram adapters (conversation layer is channel-ready),
dashboard and guest-chat screens (the web app is only a login shell), push notifications to staff,
observability dashboards, RAGAS evaluation, Docker/CI verification. Pinecone, sentence-transformers,
the Anthropic provider and the OpenAI-compatible provider are tested against fakes/mock transports
only, never against live services. See `docs/ARCHITECTURE.md`.

## What the agent can do

| # | Workflow | Behaviour |
|---|---|---|
| 1 | Policy question | Retrieves from the hotel's knowledge base, quotes it with citations, abstains when unsupported |
| 2 | Reservation | Collects dates/party, shows live options and prices, **asks YES/NO**, re-validates inventory under lock, returns the stored confirmation code |
| 3 | Maintenance / housekeeping | Requires a verified current stay (code + surname), creates and routes a real ticket, reports stored status |
| 4 | Late checkout | Shows the policy, files an approval request; staff decide; guest is told only the recorded decision |
| 5 | Complaint | Severity rules decide escalation; records reason; promises nothing; emergencies get the hotel's configured procedure and immediate escalation |
| 6 | Manager analytics | Staff-only; fixed metric queries over real records (no generated SQL) |
| 7 | Human handoff | Creates a handoff ticket with the conversation context, pauses the bot, staff can reply and release |

Guest-visible wording is templated or quoted from the knowledge base. The LLM (optional,
`LLM_PROVIDER`) only classifies ambiguous intents, extracts dates and summarises, as
schema-validated structured output. Every confirmation code / ticket number in a reply must have
been read back from the database in the same turn, or the reply is replaced.

Try it (server running, DB seeded): `POST /api/v1/chat/sessions {"org_slug":"demo-group",
"hotel_slug":"demo-grand"}` returns a guest token; then `POST /api/v1/chat/messages
{"text": "...", "client_message_id": "<unique>"}`. Resending the same `client_message_id` returns the
original reply without repeating any action.

## Local setup

```bash
cp .env.example .env
docker compose up -d postgres redis

cd apps/api
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
alembic upgrade head          # create schema
python -m scripts.seed        # demo data incl. demo knowledge base (idempotent)
uvicorn app.main:app --reload # http://localhost:8000/docs

cd ../web && npm install && npm run dev   # http://localhost:3000
```

Demo staff logins (demo data, local only): `manager@demo.hotel`, `admin@demo.hotel`,
`reception@demo.hotel` — password `demo-password-123`.

## Tests

```bash
cd apps/api && pytest -q     # needs Postgres; WARNING: drops/recreates tables in DATABASE_URL
```

Use a dedicated test database (`DATABASE_URL=.../hospitality_test`), never a shared one.

## Knowledge base / RAG

Upload (admin/manager): `POST /api/v1/hotels/{hotel_id}/knowledge/documents` (multipart:
`file`, `title`, `doc_type`, optional `doc_key`, `is_confidential`, `effective_date`). PDF, DOCX,
TXT, MD, CSV, HTML; max 5 MB. Re-uploading with the same `doc_key` creates a new version and
deactivates the old one atomically; a failed ingestion leaves the previous version live.
`/search` and `/ask` (staff preview; `audience: guest|staff`) show what the concierge would retrieve
or answer. Dev/test use the local vector store and the `hashing` embedder; **production refuses to
start with either** (see `.env.example`). After changing the embedder run
`python -m scripts.reindex_knowledge`. Evaluation: `evals/README.md`.

## Layout

`apps/api` FastAPI · `apps/web` Next.js · `apps/worker` (planned) · `packages/ai`,
`packages/integrations`, `packages/shared` (planned) · `infra` Docker · `docs`.

## Known Phase 1 limitations

- Web login keeps the JWT in `sessionStorage`; move to httpOnly cookies before production.
- No rate limiting or refresh tokens yet.
- Seed passwords are demo-only.
