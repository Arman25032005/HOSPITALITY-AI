# Operations notes

Facts below are what the code does today. Anything not listed (backups, alerting rules, TLS,
autoscaling) is **not** implemented here and must be provided by the deployment.

## Health and metrics
- `GET /health` liveness; `GET /ready` checks the database.
- `GET /metrics` Prometheus text. Set `METRICS_TOKEN` and scrape with `Authorization: Bearer …`
  (production refuses to start without it). Series: `http_requests_total`,
  `http_request_duration_seconds`, `agent_turns_total{intent,outcome}`, `agent_turn_duration_seconds`,
  `tool_calls_total{tool,result}`, `llm_calls_total`, `llm_tokens_total`, `knowledge_answers_total`,
  `knowledge_cache_total`, `tickets_created_total`, `reservation_events_total`,
  `outbound_messages_total`, `webhook_events_total`, `rate_limited_total`,
  `rate_limit_backend_errors_total`.
- Useful alerts to define yourself: sustained `outbound_messages_total{result="failed"}`,
  `rate_limit_backend_errors_total` increasing, `tool_calls_total{result="error"}` ratio,
  `agent_turns_total{outcome="error"}`.
- Logs are JSON on stdout with `request_id`; emails, phone numbers and card-like numbers are
  redacted by the logging layer. Product-level KPIs: `docs/KPIS.md`.

## Data and retention
- Conversations carry `retention_expires_at` (`CONVERSATION_RETENTION_DAYS`, default 90); the
  worker's `purge_conversations` task deletes expired conversations and their messages hourly. Agent
  execution records (redacted traces) are kept but unlinked from the conversation. Audit events are
  not purged.
- Card-like numbers are masked before a message is stored.
- Backups, point-in-time recovery and restore drills are the database operator's job; this
  repository does not configure them. Vectors for the local store live in Postgres, so a database
  backup covers them; with Pinecone, vectors can be rebuilt with `python -m scripts.reindex_knowledge`.

## Secrets
- Environment variables, or `SECRETS_PROVIDER=aws` with `SECRETS_ID` (a JSON secret in AWS Secrets
  Manager; tested against a fake client only). Channel secrets never live in the database; the
  dashboard shows only whether they are present.

## Scaling notes
- API replicas need `RATE_LIMIT_BACKEND=redis` for shared limits. Run exactly the worker count you
  need; its queue claims use `SKIP LOCKED`, so several workers are safe.
- Behind a reverse proxy set `TRUST_PROXY_HEADERS=true` only if the proxy overwrites
  `X-Forwarded-For`; otherwise clients can spoof their IP and dodge limits.

## Incident quick reference
- Guests report no replies on WhatsApp/Telegram: check `outbound_messages` rows with status `dead` or `failed`
  and `webhook_events_total` for rejected signatures (wrong `PUBLIC_BASE_URL` breaks Twilio checks).
- Assistant gives "no verified information" too often: Dashboard → AI insights → Unanswered, then
  add or fix documents in Knowledge and use "Test retrieval".
- Sign out a user everywhere: disable them or reset their password in Team (bumps `token_version`).
