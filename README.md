# HospitalityOS AI

Multi-tenant, agentic AI hospitality platform (guest concierge + hotel operations copilot).

**Status: Phases 1-4 complete and verified locally; Phase 5 (deployment finalisation) not started.**
Working and tested: tenant-aware data model and migrations (0001-0005), JWT auth with roles and
server-side session revocation, transactional reservations, service tickets, knowledge ingestion +
hybrid retrieval + grounded answers, persistent conversations, a LangGraph agent running the seven
workflows below, WhatsApp (Twilio) and Telegram webhooks with signature verification and a
retrying outbound queue, a DB-backed worker, a staff dashboard and guest chat web app (httpOnly
cookie sessions behind a same-origin proxy), knowledge administration, AI insights, team
management, Prometheus metrics, JSON logs with PII redaction, rate limiting, and agent/RAG
evaluation harnesses. See `docs/ARCHITECTURE.md`, `docs/OPERATIONS.md`, `docs/KPIS.md`, `evals/README.md`.

**Never verified against live services:** Twilio and Telegram delivery, Pinecone,
sentence-transformers, the Anthropic and OpenAI-compatible providers (all tested against fakes or
mock transports), AWS Secrets Manager, OTLP export, RAGAS (needs an LLM judge), and the GitHub
Actions workflow (only its script, `apps/api/scripts/run_checks.sh`, has been run, locally).
Default retrieval uses a lexical hashing embedder suited to dev/test only; production refuses it.
Agent-eval scores come from developer-authored scenarios against the rule-based router with no real
LLM, so they are a regression guard, not a quality benchmark.

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

## Tests and evaluation

```bash
cd apps/api && pytest -q     # needs Postgres; WARNING: drops/recreates tables in DATABASE_URL
```

Use a dedicated test database (`DATABASE_URL=.../hospitality_test`), never a shared one.
`apps/api/scripts/run_checks.sh` runs lint, tests, the agent and RAG evaluations and writes
`reports/summary.json` with pass/fail gates (needs `TEST_DATABASE_URL` and `EVAL_DATABASE_URL`).
Browser tests (Playwright + axe, ~25 tests): `apps/web/e2e/run.sh` (builds the web app first:
`cd apps/web && npm run build`; uses an isolated `hospitality_e2e` database).

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

`apps/api` FastAPI · `apps/web` Next.js · `apps/worker` DB-backed worker · `infra` Docker ·
`docs`. (The `packages/*` directories from the original plan were not needed.)

## Known limitations

- Seed passwords are demo-only; production needs real secrets (`APP_SECRET_KEY`, `METRICS_TOKEN`,
  Pinecone key, a real embedding provider) or the API refuses to start.
- The web app's CSP still allows inline scripts (Next.js hydration); nonce-based CSP is not done.
- Retrieval is lexical by default; near-miss questions (a generic word matches an unrelated
  section) are only partly caught (see `evals/README.md`).
- No Docker/Kubernetes deployment has been exercised end to end; Phase 5 covers that.
