# Evaluations

## RAG retrieval/answer eval (`dataset_v1.json`)

```bash
cd apps/api && python -m scripts.seed && python -m scripts.eval_rag --out evals/reports/<name>.json
```

Measures, with no LLM involved: hit@1 / hit@k / MRR for the expected document section, whether the
answer cites the expected section and contains the expected facts, false-abstain rate on
answerable questions, and correct-abstain / unsupported-answer rate on unanswerable ones
(including questions only a confidential staff document could answer, asked as a guest).

Results are deterministic for a given dataset, embedder and seed (verified identical on two
separate databases). Reports record the embedder, thresholds and git SHA.

### Current result (`reports/rag_hybrid-fts_hashing-v2.json`)

26 answerable + 8 unanswerable questions, `hashing-v2` embedder, hybrid (vector + Postgres FTS)
retrieval, top_k 5: hit@1 0.846, hit@5 0.962, MRR 0.897, end-to-end correct 0.885, false-abstain
0.115, unsupported-answer rate 0.0.

### How far to trust this

- The dataset was written by the same author as the demo corpus and is small (34 items). It is a
  regression guard, not a held-out benchmark.
- The tokenizer (stemming, a handful of synonyms) was adjusted after inspecting failures on
  questions that overlap this dataset, so the numbers are optimistic for unseen phrasing.
- `hashing-v2` is a lexical embedder. The three remaining misses (q14, q18, q20) are paraphrases
  with no shared words ("open at night" vs "24 hours"). Re-run this eval with a real embedding model
  (`EMBEDDING_PROVIDER=sentence-transformers`, then `python -m scripts.reindex_knowledge`) before
  drawing conclusions about production quality; not yet done.
- RAGAS metrics (faithfulness, answer relevancy, context precision/recall) need an LLM judge and are
  planned for the evaluation phase; none have been measured.
