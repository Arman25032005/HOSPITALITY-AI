# Product KPIs and AI metrics

All figures are computed from stored records for one hotel over a window of N days
(`GET /api/v1/hotels/{id}/ai/kpis?days=N`, managers and administrators only). Staff-assistant
(analytics) chats are excluded. Nothing is estimated except cost, which uses the configured
token prices (`LLM_INPUT_PRICE_PER_MTOK`, `LLM_OUTPUT_PRICE_PER_MTOK`; set them to your model's real
pricing).

**Turn** = one guest message processed by the agent (one `agent_executions` row).
**Conversation in scope** = a guest conversation with at least one turn in the window.

| KPI | Formula |
|---|---|
| Responded conversations | count of conversations in scope (every one received a reply) |
| Resolved conversations | in-scope conversations with **no** `escalated`/`handoff` turn whose **last** turn outcome is `answered` or `action_done` |
| Handed-off conversations | in-scope conversations with at least one `escalated` or `handoff` turn |
| Unresolved conversations | the rest: last outcome is `abstained`, `fallback`, `error`, `needs_info` or `action_pending` |
| **Resolution rate** | resolved / responded |
| Handoff rate | handed-off / responded |
| Turn escalation rate | turns with `escalated`/`handoff` / all turns |
| Knowledge abstain rate | `abstained` turns / turns with intent `knowledge` |
| Reservation completion rate | conversations with an `action_done` turn of intent `reservation` / conversations with any `reservation` turn |
| Agent tickets created | `ticket.created` audit events with actor `agent` |
| Agent ticket resolution rate | of those tickets, share now `resolved` or `closed` |
| Tool success rate | successful tool executions / all tool executions (per-tool table at `/ai/tools`) |
| Retrieval latency | mean `latency_ms` of `search_knowledge` tool calls |
| Turn latency p50 / p95 | percentiles of `agent_executions.latency_ms` |
| Model latency | mean `llm_ms` over turns that called a model |
| Estimated cost | (tokens_in x input price + tokens_out x output price) / 1,000,000 |
| Cost per conversation | estimated cost / conversations in scope |
| Feedback | mean rating of guest feedback in the window; low-rated = rating <= 2 (listed at `/ai/flagged`) |

## "Responded" is not "resolved"

A guest who asks a question we cannot answer still gets a reply (an honest abstention), so
*responded* is always 100%. *Resolved* requires the last turn to have actually answered the guest or
completed an action, and no escalation along the way. Report resolution rate, not response rate.

## Known limits

- Classification uses the conversation as it stands when queried: an open conversation whose last
  turn was an answer counts as resolved until the guest writes again.
- "Resolved" is an outcome label, not a guest-confirmed satisfaction signal; use the feedback
  metrics alongside it.
- Percentiles are computed in the application; for very high volume move them to SQL
  `percentile_cont` or the Prometheus histograms (`agent_turn_duration_seconds`).
- Retrieval *quality* (as opposed to latency and abstention) is measured offline: see
  `apps/api/evals/README.md`.

## Prometheus / OpenTelemetry

`/metrics` (bearer token `METRICS_TOKEN`; required in production) exposes request, agent, tool, LLM,
knowledge, ticket, reservation, delivery, webhook and rate-limit counters with low-cardinality
labels only. Set `OTEL_EXPORTER_OTLP_ENDPOINT` to export traces (HTTP spans plus `agent.turn` and
`tool.*` spans; no message text or personal data). Logs are JSON with a request id and are
redacted for emails, phone numbers and card numbers.
