# HospitalityOS AI

Multi-tenant, agentic AI hospitality platform (guest concierge + hotel operations copilot).

**Status: Phase 1 (Foundation) only.** Implemented: repo structure, tenant-aware data model,
Alembic migrations, JWT auth with role checks, deterministic demo seed, minimal Next.js shell
(staff login). **Not yet implemented:** RAG, agents, reservations/tickets services, WhatsApp/
Telegram, dashboard views, observability. See `docs/ARCHITECTURE.md` for the roadmap.

## Local setup

```bash
cp .env.example .env
docker compose up -d postgres redis

cd apps/api
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
alembic upgrade head          # create schema
python -m scripts.seed        # demo data (idempotent)
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

## Layout

`apps/api` FastAPI · `apps/web` Next.js · `apps/worker` (planned) · `packages/ai`,
`packages/integrations`, `packages/shared` (planned) · `infra` Docker · `docs`.

## Known Phase 1 limitations

- Web login keeps the JWT in `sessionStorage`; move to httpOnly cookies before production.
- No rate limiting or refresh tokens yet.
- Seed passwords are demo-only.
