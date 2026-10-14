# Evaluations

## RAG retrieval/answer eval (`dataset_v1.json`)

```bash
cd apps/api && python -m scripts.seed && python -m scripts.eval_rag --out evals/reports/<name>.json
```

Measures, with no LLM involved: hit@1 / hit@k / precision@k / MRR for the expected document section, whether the
answer cites the expected section and contains the expected facts, false-abstain rate on
answerable questions, and correct-abstain / unsupported-answer rate on unanswerable ones
(including questions only a confidential staff document could answer, asked as a guest).

Results are deterministic for a given dataset, embedder and seed (verified identical on two
separate databases). Reports record the embedder, thresholds and git SHA.

### Current result (`reports/rag_hybrid-fts_hashing-v2.json`)

(See the update below for the near-miss set; this paragraph describes the original 34 items.)

26 answerable + 8 unanswerable questions, `hashing-v2` embedder, hybrid (vector + Postgres FTS)
retrieval, top_k 5: hit@1 0.846, hit@5 0.962, MRR 0.897, end-to-end correct 0.885, false-abstain
0.115, unsupported-answer rate 0.0.

### Near-miss questions and what they exposed

A browser test of the new knowledge screen showed "What are the sauna hours?" being answered from
the spa's opening hours: only the generic word "hours" matched, and 1 of 2 terms cleared the 0.5
coverage gate. Seven near-miss unanswerable questions (u09-u15, flagged `near_miss`) were added
and measured BEFORE changing anything: 3 of 7 were answered (sauna, steam room, business center).
Making generic words ("hours", "price", "room", ...) count for a quarter of a distinctive term in
the coverage score brought that to 1 of 7 (business center, matched via "center" in "Fitness
Center") and improved the answerable set (end-to-end correct 0.885 → 0.923; hit@1/hit@5/MRR
unchanged). Raising `RAG_MIN_COVERAGE` to 0.6 removed the last one but raised false-abstain from
0.077 to 0.346, so it was not adopted. Current: `unsupported_answer_rate` 0.0 on the 8 clear
unanswerables; `near_miss_unsupported_rate` 0.143 (1 of 7), gated at ≤ 0.15 in
`aggregate_reports`, which is a tolerance chosen to match today's result, not a target.
A real embedding model or reranker is the proper fix and has not been tried.

### How far to trust this

- The dataset was written by the same author as the demo corpus and is small (34 items). It is a
  regression guard, not a held-out benchmark.
- The tokenizer (stemming, a handful of synonyms) was adjusted after inspecting failures on
  questions that overlap this dataset, so the numbers are optimistic for unseen phrasing.
- `hashing-v2` is a lexical embedder. The three remaining misses (q14, q18, q20) are paraphrases
  with no shared words ("open at night" vs "24 hours"). Re-run this eval with a real embedding model
  (`EMBEDDING_PROVIDER=sentence-transformers`, then `python -m scripts.reindex_knowledge`) before
  drawing conclusions about production quality; not yet done.
- precision@5 (0.192) is bounded by 0.2 here because each question has exactly one expected
  section among five retrieved passages; read it as a regression signal, not a quality score.
- RAGAS (faithfulness, context precision/recall) needs an LLM judge. The adapter
  (`scripts/eval_ragas.py`, hand-written `references_v1.json`) is implemented and unit-tested for its
  dataset building, but it has NEVER been run against a judge model: without `ragas` and a
  configured `LLM_PROVIDER` it exits 3 and prints that no scores were produced. No RAGAS number
  exists for this project.

## Agent behaviour eval (`agent/scenarios_v1.json`, 68 scenarios)

```bash
createdb hospitality_eval && DATABASE_URL=…/hospitality_eval alembic upgrade head
DATABASE_URL=…/hospitality_eval python -m scripts.eval_agent --out evals/reports/<name>.json
```

DESTRUCTIVE: each scenario truncates all tenant data and reseeds, so the runner refuses any
database not named `*_eval` / `*_test` (override: `--allow-destructive`). Scenarios run through the
real HTTP API in-process; faults (LLM down/garbage/refusal, a tool raising, zero tool budget) are
injected with the same dependency overrides the test-suite uses. The LLM is scripted or absent.

| Metric | Definition |
|---|---|
| `intent_routing_accuracy` | steps with an expected intent whose recorded `AgentExecution.intent` matches |
| `tool_selection_accuracy` | steps with expected tools whose set of called tools equals the expected set |
| `tool_argument_validity` | tool calls not rejected as "invalid tool arguments" |
| `workflow_completion_rate` | `workflow` scenarios passing every step + database-state check |
| `policy_compliance_rate` | `policy` scenarios passing (verification gate, no invented promises, validation) |
| `escalation_precision` / `_recall` | scenarios labelled should/should-not escalate vs. any step ending `escalated`/`handoff` |
| `confirmation_handling_rate` | `confirmation` scenarios (yes/no/duplicate/expired/stale inventory) |
| `failure_recovery_rate` | `recovery` scenarios (injected faults) |
| `injection_resistance_rate` | `injection` scenarios (prompt injection, poisoned KB doc, SQL/XSS text, attempt limits) |

Exit code 2 when a `policy`/`injection`/`confirmation` scenario fails or an expected escalation is
missed; `--strict` makes any failure non-zero.

### Current result (`reports/agent_scenarios-v1_rules-scripted.json`)

68/68 scenarios pass; every metric is 1.0 (counts are in the report). Treat that as a regression
guard, not evidence of broad quality:

- The scenarios were authored by the developers against the rule-based router and then run; the
  first run exposed 4 failures that were resolved as follows: two were genuine behaviour gaps fixed
  in code (emergency turns recorded no intent; an unanswered "connect you with staff?" offer made
  every later message get "I'm waiting for your answer", now superseded by a new request), and two
  were mistakes in the dataset/harness (suite sell-out dates; an expectation on verification
  attempts that the chosen message could never reach). The dataset was corrected for those, so the
  100% is partly by construction.
- Mutation checks confirmed the runner fails when a safety behaviour is removed (ignoring pending
  expiry fails `c03`; an invented refund promise fails `p06`).
- No real LLM was involved. LLM-dependent routing (ambiguous intent, natural-language dates) is
  only exercised through scripted/failing models, so no claim is made about real-model accuracy.

## One-shot check run

`apps/api/scripts/run_checks.sh` runs ruff, pytest (junit), both evals and
`scripts.aggregate_reports`, which writes `reports/summary.json` with explicit pass/fail gates (a
missing report fails its gate). Web e2e is separate (`apps/web/e2e/run.sh`) and is included in the
summary when its JSON is present. The GitHub Actions workflow runs the same script, but it has not
yet been executed on GitHub: the pipeline is only verified locally.
