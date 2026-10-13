# HospitalityOS AI

Multi-tenant, agentic AI hospitality platform (guest concierge + hotel operations copilot).

**Status: early build (Phases 1 and 2 partly done).** Working and tested: tenant-aware data model
and migrations, JWT auth with roles, transactional reservations (row-locked inventory,
idempotency), service tickets with workflow and RBAC, knowledge ingestion + hybrid retrieval +
grounded extractive answers with abstention (`/api/v1/hotels/{id}/knowledge/*`), demo seed, retrieval
eval harness. **Not yet implemented:** LLM provider layer, conversations, LangGraph agents,
WhatsApp/Telegram, dashboard screens (the web app is only a login shell), observability,
RAGAS evaluation. Pinecone and sentence-transformers adapters exist but have not been run against
live services. See `docs/ARCHITECTURE.md`.

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
