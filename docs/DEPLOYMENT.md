# Deployment runbook

Read `docs/OPERATIONS.md` for monitoring/retention and `README.md` for what has and has not been
verified. This page covers getting the system running and keeping it safe. **Verified** means it was
run in this repository's sandbox (Docker 29, Compose v5) and is re-checked by `infra/smoke_stack.sh`
and the CI `stack` job; **Not verified** means it needs a real environment.

## 1. What you deploy

| Artifact | Built from | Notes |
|---|---|---|
| `hospitality-api` image | `infra/docker/api.Dockerfile` | One image, three commands: API (default), worker (`python -m worker.main`), migrations (`alembic upgrade head`). Non-root, no OS packages, ~440 MB. |
| `hospitality-web` image | `infra/docker/web.Dockerfile` | `next build` + `next start`, non-root. Reads `API_INTERNAL_URL` at runtime. |
| PostgreSQL 16 | managed service recommended | Holds data **and** (with the local store) vectors. |
| Redis 7 | managed or container | Rate limiting (`RATE_LIMIT_BACKEND=redis`). The limiter fails open if Redis is down. |
| Reverse proxy / ingress | `infra/nginx.conf.example` | One public origin: `/api/v1/*`, `/health`, `/ready` -> API; everything else -> web; `/metrics` not public. |

Build arguments for the API image: `EXTRAS=pinecone,embeddings,aws` installs the production
integrations (Pinecone client, sentence-transformers, boto3). They import correctly (**Verified**),
but have never run against the live services (**Not verified**). The `embeddings` extra pulls the
default CUDA PyTorch build and makes the image ~9.8 GB; `--build-arg TORCH_INDEX_URL=https://download.pytorch.org/whl/cpu`
should give a CPU-only build (**Not verified**: that index was unreachable from the sandbox). Behind a
TLS-inspecting proxy add `--secret id=ca,src=<ca-bundle.crt>` to `docker build`.

## 2. Production configuration

Production (`ENVIRONMENT=production`) refuses to start unless all of these hold
(`Settings.validate_for_runtime`, **Verified**: the container exits with the first violated rule):

- `APP_SECRET_KEY`: strong random value (not the dev default).
- `METRICS_TOKEN`: set; scrape `/metrics` with `Authorization: Bearer <token>`.
- `VECTOR_STORE_PROVIDER=pinecone` and `PINECONE_API_KEY`; `PINECONE_INDEX`.
- `EMBEDDING_PROVIDER=sentence-transformers` (not `hashing`), then reindex knowledge after any change.
- `TWILIO_WEBHOOK_VALIDATE=true`; `TELEGRAM_WEBHOOK_SECRET` whenever `TELEGRAM_BOT_TOKEN` is set.

Also set: `DATABASE_URL`, `REDIS_URL`, `RATE_LIMIT_BACKEND=redis`, `BACKEND_CORS_ORIGINS` (leave empty
when only the web app calls the API), `PUBLIC_BASE_URL` (the public origin, no trailing slash;
Twilio signatures are verified against `PUBLIC_BASE_URL` + request path), `TRUST_PROXY_HEADERS=true`
only if your proxy overwrites `X-Forwarded-For` (the nginx template does), channel credentials, and
optionally `LLM_PROVIDER`/`LLM_API_KEY`/`LLM_MODEL` plus `LLM_DAILY_TOKEN_BUDGET_PER_HOTEL`.
Inject secrets from your secrets manager or `SECRETS_PROVIDER=aws` (**Not verified** against AWS).
Never put secrets in the image or in git. `.env.example` lists every variable.

## 3. First deployment (order matters)

1. Provision Postgres and Redis; take note of the connection strings.
2. Run migrations once: `docker run --rm --env-file prod.env hospitality-api:<tag> alembic upgrade head`
   (compose does this with the `migrate` service; orchestrators should run it as a one-shot job
   that must succeed before API/worker roll out). **Verified** in compose.
3. Start the API and worker (same image, same env). The worker is required for outbound
   WhatsApp/Telegram delivery and retention purges; run one or more (claims use `SKIP LOCKED`).
4. Start the web container with `API_INTERNAL_URL=http://<api-service>:8000`.
5. **Do not run `scripts.seed` in production** (demo organization with a public demo password).
6. Provision your organization, hotel, rooms and first admin (**Verified** in the container smoke test):

   ```bash
   cat > rooms.json <<'J'
   [{"code":"std","name":"Standard Queen","max_occupancy":2,"base_rate":"150.00","count":6,"floor":1},
    {"code":"dlx","name":"Deluxe King","max_occupancy":3,"base_rate":"220.00","count":4,"floor":2}]
   J
   BOOTSTRAP_ADMIN_PASSWORD='<strong password>' python -m scripts.bootstrap \
     --org-name "Harbor Hotels" --org-slug harbor --hotel-name "Harbor Inn" --hotel-slug harbor-inn \
     --admin-email ops@example.com --admin-name "Ops Admin" --rooms rooms.json \
     --emergency-instructions "If anyone is in danger call 112 now." --front-desk-phone "+1-555-0100"
   ```
   The password is read from the environment or a prompt (never argv). Inventory covers the next
   365 nights; **schedule** `python -m scripts.bootstrap extend-inventory --hotel-slug harbor-inn --days 365`
   (e.g. monthly), otherwise bookings stop at the end of the published window.
7. Sign in as the admin, change the password (My account), add staff (Team), upload the hotel's
   documents (Knowledge) and use "Test retrieval" before relying on them. Mark internal procedures
   Confidential.
8. Channels (Dashboard -> Channels, admin only): enable WhatsApp/Telegram. Twilio: set the sandbox/
   number webhook to `<PUBLIC_BASE_URL>/api/v1/webhooks/twilio/whatsapp`. Telegram: run
   `python -m scripts.telegram_set_webhook <integration_id>`. **Not verified** against live providers;
   do a real test message from each channel before announcing it.

## 4. Upgrades and rollback

- Order: back up -> run the new image's migration job -> roll out API/worker -> roll out web.
  Migrations are additive in 0001-0005; check each new migration for destructive changes first.
- Roll back application code by redeploying the previous image tag. Roll back the schema only with
  `alembic downgrade <rev>` if you have confirmed the migration is reversible (0005 round-trips, **Verified**
  in development); otherwise restore the pre-upgrade backup. Rolling the schema back can discard data
  written by the newer version.
- Never edit applied migrations; add new ones. `alembic check` should report no drift.

## 5. Backup and restore

Backups of the database are the operator's responsibility; this is the minimal logical procedure
(**Verified**: `smoke_stack.sh` restores a dump into a scratch database and compares row counts):

```bash
pg_dump -U <user> -Fc <db> > hospitality-$(date +%F).dump              # backup
createdb -U <user> restore_check
pg_restore -U <user> -d restore_check --no-owner hospitality-<date>.dump # restore (test it regularly)
```
Use your platform's PITR/snapshots for real production recovery objectives; **drill the restore**.
Uploaded document text and chunks live in Postgres; with Pinecone, vectors are rebuilt with
`python -m scripts.reindex_knowledge`.

## 6. Reverse proxy and TLS

Use `infra/nginx.conf.example` as the routing template (**Verified** for routing against the compose
stack: web, API, webhook path and hidden `/metrics`). TLS termination, HSTS and edge rate limiting
are yours to add. The API sends HSTS itself in production; the web app sets its security headers and
CSP (which still allows inline scripts, a known limitation).

## 7. Go-live checklist

- [ ] Production env passes startup validation; `GET /ready` is 200; `/metrics` scraped with the token.
- [ ] No demo data or demo credentials (`demo-password-123`) anywhere; first admin's password changed.
- [ ] Emergency instructions and front-desk phone set for each hotel.
- [ ] Knowledge uploaded; "Test retrieval" gives correct, cited answers; unanswerable questions abstain.
- [ ] Real embedding provider + Pinecone configured and reindexed; retrieval evaluated with it
      (`scripts/eval_rag.py`): the hashing-embedder numbers in `evals/README.md` do not apply.
- [ ] A real message round trip on every enabled channel; worker running; outbound queue empty.
- [ ] Backups configured and a restore drilled; alerts defined (see OPERATIONS.md).
- [ ] If an LLM is enabled: provider key, budget cap set, behaviour reviewed (no real-model evaluation exists yet).
- [ ] Inventory extension scheduled.
